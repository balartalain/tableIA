import json
import logging
import re

from django.conf import settings
from django.contrib.auth import get_user_model
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils.timesince import timesince
from django.utils.timezone import now
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from sheets_reports.models import Dashboard, Widget
from sheets_reports.services.sheets import (
    SheetError,
    get_dimension_fields,
    get_field_samples,
    get_sheet_dataframe,
    get_sheet_schema,
    invalidate_sheet_cache,
)
from sheets_reports.widgets import WIDGETS as WIDGET_REGISTRY
from sheets_reports.utils.validation import SpecValidationError
from sheets_reports.services.widget_service import WidgetService

logger = logging.getLogger(__name__)


def _widget_manifest() -> dict:
    """Lo que la UI necesita de cada tipo: etiqueta, controles de estilo y capacidades de datos."""
    return {
        key: {
            "label": widget_cls.label,
            "style_schema": widget_cls.style_schema,
            "style_defaults": widget_cls.style_defaults(),
            "capabilities": widget_cls.capabilities,
            "max_per_dashboard": widget_cls.max_per_dashboard,
        }
        for key, widget_cls in WIDGET_REGISTRY.items()
    }


def board_editor(request, dashboard_id):
    dashboard = get_object_or_404(Dashboard, id=dashboard_id)
    return render(request, "board_editor.html", {
        "dashboard": dashboard, "refresh_minutes": settings.WIDGET_REFRESH_MINUTES,
        "widget_manifest": _widget_manifest(),
    })


def board_view(request, dashboard_id):
    dashboard = get_object_or_404(Dashboard, id=dashboard_id)
    return render(request, "board_view.html", {
        "dashboard": dashboard, "refresh_minutes": settings.WIDGET_REFRESH_MINUTES,
    })


@csrf_exempt
@require_http_methods(["GET", "POST"])
def dashboard_list(request):
    """GET: tableros del usuario actual. POST {nombre, sheet_url}: crea uno."""
    if request.method == "GET":
        user = _get_user(request)
        if user is None:
            return _error("No hay usuarios creados. Crea uno con: python manage.py createsuperuser",
                          status=401)
        return JsonResponse([_serialize_dashboard(d) for d in Dashboard.objects.filter(owner=user)],
                            safe=False)
    try:
        data = _json_body(request)
    except ValueError as e:
        return _error(str(e))
    nombre = str(data.get("nombre") or "").strip()
    sheet_url = str(data.get("sheet_url") or "").strip()
    if not nombre:
        return _error("El nombre es obligatorio")
    if not sheet_url:
        return _error("La URL de la hoja es obligatoria")
    user = _get_user(request)
    if user is None:
        return _error("No hay usuarios creados. Crea uno con: python manage.py createsuperuser",
                      status=401)
    dashboard = Dashboard.objects.create(owner=user, nombre=nombre, sheet_url=sheet_url,
                                         sheet_gid=str(data.get("sheet_gid") or "0"))
    return JsonResponse(_serialize_dashboard(dashboard), status=201)


@csrf_exempt
@require_http_methods(["GET", "PUT", "DELETE"])
def dashboard_detail(request, dashboard_id):
    """GET / PUT {nombre?, sheet_url?} / DELETE de un tablero del usuario."""
    dashboard = _owned_dashboard(request, dashboard_id)
    if not dashboard:
        return _error("Dashboard no encontrado", status=404)
    if request.method == "GET":
        return JsonResponse(_serialize_dashboard(dashboard))
    if request.method == "DELETE":
        dashboard.delete()
        return JsonResponse({"deleted": True})
    try:
        data = _json_body(request)
    except ValueError as e:
        return _error(str(e))
    if "nombre" in data:
        nombre = str(data.get("nombre") or "").strip()
        if not nombre:
            return _error("El nombre es obligatorio")
        dashboard.nombre = nombre
    if "sheet_url" in data:
        sheet_url = str(data.get("sheet_url") or "").strip()
        if not sheet_url:
            return _error("La URL de la hoja es obligatoria")
        if sheet_url != dashboard.sheet_url:
            # El cache viejo es el de la hoja ANTERIOR: se invalida antes de cambiar la URL.
            previous_sheet_id = dashboard.sheet_id
            dashboard.sheet_url = sheet_url
            invalidate_sheet_cache(previous_sheet_id, dashboard.sheet_gid)
    if "sheet_gid" in data:
        dashboard.sheet_gid = str(data.get("sheet_gid") or "0")
    dashboard.save()
    return JsonResponse(_serialize_dashboard(dashboard))


@require_http_methods(["POST"])
def dashboard_duplicate(request, dashboard_id):
    """Duplica un tablero (incluyendo widgets)."""
    dashboard = _owned_dashboard(request, dashboard_id)
    if not dashboard:
        return _error("Dashboard no encontrado", status=404)
    new_dashboard = Dashboard.objects.create(
        owner=_get_user(request),
        nombre=f"{dashboard.nombre} (copia)",
        sheet_url=dashboard.sheet_url,
        sheet_gid=dashboard.sheet_gid,
    )
    for widget in dashboard.widgets.all():
        Widget.objects.create(
            dashboard=new_dashboard,
            type=widget.type,
            title=widget.title,
            position=widget.position,
            fields=widget.fields,
            style=widget.style,
            source_prompt=widget.source_prompt,
        )
    return JsonResponse(_serialize_dashboard(new_dashboard), status=201)


def home(request):
    """Página de inicio - lista de tableros."""
    user = _get_user(request)
    dashboards = Dashboard.objects.filter(owner=user)
    return render(request, "home.html", {"dashboards": dashboards})


def _get_user(request):
    if request.user.is_authenticated:
        return request.user
    User = get_user_model()
    return User.objects.filter(is_superuser=True).first() or User.objects.first()


def _json_body(request) -> dict:
    try:
        data = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        raise ValueError("JSON inválido")
    if not isinstance(data, dict):
        raise ValueError("Se esperaba un objeto JSON")
    return data


def _error(message, status=400, **extra):
    return JsonResponse({"error": message, **extra}, status=status)


def _owned_dashboard(request, dashboard_id):
    return Dashboard.objects.filter(id=dashboard_id, owner=_get_user(request)).first()


def _owned_widget(request, widget_id):
    return Widget.objects.select_related("dashboard").filter(
        id=widget_id, dashboard__owner=_get_user(request)
    ).first()


def _gid_from_url(url: str) -> str | None:
    m = re.search(r"[#&?]gid=(\d+)", url or "")
    return m.group(1) if m else None


def _load_sheet(dashboard):
    return get_sheet_dataframe(dashboard.sheet_id, dashboard.sheet_gid)


def _serialize_dashboard(dashboard):
    return {
        "id": dashboard.id,
        "nombre": dashboard.nombre,
        "sheet_url": dashboard.sheet_url,
        "sheet_gid": dashboard.sheet_gid,
        "cardCount": dashboard.widgets.count(),
        "created_at": dashboard.created_at.isoformat(),
        "updated": timesince(dashboard.created_at, now()),
    }


def _serialize_widget(widget):
    return {
        "id": widget.id,
        "type": widget.type,
        "title": widget.title,
        "position": widget.position,
        "fields": widget.fields,
        "style": widget.style,
        "source_prompt": widget.source_prompt,
    }


@require_http_methods(["GET"])
def dashboard_schema(request, dashboard_id):
    dashboard = get_object_or_404(Dashboard, id=dashboard_id)
    if request.GET.get("refresh"):
        invalidate_sheet_cache(dashboard.sheet_id, dashboard.sheet_gid)
    try:
        df = _load_sheet(dashboard)
    except SheetError as e:
        return _error(str(e), status=502)
    schema = {**get_sheet_schema(df), "dimension_fields": get_dimension_fields(df)}
    return JsonResponse({
        **schema, "sample_values": get_field_samples(df),
        "widget_manifest": _widget_manifest(),
    })


@require_http_methods(["GET"])
def dashboard_render(request, dashboard_id):
    """
    Datos listos para dibujar todos los widgets del tablero. NUNCA llama a la IA: es puro
    cálculo sobre la hoja cacheada, para que el refresco periódico del frontend sea barato.
    Es de solo lectura y sirve también a la vista compartida (board_view).
    """
    dashboard = get_object_or_404(Dashboard, id=dashboard_id)
    try:
        df = _load_sheet(dashboard)
    except SheetError as e:
        return _error(str(e), status=502)

    # Parse board filters
    try:
        service = WidgetService(dashboard, df)
        filters, filter_errors = service.parse_board_filters(request.GET.get("filters"))
    except ValueError as e:
        return _error(str(e))

    widgets_data = []
    for widget in dashboard.widgets.all():
        rendered = service.render(widget, filters)
        widgets_data.append({
            "id": widget.id,
            "type": widget.type,
            "title": widget.title,
            "position": widget.position,
            "fields": widget.fields,
            "style": widget.style,
            "source_prompt": widget.source_prompt,
            "data": rendered.get("data"),
            "error": rendered.get("error"),
        })

    return JsonResponse({
        "dashboard": _serialize_dashboard(dashboard),
        "widgets": widgets_data,
        "filter_errors": filter_errors,
    })


MAX_ASSISTANT_HISTORY = 20


def _assistant_current(raw) -> dict | None:
    """El borrador del panel (`{title, fields, style}`) que la IA debe ajustar, o None."""
    if not isinstance(raw, dict):
        return None
    current = {
        "title": str(raw.get("title") or "").strip(),
        "fields": raw.get("fields") if isinstance(raw.get("fields"), dict) else {},
        "style": raw.get("style") if isinstance(raw.get("style"), dict) else {},
    }
    return current if current["title"] or current["fields"] or current["style"] else None


def _assistant_history(raw) -> list[dict]:
    """Los mensajes previos del hilo: solo los que tienen la forma esperada, los más recientes."""
    if not isinstance(raw, list):
        return []
    messages = []
    for item in raw[-MAX_ASSISTANT_HISTORY:]:
        if not isinstance(item, dict):
            continue
        if item.get("role") == "user" and isinstance(item.get("text"), str):
            messages.append({"role": "user", "text": item["text"][:2000]})
        elif item.get("role") == "assistant" and isinstance(item.get("proposal"), dict):
            messages.append({"role": "assistant", "proposal": item["proposal"]})
    return messages


@csrf_exempt
@require_http_methods(["POST"])
def table_assistant(request, dashboard_id):
    """
    POST {prompt, widget_type?, current?, history?}
    Chat con la IA del panel de un widget: la IA propone el `WidgetForm` (fields + style)
    validado contra la hoja, ajustando el borrador actual (`current`: `{title, fields, style}`)
    con el contexto de los mensajes previos (`history`). El panel lo muestra como pasos.
    NO crea ni modifica widgets.
    """
    dashboard = _owned_dashboard(request, dashboard_id)
    if not dashboard:
        return _error("Dashboard no encontrado", status=404)
    try:
        data = _json_body(request)
    except ValueError as e:
        return _error(str(e))

    prompt = (data.get("prompt") or "").strip()
    if not prompt:
        return _error("El prompt es obligatorio")

    try:
        df = _load_sheet(dashboard)
    except SheetError as e:
        return _error(str(e), status=502)

    try:
        from sheets_reports.engine.context import SheetContext
        from sheets_reports.services.ai_spec import SpecGenerationError, generate_widget_form
        ctx = SheetContext.from_dataframe(df, dashboard.sheet_gid, samples=get_field_samples(df))
        proposal = generate_widget_form(prompt, data.get("widget_type"), ctx,
                                        current=_assistant_current(data.get("current")),
                                        history=_assistant_history(data.get("history")))
    except SpecGenerationError as e:
        return _error(str(e), status=422)
    except Exception:
        logger.exception("Falló la consulta con IA")
        return _error("La IA no respondió correctamente. Intenta de nuevo.", status=502)

    return JsonResponse({
        "widget_type": proposal["widget_type"],
        "fields": proposal["fields"],
        "style": proposal["style"],
        "title": proposal.get("title", ""),
    })


@csrf_exempt
@require_http_methods(["POST"])
def create_widget(request, dashboard_id):
    """
    POST {type, fields?, style?, title?, position?}
    Crea un widget desde el builder (o desde la IA). NUNCA llama a la IA por su cuenta.
    """
    dashboard = _owned_dashboard(request, dashboard_id)
    if not dashboard:
        return _error("Dashboard no encontrado", status=404)
    try:
        data = _json_body(request)
    except ValueError as e:
        return _error(str(e))

    widget_type = data.get("type")
    if widget_type not in WIDGET_REGISTRY:
        return _error(f"Tipo de widget desconocido: {widget_type}")

    try:
        df = _load_sheet(dashboard)
    except SheetError as e:
        return _error(str(e), status=502)

    service = WidgetService(dashboard, df)
    try:
        widget = service.create(widget_type, data)
    except SpecValidationError as e:
        return _error(str(e), status=422)

    rendered = service.render(widget)
    return JsonResponse({**_serialize_widget(widget), "data": rendered.get("data"),
                         "error": rendered.get("error")}, status=201)


@csrf_exempt
@require_http_methods(["PUT", "DELETE"])
def widget_detail(request, widget_id):
    """
    PUT {position?, title?, fields?, style?}: guarda la configuración y devuelve el widget ya
    calculado. DELETE: borra.
    """
    widget = _owned_widget(request, widget_id)
    if not widget:
        return _error("Widget no encontrado", status=404)

    if request.method == "DELETE":
        widget.delete()
        return JsonResponse({"deleted": True})

    try:
        data = _json_body(request)
    except ValueError as e:
        return _error(str(e))

    try:
        df = _load_sheet(widget.dashboard)
    except SheetError as e:
        return _error(str(e), status=502)

    service = WidgetService(widget.dashboard, df)
    try:
        service.update(widget, data)
    except SpecValidationError as e:
        return _error(str(e), status=422)
    except Exception as e:
        return _error(str(e), status=422)

    rendered = service.render(widget)
    return JsonResponse({**_serialize_widget(widget), "data": rendered.get("data"),
                         "error": rendered.get("error")})
