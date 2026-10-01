import json
import logging
import re

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils.timesince import timesince
from django.utils.timezone import now
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from sheets_reports.dsl.context import SheetContext
from sheets_reports.dsl.errors import SpecValidationError
from sheets_reports.models import Dashboard, Widget
from sheets_reports.services.ai_spec import SpecGenerationError, generate_widget_spec
from sheets_reports.services.sheets import (
    SheetError,
    get_dimension_fields,
    get_field_samples,
    get_sheet_dataframe,
    get_sheet_schema,
    invalidate_sheet_cache,
)
from sheets_reports.services.widget_service import WidgetService, clean_position
from sheets_reports.widgets import WIDGETS

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Páginas
# ---------------------------------------------------------------------------

def home(request):
    return render(request, "home.html")


def board_editor(request, dashboard_id):
    dashboard = get_object_or_404(Dashboard, id=dashboard_id)
    return render(request, "board_editor.html", {
        "dashboard": dashboard, "refresh_minutes": settings.WIDGET_REFRESH_MINUTES,
    })


def board_view(request, dashboard_id):
    dashboard = get_object_or_404(Dashboard, id=dashboard_id)
    return render(request, "board_view.html", {
        "dashboard": dashboard, "refresh_minutes": settings.WIDGET_REFRESH_MINUTES,
    })


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_user(request):
    # Todavía no hay login en la app: sin sesión se usa el primer superusuario.
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


NO_USER_MESSAGE = "No hay ningún usuario. Crea uno con: python manage.py createsuperuser"


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
    """La hoja del tablero, desde la caché."""
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
        "position": widget.position,
        "data_spec": widget.data_spec,
        "view_spec": widget.view_spec,
        "source_prompt": widget.source_prompt,
    }


def _board_filters(request, ctx: SheetContext) -> list[dict]:
    """
    Filtros del tablero, desde la query string:
      ?filters=[{"field": ..., "op": ..., "value": ...}]   (JSON)
      ?filtro_<columna>=<valor>                             (atajo: eq)
    El servicio los valida con las mismas reglas que las condiciones del data_spec.
    """
    filters = []
    raw = request.GET.get("filters")
    if raw:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            raise ValueError("El parámetro 'filters' no es JSON válido.")
        if not isinstance(parsed, list):
            raise ValueError("El parámetro 'filters' debe ser una lista.")
        filters.extend(parsed)
    for key, value in request.GET.items():
        if key.startswith("filtro_") and value != "":
            field = key[len("filtro_"):]
            if ctx.is_numeric(field):
                try:
                    value = float(value)
                except ValueError:
                    pass
            filters.append({"field": field, "op": "eq", "value": value})
    return filters


# ---------------------------------------------------------------------------
# Dashboards
# ---------------------------------------------------------------------------

@csrf_exempt
@require_http_methods(["GET", "POST"])
def dashboard_list(request):
    user = _get_user(request)
    if not user:
        return _error(NO_USER_MESSAGE, status=401)

    if request.method == "GET":
        dashboards = Dashboard.objects.filter(owner=user).prefetch_related("widgets")
        return JsonResponse([_serialize_dashboard(d) for d in dashboards], safe=False)

    try:
        data = _json_body(request)
    except ValueError as e:
        return _error(str(e))
    nombre = (data.get("nombre") or "").strip()
    sheet_url = (data.get("sheet_url") or "").strip()
    if not nombre:
        return _error("El nombre es obligatorio")
    dashboard = Dashboard(
        nombre=nombre,
        owner=user,
        sheet_url=sheet_url,
        sheet_gid=str(data.get("sheet_gid") or _gid_from_url(sheet_url) or "0"),
    )
    if not dashboard.sheet_id:
        return _error("La URL no parece de una hoja de Google Sheets")
    dashboard.save()
    return JsonResponse(_serialize_dashboard(dashboard), status=201)


@csrf_exempt
@require_http_methods(["PUT", "DELETE"])
def dashboard_detail(request, dashboard_id):
    dashboard = _owned_dashboard(request, dashboard_id)
    if not dashboard:
        return _error("Dashboard no encontrado", status=404)

    if request.method == "DELETE":
        dashboard.delete()
        return JsonResponse({"deleted": True})

    try:
        data = _json_body(request)
    except ValueError as e:
        return _error(str(e))
    if "nombre" in data:
        nombre = (data["nombre"] or "").strip()
        if not nombre:
            return _error("El nombre no puede estar vacío")
        dashboard.nombre = nombre
    if "sheet_url" in data:
        dashboard.sheet_url = (data["sheet_url"] or "").strip()
        dashboard.sheet_gid = str(data.get("sheet_gid") or _gid_from_url(dashboard.sheet_url) or "0")
        if not dashboard.sheet_id:
            return _error("La URL no parece de una hoja de Google Sheets")
    dashboard.save()
    return JsonResponse(_serialize_dashboard(dashboard))


@csrf_exempt
@require_http_methods(["POST"])
def dashboard_duplicate(request, dashboard_id):
    original = _owned_dashboard(request, dashboard_id)
    if not original:
        return _error("Dashboard no encontrado", status=404)
    with transaction.atomic():
        copy = Dashboard.objects.create(
            nombre=f"{original.nombre} (copia)",
            owner=original.owner,
            sheet_url=original.sheet_url,
            sheet_gid=original.sheet_gid,
        )
        Widget.objects.bulk_create([
            Widget(
                dashboard=copy,
                type=w.type,
                position=w.position,
                data_spec=w.data_spec,
                view_spec=w.view_spec,
                source_prompt=w.source_prompt,
            )
            for w in original.widgets.all()
        ])
    return JsonResponse(_serialize_dashboard(copy), status=201)


@require_http_methods(["GET"])
def dashboard_schema(request, dashboard_id):
    """Columnas de la hoja (chips del frontend y selects del builder)."""
    dashboard = get_object_or_404(Dashboard, id=dashboard_id)
    if request.GET.get("refresh"):
        invalidate_sheet_cache(dashboard.sheet_id, dashboard.sheet_gid)
    try:
        df = _load_sheet(dashboard)
    except SheetError as e:
        return _error(str(e), status=502)
    return JsonResponse({
        **get_sheet_schema(df), "dimension_fields": get_dimension_fields(df), "sample_values": get_field_samples(df),
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
        service = WidgetService(dashboard, _load_sheet(dashboard))
    except SheetError as e:
        return _error(str(e), status=502)
    try:
        filters = service.board_filters(_board_filters(request, service.ctx))
    except ValueError as e:
        return _error(str(e))

    widgets = sorted(dashboard.widgets.all(), key=lambda w: (w.position.get("y", 0), w.id))
    return JsonResponse({
        "dashboard": _serialize_dashboard(dashboard),
        "widgets": [{**_serialize_widget(w), **service.render(w, filters)} for w in widgets],
    })


# ---------------------------------------------------------------------------
# Widgets
# ---------------------------------------------------------------------------

@csrf_exempt
@require_http_methods(["POST"])
def table_assistant(request, dashboard_id):
    """
    POST {prompt}
    "Consulta con la IA" de las tablas: la IA propone la configuración (filas, columnas,
    métricas, orden, filtros) como un data_spec validado contra la hoja, y el panel la muestra
    como pasos a seguir en el constructor. NO crea ni modifica widgets.
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
        ctx = SheetContext.from_dataframe(df, dashboard.sheet_gid, samples=get_field_samples(df))
        spec = generate_widget_spec(prompt, "dynamic_table", ctx)
    except SpecGenerationError as e:
        return _error(str(e), status=422)
    except Exception:
        logger.exception("Falló la consulta de tabla con IA")
        return _error("La IA no respondió correctamente. Intenta de nuevo.", status=502)

    return JsonResponse({"data_spec": spec["data_spec"], "view_spec": spec["view_spec"]})


def _validation_error(e: SpecValidationError):
    return _error(e.errors[0], status=422, errors=e.errors)


@csrf_exempt
@require_http_methods(["POST"])
def create_widget(request, dashboard_id):
    """
    POST {type, <claves del data_spec>, <opciones de vista del tipo>, title?, labels?,
          position?, display?}
    Crea un widget desde el builder (el usuario elige columnas y métricas). NUNCA llama a la IA.
    """
    dashboard = _owned_dashboard(request, dashboard_id)
    if not dashboard:
        return _error("Dashboard no encontrado", status=404)
    try:
        data = _json_body(request)
    except ValueError as e:
        return _error(str(e))
    widget_type = data.get("type")
    if widget_type not in WIDGETS:
        return _error(f"Tipo de widget desconocido: {widget_type}")

    try:
        service = WidgetService(dashboard, _load_sheet(dashboard))
    except SheetError as e:
        return _error(str(e), status=502)
    try:
        widget = service.create(widget_type, data)
    except SpecValidationError as e:
        return _validation_error(e)
    return JsonResponse({**_serialize_widget(widget), **service.render(widget)}, status=201)


@csrf_exempt
@require_http_methods(["PUT"])
def update_widget_spec(request, widget_id):
    """
    PUT {<claves del data_spec>, <opciones de vista del tipo>, title?, labels?}
    Edición manual desde el builder: aplica las mismas validaciones que el camino de IA,
    reconstruye view_spec y guarda. Este camino NUNCA llama a la IA.
    """
    widget = _owned_widget(request, widget_id)
    if not widget:
        return _error("Widget no encontrado", status=404)
    try:
        data = _json_body(request)
    except ValueError as e:
        return _error(str(e))

    try:
        service = WidgetService(widget.dashboard, _load_sheet(widget.dashboard))
    except SheetError as e:
        return _error(str(e), status=502)
    try:
        service.update_spec(widget, data)
    except SpecValidationError as e:
        return _validation_error(e)
    return JsonResponse({**_serialize_widget(widget), **service.render(widget)})


@csrf_exempt
@require_http_methods(["PUT", "DELETE"])
def widget_detail(request, widget_id):
    """PUT {position?, title?, display?}: solo presentación, no toca data_spec. DELETE: borra."""
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
    if "position" in data:
        widget.position = clean_position(data["position"], fallback=widget.position)
    view_spec = dict(widget.view_spec)
    if "title" in data:
        view_spec["title"] = str(data["title"] or "").strip() or view_spec.get("title", "")
    if isinstance(data.get("display"), dict):
        view_spec["display"] = data["display"]
    widget.view_spec = view_spec
    widget.save()
    return JsonResponse(_serialize_widget(widget))
