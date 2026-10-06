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

from sheets_reports.models import Dashboard, DataSource, Widget
from sheets_reports.services import google_drive, sheets
from sheets_reports.services.sheets import (
    COLUMN_TYPES,
    SheetError,
    get_dimension_fields,
    get_field_samples,
    get_sheet_schema,
    infer_column_types,
    invalidate_sheet_cache,
    source_key,
)
from sheets_reports.utils.data import time_fields
from sheets_reports.widgets import WIDGETS as WIDGET_REGISTRY
from sheets_reports.utils.validation import SpecValidationError
from sheets_reports.services.widget_service import WidgetService

logger = logging.getLogger(__name__)

NO_SOURCE_ERROR = "La fuente de datos de este widget ya no existe. Edita el widget y elige otra."


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
    """GET: tableros del usuario actual. POST {nombre, sheet_id, sheet_gid, sheet_name,
    tab_name, columns}: crea el tablero con su primera fuente de datos."""
    user = _get_user(request)
    if user is None:
        return _error("No hay usuarios creados. Crea uno con: python manage.py createsuperuser",
                      status=401)
    if request.method == "GET":
        dashboards = Dashboard.objects.filter(owner=user).prefetch_related("sources")
        return JsonResponse([_serialize_dashboard(d) for d in dashboards], safe=False)
    try:
        data = _json_body(request)
    except ValueError as e:
        return _error(str(e))
    nombre = str(data.get("nombre") or "").strip()
    if not nombre:
        return _error("El nombre es obligatorio")
    source_data, error = _source_fields(data)
    if error:
        return _error(error)
    dashboard = Dashboard.objects.create(owner=user, nombre=nombre)
    DataSource.objects.create(dashboard=dashboard, **source_data)
    return JsonResponse(_serialize_dashboard(dashboard), status=201)


def _source_fields(data: dict) -> tuple[dict, str | None]:
    """Los campos de una fuente nueva desde el selector: ({...}, None) o ({}, error)."""
    sheet_id = str(data.get("sheet_id") or "").strip()
    if not sheet_id:
        return {}, "Elige una hoja de cálculo"
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", sheet_id):
        return {}, "El id de la hoja no es válido"
    columns = data.get("columns") or []
    errors = _columns_errors(columns)
    if errors:
        return {}, errors[0]
    return {
        "sheet_id": sheet_id,
        "gid": str(data.get("sheet_gid") or "0"),
        "sheet_name": str(data.get("sheet_name") or "").strip()[:255],
        "tab_name": str(data.get("tab_name") or "").strip()[:255],
        "columns": _clean_columns(columns),
    }, None


def _clean_columns(columns: list) -> list[dict]:
    return [{"name": c["name"], "type": c["type"], "include": bool(c.get("include", True))}
            for c in columns]


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
    """GET / PUT {nombre} / DELETE de un tablero del usuario."""
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
    dashboard.save()
    return JsonResponse(_serialize_dashboard(dashboard))


@csrf_exempt
@require_http_methods(["POST"])
def dashboard_duplicate(request, dashboard_id):
    """Duplica un tablero con sus fuentes y sus widgets (cada uno con la copia de su fuente)."""
    dashboard = _owned_dashboard(request, dashboard_id)
    if not dashboard:
        return _error("Dashboard no encontrado", status=404)
    new_dashboard = Dashboard.objects.create(owner=_get_user(request), nombre=f"{dashboard.nombre} (copia)")
    copies = {}
    for source in dashboard.sources.all():
        copies[source.id] = DataSource.objects.create(
            dashboard=new_dashboard, kind=source.kind, sheet_id=source.sheet_id, gid=source.gid,
            sheet_name=source.sheet_name, tab_name=source.tab_name, columns=source.columns,
        )
    for widget in dashboard.widgets.all():
        Widget.objects.create(
            dashboard=new_dashboard,
            source=copies.get(widget.source_id),
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
    return Widget.objects.select_related("dashboard", "source").filter(
        id=widget_id, dashboard__owner=_get_user(request)
    ).first()


def _owned_source(request, source_id):
    return DataSource.objects.select_related("dashboard").filter(
        id=source_id, dashboard__owner=_get_user(request)
    ).first()


def _dashboard_source(dashboard, raw_id, default=None):
    """La fuente `raw_id` del tablero (o `default` si no viene). Lanza ValueError si no es suya."""
    if raw_id in (None, ""):
        return default
    try:
        source = dashboard.sources.filter(id=int(raw_id)).first()
    except (TypeError, ValueError):
        source = None
    if source is None:
        raise ValueError("La fuente de datos no pertenece a este tablero")
    return source


def _serialize_dashboard(dashboard):
    return {
        "id": dashboard.id,
        "nombre": dashboard.nombre,
        "sources": [s.label for s in dashboard.sources.all()],
        "cardCount": dashboard.widgets.count(),
        "created_at": dashboard.created_at.isoformat(),
        "updated": timesince(dashboard.created_at, now()),
        "last_opened_at": dashboard.last_opened_at.isoformat() if dashboard.last_opened_at else None,
    }


def _serialize_source(source):
    included = [c for c in source.columns if c.get("include", True)]
    return {
        "id": source.id,
        "label": source.label,
        "kind": source.kind,
        "sheet_id": source.sheet_id,
        "gid": source.gid,
        "sheet_name": source.sheet_name,
        "tab_name": source.tab_name,
        # Sin configuración (fuentes anteriores al selector) se usan todas: no se sabe cuántas.
        "columns_included": len(included) if source.columns else None,
        "columns_total": len(source.columns) if source.columns else None,
        "widgets": source.widgets.count(),
    }


# ------------------------------------------------------------------ selector de hojas
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


def _sheet_columns(df) -> list[dict]:
    """Columnas de la hoja tal cual: tipo inferido y algunos valores de ejemplo."""
    types = infer_column_types(df)
    columns = []
    for col in df.columns:
        values = [v for v in df[col].dropna().astype(str).str.strip().unique() if v]
        columns.append({"name": col, "type": types[col], "samples": values[:SOURCE_SAMPLES]})
    return columns


@require_http_methods(["GET"])
def source_columns(request, spreadsheet_id, gid):
    """Columnas de la pestaña con el tipo que se infiere y algunos valores de ejemplo."""
    try:
        df = sheets.get_sheet_dataframe(spreadsheet_id, gid)
    except SheetError as e:
        return _error(str(e), status=502)
    return JsonResponse({"columns": _sheet_columns(df), "rows": len(df)})


# ------------------------------------------------------------------ fuentes del tablero
@csrf_exempt
@require_http_methods(["GET", "POST"])
def dashboard_sources(request, dashboard_id):
    """GET: fuentes del tablero. POST {sheet_id, sheet_gid, sheet_name, tab_name, columns}: agrega una."""
    dashboard = _owned_dashboard(request, dashboard_id)
    if not dashboard:
        return _error("Dashboard no encontrado", status=404)
    if request.method == "GET":
        return JsonResponse({"sources": [_serialize_source(s) for s in dashboard.sources.all()]})
    try:
        data = _json_body(request)
    except ValueError as e:
        return _error(str(e))
    source_data, error = _source_fields(data)
    if error:
        return _error(error)
    source = DataSource.objects.create(dashboard=dashboard, **source_data)
    return JsonResponse(_serialize_source(source), status=201)


@csrf_exempt
@require_http_methods(["PUT", "DELETE"])
def source_detail(request, source_id):
    """PUT {columns}: columnas y tipos de la fuente. DELETE: la borra; sus widgets quedan sin
    fuente y lo dicen al calcularse (no se chequea antes: una columna excluida o un tipo
    cambiado también los rompe, y el error se ve en cada widget)."""
    source = _owned_source(request, source_id)
    if not source:
        return _error("Fuente no encontrada", status=404)
    if request.method == "DELETE":
        source.delete()
        return JsonResponse({"deleted": True})
    try:
        data = _json_body(request)
    except ValueError as e:
        return _error(str(e))
    columns = data.get("columns")
    errors = _columns_errors(columns)
    if errors:
        return _error(errors[0])
    source.columns = _clean_columns(columns)
    source.save(update_fields=["columns"])
    return JsonResponse(_serialize_source(source))


@require_http_methods(["GET"])
def source_saved_columns(request, source_id):
    """Las columnas de la hoja de la fuente para editarla: lo guardado (tipo, incluir) manda;
    las columnas nuevas de la hoja entran incluidas y las que ya no están se descartan."""
    source = _owned_source(request, source_id)
    if not source:
        return _error("Fuente no encontrada", status=404)
    try:
        df = sheets.get_sheet_dataframe(source.sheet_id, source.gid)
    except SheetError as e:
        return _error(str(e), status=502)
    saved = {c["name"]: c for c in source.columns}
    columns = []
    for column in _sheet_columns(df):
        stored = saved.get(column["name"])
        columns.append({**column,
                        "type": stored["type"] if stored else column["type"],
                        "include": stored.get("include", True) if stored else True})
    return JsonResponse({"columns": columns, "rows": len(df), "source": _serialize_source(source)})


@require_http_methods(["GET"])
def source_schema(request, source_id):
    """Columnas de la fuente para el panel de un widget (agrupables, numéricas, de tiempo y
    valores de ejemplo) y el manifiesto de widgets."""
    source = _owned_source(request, source_id)
    if not source:
        return _error("Fuente no encontrada", status=404)
    if request.GET.get("refresh"):
        invalidate_sheet_cache(source.sheet_id, source.gid)
    try:
        df = sheets.load_source(source)
    except SheetError as e:
        return _error(str(e), status=502)
    schema = {**get_sheet_schema(df), "dimension_fields": get_dimension_fields(df),
              "time_fields": time_fields(df)}
    return JsonResponse({
        **schema, "sample_values": get_field_samples(df),
        "widget_manifest": _widget_manifest(),
    })


def _serialize_widget(widget):
    return {
        "id": widget.id,
        "type": widget.type,
        "source_id": widget.source_id,
        "title": widget.title,
        "position": widget.position,
        "fields": widget.fields,
        "style": widget.style,
        "source_prompt": widget.source_prompt,
    }


def _board_filters_error(raw) -> str | None:
    """El parámetro `filters` debe ser una lista JSON (cada filtro se valida por fuente)."""
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return "El parámetro 'filters' no es JSON válido."
    return None if isinstance(parsed, list) else "El parámetro 'filters' debe ser una lista."


@require_http_methods(["GET"])
def dashboard_render(request, dashboard_id):
    """
    Datos listos para dibujar todos los widgets del tablero. NUNCA llama a la IA: es puro
    cálculo sobre las hojas cacheadas, para que el refresco periódico del frontend sea barato.
    Es de solo lectura y sirve también a la vista compartida (board_view).

    Cada widget se calcula sobre su fuente; cada fuente se carga una vez. Una fuente que no se
    puede leer, o un widget sin fuente, solo afecta a esos widgets. Un filtro del tablero se
    aplica en las fuentes que tienen su columna, y se avisa solo si no vale en ninguna.
    """
    dashboard = get_object_or_404(Dashboard, id=dashboard_id)
    raw_filters = request.GET.get("filters")
    error = _board_filters_error(raw_filters)
    if error:
        return _error(error)

    sources = {s.id: s for s in dashboard.sources.all()}
    loaded = {}  # source_id -> (service, filters) o el mensaje de error de la hoja
    ignored_by_source = []

    def service_for(source_id):
        if source_id not in loaded:
            source = sources[source_id]
            try:
                service = WidgetService(dashboard, sheets.load_source(source), source)
            except SheetError as e:
                loaded[source_id] = str(e)
            else:
                filters, ignored = service.parse_board_filters(raw_filters)
                ignored_by_source.append(ignored)
                loaded[source_id] = (service, filters)
        return loaded[source_id]

    widgets_data = []
    for widget in dashboard.widgets.all():
        if widget.source_id not in sources:
            rendered = {"error": NO_SOURCE_ERROR}
        else:
            entry = service_for(widget.source_id)
            rendered = {"error": entry} if isinstance(entry, str) else entry[0].render(widget, entry[1])
        widgets_data.append({
            **_serialize_widget(widget),
            "data": rendered.get("data"),
            "error": rendered.get("error"),
        })

    # Un filtro ignorado en todas las fuentes que se leyeron (en el orden en que llegaron).
    filter_errors = []
    if ignored_by_source:
        everywhere = set(ignored_by_source[0]).intersection(*map(set, ignored_by_source[1:]))
        filter_errors = [e for e in ignored_by_source[0] if e in everywhere]

    return JsonResponse({
        "dashboard": {**_serialize_dashboard(dashboard),
                      "sources": [{"id": s.id, "label": s.label} for s in sources.values()]},
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


def _require_source(dashboard, raw_id):
    """(fuente, None) o (None, respuesta de error): la fuente elegida, o la primera del tablero."""
    try:
        source = _dashboard_source(dashboard, raw_id, default=dashboard.sources.first())
    except ValueError as e:
        return None, _error(str(e))
    if source is None:
        return None, _error("El tablero no tiene fuentes de datos. Agrega una.")
    return source, None


@csrf_exempt
@require_http_methods(["POST"])
def table_assistant(request, dashboard_id):
    """
    POST {prompt, source?, widget_type?, current?, history?}
    Chat con la IA del panel de un widget: la IA propone el `WidgetForm` (fields + style)
    validado contra la hoja de la fuente, ajustando el borrador actual (`current`:
    `{title, fields, style}`) con el contexto de los mensajes previos (`history`). El panel lo
    muestra como pasos. NO crea ni modifica widgets.
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

    source, error = _require_source(dashboard, data.get("source"))
    if error:
        return error
    try:
        df = sheets.load_source(source)
    except SheetError as e:
        return _error(str(e), status=502)

    try:
        from sheets_reports.engine.context import SheetContext
        from sheets_reports.services.ai_spec import SpecGenerationError, generate_widget_form
        ctx = SheetContext.from_dataframe(df, source.gid, samples=get_field_samples(df))
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
    GET ?widget_type=kpi[&source=<id>&refresh=1&avoid=…] → {"suggestions": [...]}
    Dos pedidos sugeridos para el chat del panel, según el tipo de widget y las columnas de la
    hoja de la fuente. El panel los pide al abrirse, sin esperar, y los muestra como tags.
    """
    dashboard = _owned_dashboard(request, dashboard_id)
    if not dashboard:
        return _error("Dashboard no encontrado", status=404)
    widget_type = request.GET.get("widget_type") or ""
    widget = WIDGET_REGISTRY.get(widget_type) if widget_type in WIDGET_REGISTRY else None
    if not widget or not widget.ai_enabled:
        return _error("Tipo de widget desconocido")
    source, error = _require_source(dashboard, request.GET.get("source"))
    if error:
        return error
    try:
        df = sheets.load_source(source)
    except SheetError as e:
        return _error(str(e), status=502)

    from sheets_reports.services.ai_suggestions import widget_suggestions as suggest
    # «Otras ideas»: refresh=1 salta la caché y avoid=… son las que ya ve el usuario.
    return JsonResponse({"suggestions": suggest(widget, df, source_key(source),
                                                refresh=request.GET.get("refresh") == "1",
                                                avoid=request.GET.getlist("avoid"))})


@csrf_exempt
@require_http_methods(["POST"])
def create_widget(request, dashboard_id):
    """
    POST {type, source?, fields?, style?, title?, position?}
    Crea un widget desde el builder (o desde la IA) sobre la fuente elegida (o la primera del
    tablero). NUNCA llama a la IA por su cuenta.
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

    source, error = _require_source(dashboard, data.get("source"))
    if error:
        return error
    try:
        df = sheets.load_source(source)
    except SheetError as e:
        return _error(str(e), status=502)

    service = WidgetService(dashboard, df, source)
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
    PUT {position?, title?, source?, fields?, style?}: guarda la configuración y devuelve el
    widget ya calculado (sobre la fuente nueva si cambia). DELETE: borra.
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
        source = _dashboard_source(widget.dashboard, data.get("source"), default=widget.source)
    except ValueError as e:
        return _error(str(e))
    if source is None:
        return _error(NO_SOURCE_ERROR)
    try:
        df = sheets.load_source(source)
    except SheetError as e:
        return _error(str(e), status=502)

    service = WidgetService(widget.dashboard, df, source)
    try:
        service.update(widget, data)
    except SpecValidationError as e:
        return _error(str(e), status=422)
    except Exception as e:
        return _error(str(e), status=422)

    rendered = service.render(widget)
    return JsonResponse({**_serialize_widget(widget), "data": rendered.get("data"),
                         "error": rendered.get("error")})
