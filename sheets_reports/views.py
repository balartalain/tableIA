import json
import logging
import re
import uuid

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
    COLUMN_FORMATS,
    COLUMN_TYPES,
    SheetError,
    get_dimension_fields,
    get_field_samples,
    get_sheet_schema,
    column_display_name,
    infer_column_types,
    source_key,
)
from sheets_reports.engine.formulas import (
    BUILDER_CATALOG,
    FORMATS as FORMULA_FORMATS,
    FormulaError,
    aggregated_fields,
    apply_calculated_fields,
    compile_formula,
    formula_text,
    formula_tree,
    rename_columns,
)
from sheets_reports.services.ai_spec import panel_options
from sheets_reports.services.source_columns import impact, rename_in_widgets
from sheets_reports.utils.data import time_fields, to_key, to_python
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
            # Qué ofrece el panel en las métricas: las mismas reglas que valida el servidor.
            "panel_options": panel_options(widget_cls),
        }
        for key, widget_cls in WIDGET_REGISTRY.items()
    }


def board_editor(request, dashboard_id):
    dashboard = get_object_or_404(Dashboard, id=dashboard_id)
    Dashboard.objects.filter(id=dashboard.id).update(last_opened_at=now())
    return render(request, "board_editor.html", {
        "dashboard": dashboard, "widget_manifest": _widget_manifest(),
        "service_account_email": sheets.service_account_email(), "formula_catalog": BUILDER_CATALOG,
    })


def board_new(request):
    """Editor sin tablero: el bottom sheet abre el selector de fuente y, al confirmar, crea el
    tablero y redirige a su editor."""
    return render(request, "board_editor.html", {
        "dashboard": None, "widget_manifest": _widget_manifest(),
        "service_account_email": sheets.service_account_email(), "formula_catalog": BUILDER_CATALOG,
    })


def board_view(request, dashboard_id):
    dashboard = get_object_or_404(Dashboard, id=dashboard_id)
    return render(request, "board_view.html", {"dashboard": dashboard})


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
        "name": str(data.get("name") or "").strip()[:255],
        "first_row_headers": bool(data.get("first_row_headers", True)),
        "columns": _clean_columns(columns),
    }, None


def _clean_columns(columns: list) -> list[dict]:
    out = []
    for c in columns:
        column = {"name": c["name"], "type": c["type"], "include": bool(c.get("include", True))}
        label = str(c.get("label") or "").strip()
        if label and label != c["name"]:
            column["label"] = label
        # Formato (moneda, %): solo en las numéricas; sin él se muestran como número.
        if c["type"] == "number" and c.get("format") in COLUMN_FORMATS:
            column["format"] = c["format"]
        out.append(column)
    return out


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
        if c.get("label") is not None and not isinstance(c["label"], str):
            return [f"El nombre a mostrar de '{c['name']}' debe ser texto"]
        if c.get("format") not in (None, "", "number", *COLUMN_FORMATS):
            return [f"Formato no válido para '{c['name']}': usa number, {', '.join(COLUMN_FORMATS)}"]
        names.add(c["name"])
    included = [c for c in columns if c.get("include", True)]
    if not included:
        return ["Incluye al menos una columna"]
    shown = set()
    for c in included:
        display = column_display_name(c)
        if display in shown:
            return [f"Dos columnas se llamarían «{display}»: cambia uno de los nombres a mostrar"]
        shown.add(display)
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
            sheet_name=source.sheet_name, tab_name=source.tab_name, name=source.name,
            first_row_headers=source.first_row_headers, columns=source.columns,
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
    refreshed = sheets.fetched_at(source)
    return {
        "id": source.id,
        "label": source.label,
        "name": source.name,
        "original_label": source.original_label,
        "kind": source.kind,
        "sheet_id": source.sheet_id,
        "gid": source.gid,
        "sheet_name": source.sheet_name,
        "tab_name": source.tab_name,
        "first_row_headers": source.first_row_headers,
        "refreshed_at": refreshed.isoformat() if refreshed else None,
        # Sin configuración (fuentes anteriores al selector) se usan todas: no se sabe cuántas.
        "columns_included": len(included) if source.columns else None,
        "columns_total": len(source.columns) if source.columns else None,
        # Con su árbol: el editor la arma como bloques.
        "calculated_fields": [{**f, "tree": formula_tree(f.get("formula", ""))}
                              for f in source.calculated_fields],
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
        # to_key: 5000.0 → «5000», como se ve en la hoja.
        values = [v for v in dict.fromkeys(str(to_key(x)).strip() for x in df[col].dropna()) if v]
        columns.append({"name": col, "type": types[col], "samples": values[:SOURCE_SAMPLES]})
    return columns


def _headers_param(request, default: bool = True) -> bool:
    """`?headers=0|1`: si la primera fila de la pestaña son los encabezados."""
    raw = request.GET.get("headers")
    return default if raw in (None, "") else raw not in ("0", "false")


def _read_sheet(request, sheet_id: str, gid: str, headers: bool):
    """La pestaña desde el caché o, con `?refresh=1` («Actualizar datos»), releída de Google."""
    read = sheets.refresh_sheet if request.GET.get("refresh") else sheets.get_sheet_dataframe
    return read(sheet_id, gid, headers=headers)


@require_http_methods(["GET"])
def source_columns(request, spreadsheet_id, gid):
    """Columnas de la pestaña con el tipo que se infiere y algunos valores de ejemplo."""
    try:
        df = _read_sheet(request, spreadsheet_id, gid, _headers_param(request))
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


def _as_columns(calculated: list[dict]) -> list[dict]:
    """Los campos calculados como columnas para comparar versiones: se siguen por su `id`, así
    un campo renombrado sigue siendo el mismo."""
    return [{"name": f"calc:{f['id']}", "label": f["name"], "type": "calc", "include": True}
            for f in calculated]


def _column_change(source, new_columns: list[dict],
                   new_calculated: list[dict] | None = None) -> tuple[list[dict], dict[str, str]]:
    """Qué pasa con los widgets de la fuente si sus columnas pasan a ser `new_columns`
    ([{name, label?, type, include}], las de la hoja nueva al reemplazarla) y sus campos
    calculados, `new_calculated`:
    - `impact`: los widgets que usan columnas o campos que se quitan, ya no están en la hoja o
      cambian de tipo ([{id, title, columns}]).
    - `renames`: nombre a mostrar viejo → nuevo de las columnas y campos que siguen, para que
      los widgets (y las fórmulas) los sigan."""
    if new_calculated is None:
        new_calculated = source.calculated_fields
    old = [c for c in source.columns if c.get("include", True)] + _as_columns(source.calculated_fields)
    old_by_display = {column_display_name(c): c for c in old}
    new_by_name = {c["name"]: c for c in [*new_columns, *_as_columns(new_calculated)]}
    available, retyped, renames = set(), set(), {}
    for display, column in old_by_display.items():
        new = new_by_name.get(column["name"])
        if not new or not new.get("include", True):
            continue
        available.add(display)
        if new.get("type") != column.get("type"):
            retyped.add(display)
        if column_display_name(new) != display:
            renames[display] = column_display_name(new)
    affected = impact(source.widgets.all(), set(old_by_display), available, retyped)
    return affected, renames


def _calculated_errors(fields) -> str | None:
    """La forma de `calculated_fields`: [{id, name, formula | tree, format}] (`tree`: el árbol del
    constructor de bloques; se escribe como `formula`). Las fórmulas se validan contra la hoja
    aparte (`_check_calculated`)."""
    if not isinstance(fields, list):
        return "'calculated_fields' debe ser una lista"
    ids, names = set(), set()
    for f in fields:
        if not isinstance(f, dict):
            return "Cada campo calculado debe ser un objeto"
        if not str(f.get("id") or "").strip():
            return "Cada campo calculado necesita un 'id'"
        name = str(f.get("name") or "").strip()
        if not name:
            return "Cada campo calculado necesita un nombre"
        if f.get("format", "number") not in FORMULA_FORMATS:
            return f"Formato no válido para «{name}»: usa {', '.join(FORMULA_FORMATS)}"
        if f["id"] in ids or name in names:
            return f"Hay dos campos calculados llamados «{name}»"
        if "tree" in f:
            try:
                formula_text(f["tree"])
            except FormulaError as e:
                return f"«{name}»: {e}"
        ids.add(f["id"])
        names.add(name)
    return None


def _clean_calculated(fields: list[dict]) -> list[dict]:
    """Ya validados (`_calculated_errors`): con `tree`, la fórmula es su texto."""
    return [{"id": str(f["id"]).strip(), "name": str(f["name"]).strip()[:120],
             "formula": formula_text(f["tree"]) if "tree" in f else str(f.get("formula") or "").strip(),
             "format": f.get("format") or "number"}
            for f in fields]


def _check_calculated(source, updates: dict) -> str | None:
    """Evalúa los campos calculados contra la hoja como quedaría: un error legible o None."""
    if not updates.get("calculated_fields"):
        return None
    sheet_id = updates.get("sheet_id", source.sheet_id)
    gid = updates.get("gid", source.gid)
    headers = updates.get("first_row_headers", source.first_row_headers)
    try:
        df = sheets.get_sheet_dataframe(sheet_id, gid, headers=headers)
    except SheetError as e:
        return str(e)
    df = sheets.apply_column_config(df, updates.get("columns", source.columns))
    try:
        apply_calculated_fields(df, updates["calculated_fields"], strict=True)
    except FormulaError as e:
        return str(e)
    return None


@csrf_exempt
@require_http_methods(["PUT", "DELETE"])
def source_detail(request, source_id):
    """
    PUT {columns, calculated_fields?, name?, first_row_headers?, sheet_id?, sheet_gid?,
    sheet_name?, tab_name?, dry_run?}: columnas (tipo, incluir, nombre a mostrar), campos
    calculados (validados contra la hoja), nombre de la fuente y, con
    `sheet_id`, otra hoja para la misma fuente (sus widgets la siguen usando). Los widgets se
    reescriben para seguir a las columnas renombradas. Con `dry_run` solo devuelve `{impact}`:
    los widgets que se romperían.

    DELETE: la borra (`?dry_run=1` → `{impact}`: todos sus widgets). Sus widgets quedan sin
    fuente y lo dicen al calcularse.
    """
    source = _owned_source(request, source_id)
    if not source:
        return _error("Fuente no encontrada", status=404)
    if request.method == "DELETE":
        if request.GET.get("dry_run"):
            return JsonResponse({"impact": [{"id": w.id, "title": w.title, "columns": []}
                                            for w in source.widgets.all()]})
        source.delete()
        return JsonResponse({"deleted": True})
    try:
        data = _json_body(request)
    except ValueError as e:
        return _error(str(e))

    if data.get("sheet_id"):
        updates, error = _source_fields(data)
        if error:
            return _error(error)
    else:
        columns = data.get("columns")
        errors = _columns_errors(columns)
        if errors:
            return _error(errors[0])
        updates = {"columns": _clean_columns(columns)}
        if "name" in data:
            updates["name"] = str(data.get("name") or "").strip()[:255]
        if "first_row_headers" in data:
            updates["first_row_headers"] = bool(data["first_row_headers"])
    if "calculated_fields" in data:
        error = _calculated_errors(data["calculated_fields"])
        if error:
            return _error(error)
        updates["calculated_fields"] = _clean_calculated(data["calculated_fields"])

    calculated = updates.get("calculated_fields", source.calculated_fields)
    affected, renames = _column_change(source, updates["columns"], calculated)
    if data.get("dry_run"):
        return JsonResponse({"impact": affected})
    # Las fórmulas siguen a las columnas y campos renombrados en esta misma edición.
    if renames and calculated:
        updates["calculated_fields"] = [{**f, "formula": rename_columns(f["formula"], renames)}
                                        for f in calculated]
    error = _check_calculated(source, updates)
    if error:
        return _error(error)
    for key, value in updates.items():
        setattr(source, key, value)
    source.save()
    rename_in_widgets(source.widgets.all(), renames)
    return JsonResponse({**_serialize_source(source), "impact": affected})


@require_http_methods(["GET"])
def source_saved_columns(request, source_id):
    """Las columnas de la hoja de la fuente para editarla: lo guardado (tipo, incluir, nombre a
    mostrar) manda; las columnas nuevas de la hoja entran incluidas y las que ya no están se
    descartan. `?headers=0|1` lee la pestaña con otra opción de encabezados; `?refresh=1`
    («Actualizar datos») la relee de Google. No guarda nada: los cambios de estructura se
    guardan con el PUT de la fuente."""
    source = _owned_source(request, source_id)
    if not source:
        return _error("Fuente no encontrada", status=404)
    headers = _headers_param(request, source.first_row_headers)
    try:
        df = _read_sheet(request, source.sheet_id, source.gid, headers)
    except SheetError as e:
        return _error(str(e), status=502)
    return JsonResponse({"columns": _merge_saved_columns(_sheet_columns(df), source.columns),
                         "rows": len(df), "first_row_headers": headers,
                         "source": _serialize_source(source)})


def _merge_saved_columns(sheet_columns: list[dict], saved: list[dict]) -> list[dict]:
    """Las columnas actuales de la hoja con lo guardado de cada una (por encabezado)."""
    by_name = {c["name"]: c for c in saved}
    out = []
    for column in sheet_columns:
        stored = by_name.get(column["name"])
        out.append({**column,
                    "type": stored["type"] if stored else column["type"],
                    "include": stored.get("include", True) if stored else True,
                    "label": (stored or {}).get("label", ""),
                    "format": (stored or {}).get("format", "")})
    return out


@csrf_exempt
@require_http_methods(["POST"])
def source_add_calculated(request, source_id):
    """
    Agrega campos calculados a la fuente: POST {fields: [{name, formula, format}]} (los que
    propone la IA al aplicar su propuesta). Un campo que ya existe con la misma fórmula se deja
    tal cual (aplicar otra vez la misma propuesta); con otra fórmula es un error.
    → la fuente serializada, o {error}.
    """
    source = _owned_source(request, source_id)
    if not source:
        return _error("Fuente no encontrada", status=404)
    try:
        data = _json_body(request)
    except ValueError as e:
        return _error(str(e))
    items = data.get("fields")
    if not isinstance(items, list) or not items:
        return _error("'fields' debe ser una lista con al menos un campo")
    existing = {f["name"]: f for f in source.calculated_fields}
    added = []
    for item in items:
        if not isinstance(item, dict):
            return _error("Cada campo calculado debe ser un objeto")
        name = str(item.get("name") or "").strip()
        formula = str(item.get("formula") or "").strip()
        if name in existing:
            if existing[name]["formula"] != formula:
                return _error(f"Ya hay un campo calculado «{name}» con otra fórmula")
            continue
        added.append({"id": f"cf_{uuid.uuid4().hex[:12]}", "name": name, "formula": formula,
                      "format": item.get("format") or "number"})
    calculated = [*source.calculated_fields, *added]
    error = _calculated_errors(calculated) or _check_calculated(source, {"calculated_fields": calculated})
    if error:
        return _error(error)
    source.calculated_fields = calculated
    source.save(update_fields=["calculated_fields"])
    return JsonResponse(_serialize_source(source))


FORMULA_PREVIEW_ROWS = 5


def _draft_frame(source, data: dict):
    """La hoja de la fuente como está en el editor, sin guardar: `first_row_headers`, `columns`
    (tipos y nombres a mostrar) y `calculated_fields` (los campos anteriores al que se arma).
    Lanza SheetError si no se puede leer."""
    headers = bool(data.get("first_row_headers", source.first_row_headers))
    df = sheets.get_sheet_dataframe(source.sheet_id, source.gid, headers=headers)
    columns = data.get("columns")
    valid_columns = isinstance(columns, list) and columns and not _columns_errors(columns)
    df = sheets.apply_column_config(df, _clean_columns(columns) if valid_columns else source.columns)
    previous = data.get("calculated_fields") or []
    if _calculated_errors(previous) is None:
        df = apply_calculated_fields(df, _clean_calculated(previous))
    return df


@csrf_exempt
@require_http_methods(["POST"])
def source_formula(request, source_id):
    """
    Vista previa de un campo calculado mientras se arma: POST {formula | tree, columns?,
    first_row_headers?, calculated_fields?} (lo que hay en el editor, sin guardar; los campos
    calculados son los anteriores a este) → {formula, kind: "row"|"aggregated", values: [...]}
    con la fórmula escrita y los primeros valores (por fila) o el total de la hoja (agregado), o
    {error} si la fórmula no vale. `tree` es el árbol del constructor de bloques.
    """
    source = _owned_source(request, source_id)
    if not source:
        return _error("Fuente no encontrada", status=404)
    try:
        data = _json_body(request)
    except ValueError as e:
        return _error(str(e))
    try:
        df = _draft_frame(source, data)
    except SheetError as e:
        return _error(str(e), status=502)
    try:
        text = formula_text(data["tree"]) if "tree" in data else str(data.get("formula") or "")
        formula = compile_formula(text, df.columns, aggregated_fields(df))
        if formula.aggregated:
            values = [to_python(formula.aggregate(df))]
        else:
            values = [to_python(v) for v in formula.evaluate_rows(df).head(FORMULA_PREVIEW_ROWS)]
    except FormulaError as e:
        return JsonResponse({"error": str(e)})
    return JsonResponse({"formula": formula.text, "kind": "aggregated" if formula.aggregated else "row",
                         "values": values})


@csrf_exempt
@require_http_methods(["POST"])
def source_formula_ai(request, source_id):
    """
    «Generar con IA» en el constructor de un campo calculado: POST {prompt, columns?,
    first_row_headers?, calculated_fields?} (lo mismo que la vista previa) → {formula, tree,
    name, kind} o {error} con el motivo si la IA no pudo armarla.
    """
    source = _owned_source(request, source_id)
    if not source:
        return _error("Fuente no encontrada", status=404)
    try:
        data = _json_body(request)
    except ValueError as e:
        return _error(str(e))
    prompt = str(data.get("prompt") or "").strip()
    if not prompt:
        return _error("Describe el cálculo que quieres.")
    try:
        df = _draft_frame(source, data)
    except SheetError as e:
        return _error(str(e), status=502)

    from sheets_reports.services.ai_formula import FormulaAIError, generate_formula
    try:
        return JsonResponse(generate_formula(prompt, df, aggregated_fields(df)))
    except FormulaAIError as e:
        return JsonResponse({"error": str(e)})


@csrf_exempt
@require_http_methods(["POST"])
def source_refresh(request, source_id):
    """«Actualizar» en la tabla de fuentes: relee la hoja de Google (pisa el caché) → la fuente
    serializada (con su `refreshed_at` nuevo) y su `status`, o {error, status} si no se pudo."""
    source = _owned_source(request, source_id)
    if not source:
        return _error("Fuente no encontrada", status=404)
    status = google_drive.tab_status(source.sheet_id, source.gid)
    try:
        sheets.refresh_sheet(source.sheet_id, source.gid, headers=source.first_row_headers)
    except SheetError as e:
        return JsonResponse({"error": str(e), "status": status}, status=502)
    return JsonResponse({**_serialize_source(source), "status": status})


@require_http_methods(["GET"])
def source_status(request, source_id):
    """Si la cuenta de servicio todavía llega a la pestaña de la fuente: {status: "ok" |
    "no_access" | "tab_missing" | null}. Lo pide la tabla de fuentes al abrirse."""
    source = _owned_source(request, source_id)
    if not source:
        return _error("Fuente no encontrada", status=404)
    return JsonResponse({"status": google_drive.tab_status(source.sheet_id, source.gid)})


@require_http_methods(["GET"])
def source_schema(request, source_id):
    """Columnas de la fuente para el panel de un widget (agrupables, numéricas, de tiempo y
    valores de ejemplo) y el manifiesto de widgets."""
    source = _owned_source(request, source_id)
    if not source:
        return _error("Fuente no encontrada", status=404)
    try:
        df = sheets.load_source(source)
    except SheetError as e:
        return _error(str(e), status=502)
    schema = {**get_sheet_schema(df), "dimension_fields": get_dimension_fields(df),
              "time_fields": time_fields(df),
              # Solo métricas, con agregación «auto»: no son columnas de la hoja.
              "aggregated_fields": [{"name": f.name, "format": f.format}
                                    for f in aggregated_fields(df).values()]}
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
    Chat con la IA del panel de un widget: la IA propone el `WidgetForm` (fields + style, y
    los `calculated_fields` nuevos que usan sus métricas) validado contra la hoja de la fuente, ajustando el borrador actual (`current`:
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
        # Los campos que la IA propone crear en la fuente: sus métricas los usan (agg "auto").
        "calculated_fields": proposal.get("calculated_fields", []),
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
