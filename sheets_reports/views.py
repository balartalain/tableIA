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
from jsonschema import Draft202012Validator

from sheets_reports.models import Dashboard, Widget, default_position
from sheets_reports.services.ai_spec import SpecGenerationError, generate_widget_spec
from sheets_reports.services.apex_compiler import compile_view
from sheets_reports.services.query_engine import ResultTooLargeError, apply_filters, run_data_spec
from sheets_reports.services.sheets import (
    SheetError,
    get_dimension_fields,
    get_field_samples,
    get_sheet_dataframe,
    get_sheet_schema,
    invalidate_sheet_cache,
)
from sheets_reports.services.spec_validation import (
    WIDGET_TYPES,
    build_view_spec,
    filter_schema,
    validate_widget_spec,
)

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
    """(df, schema) de la hoja del tablero, desde la caché."""
    df = get_sheet_dataframe(dashboard.sheet_id, dashboard.sheet_gid)
    return df, get_sheet_schema(df)


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


def _clean_position(value, fallback=None) -> dict:
    position = dict(fallback or default_position())
    if isinstance(value, dict):
        for key in ("x", "y", "w", "h"):
            if isinstance(value.get(key), (int, float)) and not isinstance(value.get(key), bool):
                position[key] = int(value[key])
    position["w"] = min(max(position["w"], 1), 12)
    position["x"] = min(max(position["x"], 0), 12)
    position["h"] = min(max(position["h"], 100), 3000)
    return position


def _render_widget(widget, df, extra_filters=None) -> dict:
    """Ejecuta y compila un widget. Un error en un widget no tumba el tablero."""
    try:
        # Los filtros del tablero se aplican antes: definen el universo del widget (el
        # denominador de sus porcentajes), mientras que los del propio widget lo recortan.
        if extra_filters:
            df = apply_filters(df, extra_filters)
        layout = "table" if widget.type == "table" else "auto"
        result = run_data_spec(df, widget.data_spec, layout=layout)
        return {"data": compile_view(widget.type, result, widget.view_spec)}
    except ResultTooLargeError as e:
        return {"error": str(e)}
    except KeyError as e:
        # La hoja cambió y ya no tiene una columna que el spec usa.
        return {"error": f"La columna {e} ya no existe en la hoja. Edita el widget."}
    except Exception:
        logger.exception("Error renderizando el widget %s", widget.id)
        return {"error": "No se pudo calcular este widget."}


def _board_filters(request, schema) -> list[dict]:
    """
    Filtros del tablero, desde la query string:
      ?filters=[{"field": ..., "op": ..., "value": ...}]   (JSON)
      ?filtro_<columna>=<valor>                             (atajo: eq)
    Se validan con el mismo sub-schema `filter` que el data_spec.
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
            if field in schema["numeric_fields"]:
                try:
                    value = float(value)
                except ValueError:
                    pass
            filters.append({"field": field, "op": "eq", "value": value})

    validator = Draft202012Validator(filter_schema(schema))
    for f in filters:
        errors = list(validator.iter_errors(f))
        if errors:
            raise ValueError(f"Filtro inválido {json.dumps(f, ensure_ascii=False)}: {errors[0].message}")
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
        df, schema = _load_sheet(dashboard)
    except SheetError as e:
        return _error(str(e), status=502)
    return JsonResponse({**schema, "dimension_fields": get_dimension_fields(df)})


@require_http_methods(["GET"])
def dashboard_render(request, dashboard_id):
    """
    Datos listos para dibujar todos los widgets del tablero. NUNCA llama a la IA: es puro
    cálculo sobre la hoja cacheada, para que el refresco periódico del frontend sea barato.
    Es de solo lectura y sirve también a la vista compartida (board_view).
    """
    dashboard = get_object_or_404(Dashboard, id=dashboard_id)
    try:
        df, schema = _load_sheet(dashboard)
    except SheetError as e:
        return _error(str(e), status=502)
    try:
        filters = _board_filters(request, schema)
    except ValueError as e:
        return _error(str(e))

    widgets = sorted(dashboard.widgets.all(), key=lambda w: (w.position.get("y", 0), w.id))
    return JsonResponse({
        "dashboard": _serialize_dashboard(dashboard),
        "widgets": [{**_serialize_widget(w), **_render_widget(w, df, filters)} for w in widgets],
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
        df, schema = _load_sheet(dashboard)
    except SheetError as e:
        return _error(str(e), status=502)

    try:
        spec = generate_widget_spec(
            prompt, "table", {**schema, "sample_values": get_field_samples(df)}, source=dashboard.sheet_gid,
        )
    except SpecGenerationError as e:
        return _error(str(e), status=422)
    except Exception:
        logger.exception("Falló la consulta de tabla con IA")
        return _error("La IA no respondió correctamente. Intenta de nuevo.", status=502)

    return JsonResponse({"data_spec": spec["data_spec"], "view_spec": spec["view_spec"]})


def _clean_labels(data: dict, previous: dict | None = None) -> dict:
    """
    Cabeceras de columna ({columna o alias: "Texto"}) que llegan del builder; las vacías se
    ignoran en build_view_spec. Sin `labels` en el request se conserva el que ya tenía el widget.
    """
    labels = data.get("labels")
    if isinstance(labels, dict):
        return labels
    return (previous or {}).get("labels") or {}


def _builder_data_spec(data: dict, dashboard, widget_type: str, schema: dict, previous: dict | None):
    """
    data_spec a partir de los controles del builder ({dimensions, pivot, metrics, sort?,
    filters?}). Retorna (data_spec, errores). Mismas validaciones que el camino de IA.
    """
    previous = previous or {}
    dimensions = data.get("dimensions") or []
    if isinstance(dimensions, str):
        dimensions = [dimensions]
    metrics = data.get("metrics") or []
    if not isinstance(metrics, list):
        return None, ["metrics debe ser una lista."]
    # count cuenta filas: si el builder no manda campo, se usa la dimensión (o la primera
    # columna en un KPI), como indica la semántica del DSL.
    for m in metrics:
        if isinstance(m, dict) and m.get("agg") == "count" and not m.get("field"):
            m["field"] = dimensions[0] if dimensions else (schema["all_fields"] or [""])[0]
    data_spec = {
        "source": dashboard.sheet_gid,
        "dimensions": dimensions,
        "pivot": data.get("pivot") or None,
        "metrics": metrics,
        "filters": data["filters"] if "filters" in data else previous.get("filters") or [],
        "sort": data["sort"] if "sort" in data else previous.get("sort"),
    }
    return data_spec, validate_widget_spec(widget_type, data_spec, schema, dashboard.sheet_gid)


@csrf_exempt
@require_http_methods(["POST"])
def create_widget(request, dashboard_id):
    """
    POST {type, dimensions, pivot, metrics, stacked, sort?, labels?, title?, position?}
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
    if widget_type not in WIDGET_TYPES:
        return _error(f"Tipo de widget desconocido: {widget_type}")

    try:
        df, schema = _load_sheet(dashboard)
    except SheetError as e:
        return _error(str(e), status=502)

    data_spec, errors = _builder_data_spec(data, dashboard, widget_type, schema, None)
    if errors:
        return _error(errors[0], status=422, errors=errors)

    display = data.get("display") if isinstance(data.get("display"), dict) else {}
    widget = Widget.objects.create(
        dashboard=dashboard,
        type=widget_type,
        position=_clean_position(data.get("position")),
        data_spec=data_spec,
        view_spec=build_view_spec(widget_type, data_spec, {
            "title": data.get("title"), "labels": _clean_labels(data), "stacked": bool(data.get("stacked")),
            "display": display,
        }),
    )
    return JsonResponse({**_serialize_widget(widget), **_render_widget(widget, df)}, status=201)


@csrf_exempt
@require_http_methods(["PUT"])
def update_widget_spec(request, widget_id):
    """
    PUT {dimensions, pivot, metrics, stacked, sort?, labels?, filters?}
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
        df, schema = _load_sheet(widget.dashboard)
    except SheetError as e:
        return _error(str(e), status=502)

    data_spec, errors = _builder_data_spec(data, widget.dashboard, widget.type, schema, widget.data_spec)
    if errors:
        return _error(errors[0], status=422, errors=errors)

    previous = widget.view_spec or {}
    widget.data_spec = data_spec
    widget.view_spec = build_view_spec(widget.type, data_spec, {
        "title": data.get("title", previous.get("title")),
        "labels": _clean_labels(data, previous),
        "stacked": bool(data.get("stacked", previous.get("stacked", False))),
        "display": previous.get("display"),
    })
    widget.save()
    return JsonResponse({**_serialize_widget(widget), **_render_widget(widget, df)})


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
        widget.position = _clean_position(data["position"], fallback=widget.position)
    view_spec = dict(widget.view_spec)
    if "title" in data:
        view_spec["title"] = str(data["title"] or "").strip() or view_spec.get("title", "")
    if isinstance(data.get("display"), dict):
        view_spec["display"] = data["display"]
    widget.view_spec = view_spec
    widget.save()
    return JsonResponse(_serialize_widget(widget))
