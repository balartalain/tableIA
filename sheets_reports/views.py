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
from sheets_reports.services import google_drive
from sheets_reports.services.sheets import (
    COLUMN_TYPES,
    SheetError,
    apply_column_config,
    get_dimension_fields,
    get_field_samples,
    get_sheet_dataframe,
    get_sheet_schema,
    infer_column_types,
    invalidate_sheet_cache,
)
from sheets_reports.utils.data import time_fields
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
    Dashboard.objects.filter(id=dashboard.id).update(last_opened_at=now())
    return render(request, "board_editor.html", {
        "dashboard": dashboard, "refresh_minutes": settings.WIDGET_REFRESH_MINUTES,
        "widget_manifest": _widget_manifest(),
    })


def board_new(request):
    """Editor sin tablero: el bottom sheet abre el selector de fuente y, al confirmar, crea el
    tablero y redirige a su editor."""
    return render(request, "board_editor.html", {
        "dashboard": None, "refresh_minutes": 0, "widget_manifest": _widget_manifest(),
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
    sheet_id = str(data.get("sheet_id") or "").strip()
    sheet_gid = str(data.get("sheet_gid") or "0")
    sheet_url = str(data.get("sheet_url") or "").strip()
    if sheet_id:
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", sheet_id):
            return _error("El id de la hoja no es válido")
        sheet_url = SHEET_URL.format(sheet_id=sheet_id, gid=sheet_gid)
    if not nombre:
        return _error("El nombre es obligatorio")
    if not sheet_url:
        return _error("La URL de la hoja es obligatoria")
    columns = data.get("columns") or []
    errors = _columns_errors(columns)
    if errors:
        return _error(errors[0])
    user = _get_user(request)
    if user is None:
        return _error("No hay usuarios creados. Crea uno con: python manage.py createsuperuser",
                      status=401)
    dashboard = Dashboard.objects.create(
        owner=user, nombre=nombre, sheet_url=sheet_url, sheet_gid=sheet_gid,
        sheet_name=str(data.get("sheet_name") or "").strip()[:255],
        tab_name=str(data.get("tab_name") or "").strip()[:255],
        columns=[{"name": c["name"], "type": c["type"], "include": bool(c.get("include", True))}
                 for c in columns],
    )
    return JsonResponse(_serialize_dashboard(dashboard), status=201)


SHEET_URL = "https://docs.google.com/spreadsheets/d/{sheet_id}/edit#gid={gid}"


def _columns_errors(columns) -> list[str]:
    """Las columnas elegidas al conectar la hoja: [{name, type, include}], al menos una incluida.
    Vacío es válido (todas, con el tipo que trae la hoja)."""
    if not isinstance(columns, list):
        return ["'columns' debe ser una lista"]
    if not columns:
        return []
    names = set()
    for c in columns:
        if not isinstance(c, dict) or not str(c.get("name") or "").strip():
            return ["Cada columna necesita un 'name'"]
        if c.get("type") not in COLUMN_TYPES:
            return [f"Tipo no válido para '{c['name']}': usa {', '.join(COLUMN_TYPES)}"]
        if c["name"] in names:
            return [f"Columna repetida: '{c['name']}'"]
        names.add(c["name"])
    if not any(c.get("include", True) for c in columns):
        return ["Incluye al menos una columna"]
    return []


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
        sheet_name=dashboard.sheet_name,
        tab_name=dashboard.tab_name,
        columns=dashboard.columns,
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
    """La hoja del tablero con las columnas y los tipos que se eligieron al conectarla."""
    return apply_column_config(get_sheet_dataframe(dashboard.sheet_id, dashboard.sheet_gid),
                               dashboard.columns)


def _serialize_dashboard(dashboard):
    return {
        "id": dashboard.id,
        "nombre": dashboard.nombre,
        "sheet_url": dashboard.sheet_url,
        "sheet_gid": dashboard.sheet_gid,
        "sheet_name": dashboard.sheet_name,
        "tab_name": dashboard.tab_name,
        "cardCount": dashboard.widgets.count(),
        "created_at": dashboard.created_at.isoformat(),
        "updated": timesince(dashboard.created_at, now()),
        "last_opened_at": dashboard.last_opened_at.isoformat() if dashboard.last_opened_at else None,
    }


# ------------------------------------------------------------------ fuentes (selector)
@require_http_methods(["GET"])
def source_spreadsheets(request):
    """Hojas de Google Drive compartidas con la cuenta de servicio (`?q=` filtra por nombre)."""
    try:
        return JsonResponse({"spreadsheets": google_drive.list_spreadsheets(request.GET.get("q", ""))})
    except SheetError as e:
        return _error(str(e), status=502)


@require_http_methods(["GET"])
def source_tabs(request, spreadsheet_id):
    try:
        return JsonResponse(google_drive.list_tabs(spreadsheet_id))
    except SheetError as e:
        return _error(str(e), status=502)


SOURCE_SAMPLES = 3


@require_http_methods(["GET"])
def source_columns(request, spreadsheet_id, gid):
    """Columnas de la pestaña con el tipo que se infiere y algunos valores de ejemplo."""
    try:
        df = get_sheet_dataframe(spreadsheet_id, gid)
    except SheetError as e:
        return _error(str(e), status=502)
    types = infer_column_types(df)
    columns = []
    for col in df.columns:
        values = [v for v in df[col].dropna().astype(str).str.strip().unique() if v]
        columns.append({"name": col, "type": types[col], "samples": values[:SOURCE_SAMPLES]})
    return JsonResponse({"columns": columns, "rows": len(df)})


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
    schema = {**get_sheet_schema(df), "dimension_fields": get_dimension_fields(df),
              "time_fields": time_fields(df)}
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


@require_http_methods(["GET"])
def widget_suggestions(request, dashboard_id):
    """
    GET ?widget_type=kpi[&refresh=1&avoid=…] → {"suggestions": [...]}
    Dos pedidos sugeridos para el chat del panel, según el tipo de widget y las columnas de la
    hoja. El panel los pide al abrirse, sin esperar, y los muestra como tags.
    """
    dashboard = _owned_dashboard(request, dashboard_id)
    if not dashboard:
        return _error("Dashboard no encontrado", status=404)
    widget_type = request.GET.get("widget_type") or ""
    widget = WIDGET_REGISTRY.get(widget_type) if widget_type in WIDGET_REGISTRY else None
    if not widget or not widget.ai_enabled:
        return _error("Tipo de widget desconocido")
    try:
        df = _load_sheet(dashboard)
    except SheetError as e:
        return _error(str(e), status=502)

    from sheets_reports.services.ai_suggestions import widget_suggestions as suggest
    source = f"{dashboard.sheet_id}:{dashboard.sheet_gid}"
    # «Otras ideas»: refresh=1 salta la caché y avoid=… son las que ya ve el usuario.
    return JsonResponse({"suggestions": suggest(widget, df, source,
                                                refresh=request.GET.get("refresh") == "1",
                                                avoid=request.GET.getlist("avoid"))})


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
