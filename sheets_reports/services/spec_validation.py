"""
DSL de widgets: construcción dinámica del JSON Schema de `data_spec`, validación completa
(jsonschema + reglas semánticas) y derivación de `view_spec`.

Lo comparten los dos caminos que producen specs: la IA (services/ai_spec.py) y el builder
manual (views.update_widget_spec). Los `enum` de columnas SIEMPRE salen de `schema`, que a su
vez sale de `df.columns` real de la hoja (services/sheets.get_sheet_schema) en el momento de la
llamada, nunca de una lista fija.
"""
from jsonschema import Draft202012Validator

WIDGET_TYPES = ["kpi", "bar", "line", "donut", "table"]
AGGS = ["sum", "avg", "count", "count_distinct", "min", "max", "median"]
NUMERIC_AGGS = ["sum", "avg", "min", "max", "median"]
# "Mostrar como" de Sheets: el valor, o su % del total de la fila, la columna o el general.
SHOW_AS = ["value", "pct_row", "pct_column", "pct_total"]
# Formato anterior de los porcentajes (agg pct_* + of): se sigue aceptando al leer un widget
# guardado, pero normalize_metric lo traduce a agg + show_as.
LEGACY_PERCENT_AGGS = {"pct_count": "count", "pct_sum": "sum"}
FILTER_OPS = ["eq", "ne", "lt", "lte", "gt", "gte", "in"]
ORDER_OPS = ["lt", "lte", "gt", "gte"]
AS_PATTERN = "^[a-z][a-z0-9_]{0,62}$"
MAX_METRICS = 5
MAX_FILTERS = 20
MAX_IN_VALUES = 200

# Tablas dinámicas: filas y columnas anidadas. Los gráficos usan una sola de cada una.
MAX_DIMENSIONS = 3
MAX_PIVOTS = 2

PIVOT_MULTIMETRIC_MSG = "Con pivote solo se permite una métrica"
# Solo la tabla admite varias métricas con pivote (una subcolumna por métrica en cada valor).
PIVOT_MULTIMETRIC_TYPES = ["table"]

SCALAR = {"type": ["string", "number", "boolean"]}


class SpecValidationError(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def _field_enum(fields: list[str]) -> dict:
    return {"enum": list(fields)}


def metric_schema(schema: dict) -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["field", "agg", "as"],
        "properties": {
            "field": _field_enum(schema["all_fields"]),
            "agg": {"enum": AGGS},
            "as": {"type": "string", "pattern": AS_PATTERN},
            "show_as": {"enum": SHOW_AS},
        },
        "allOf": [
            {
                "$comment": "sum/avg/min/max/median solo sobre columnas numéricas; count y count_distinct aceptan cualquier campo.",
                "if": {"properties": {"agg": {"enum": NUMERIC_AGGS}}},
                "then": {"properties": {"field": _field_enum(schema["numeric_fields"])}},
            }
        ],
    }


def filter_schema(schema: dict) -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["field", "op", "value"],
        "properties": {
            "field": _field_enum(schema["all_fields"]),
            "op": {"enum": FILTER_OPS},
            "value": True,
        },
        "allOf": [
            {
                "if": {"properties": {"op": {"const": "in"}}},
                "then": {"properties": {"value": {
                    "type": "array", "minItems": 1, "maxItems": MAX_IN_VALUES, "items": SCALAR,
                }}},
            },
            {
                "if": {"properties": {"op": {"enum": ["eq", "ne"]}}},
                "then": {"properties": {"value": SCALAR}},
            },
            {
                "$comment": "Comparaciones de orden solo sobre columnas numéricas y con valor numérico.",
                "if": {"properties": {"op": {"enum": ORDER_OPS}}},
                "then": {"properties": {
                    "field": _field_enum(schema["numeric_fields"]),
                    "value": {"type": "number"},
                }},
            },
        ],
    }


def sort_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["by", "dir"],
        "properties": {
            "by": {"type": "string"},
            "dir": {"enum": ["asc", "desc"]},
        },
    }


def build_data_spec_schema(schema: dict, source: str | None) -> dict:
    """
    JSON Schema de `data_spec`. Con `source=None` se omite la propiedad `source` (versión que
    se le da a la IA: el backend la completa). Sin $ref: todo va en línea para que el mismo
    dict sirva tanto a jsonschema como a la declaración de la tool de Gemini.
    """
    fields = schema["all_fields"]
    properties = {
        "dimensions": {"type": "array", "items": _field_enum(fields), "minItems": 0, "maxItems": MAX_DIMENSIONS},
        # Una columna, o (solo tablas) una lista de columnas anidadas.
        "pivot": {"anyOf": [
            {"type": "null"},
            _field_enum(fields),
            {"type": "array", "items": _field_enum(fields), "minItems": 1, "maxItems": MAX_PIVOTS},
        ]},
        "metrics": {"type": "array", "minItems": 1, "maxItems": MAX_METRICS, "items": metric_schema(schema)},
        "filters": {"type": "array", "maxItems": MAX_FILTERS, "items": filter_schema(schema)},
        "sort": {"anyOf": [{"type": "null"}, sort_schema()]},
    }
    required = ["dimensions", "pivot", "metrics", "filters", "sort"]
    if source is not None:
        properties = {"source": {"const": source}, **properties}
        required = ["source", *required]
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": required,
        "properties": properties,
        "allOf": [
            {
                "$comment": "Con pivote hace falta una dimensión (el límite de métricas depende del widget).",
                "if": {"properties": {"pivot": {"type": ["string", "array"]}}, "required": ["pivot"]},
                "then": {"properties": {"dimensions": {"minItems": 1}}},
            }
        ],
    }


def build_envelope_schema(schema: dict, source: str | None) -> dict:
    """Schema de `{widget_type, data_spec}`: agrega las reglas que dependen del tipo de widget."""
    data_spec = build_data_spec_schema(schema, source)
    data_spec.pop("$schema")
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": ["widget_type", "data_spec"],
        "properties": {
            "widget_type": {"enum": WIDGET_TYPES},
            "data_spec": data_spec,
        },
        "allOf": [
            {
                "if": {"properties": {"widget_type": {"const": "kpi"}}},
                "then": {"properties": {"data_spec": {"properties": {
                    "dimensions": {"maxItems": 0},
                    "pivot": {"type": "null"},
                    "metrics": {"maxItems": 1},
                }}}},
            },
            {
                "if": {"properties": {"widget_type": {"enum": ["bar", "line", "donut", "table"]}}},
                "then": {"properties": {"data_spec": {"properties": {"dimensions": {"minItems": 1}}}}},
            },
            {
                "$comment": "Solo la tabla anida varias filas o columnas; los gráficos usan una de cada.",
                "if": {"properties": {"widget_type": {"enum": ["bar", "line", "donut"]}}},
                "then": {"properties": {"data_spec": {"properties": {
                    "dimensions": {"maxItems": 1},
                    "pivot": {"type": ["null", "string"]},
                }}}},
            },
            {
                "$comment": "La dona reparte UNA métrica entre las categorías de la dimensión: sin pivote.",
                "if": {"properties": {"widget_type": {"const": "donut"}}},
                "then": {"properties": {"data_spec": {"properties": {
                    "pivot": {"type": "null"},
                    "metrics": {"maxItems": 1},
                }}}},
            },
        ],
    }


def _path(error) -> str:
    parts = []
    for p in error.absolute_path:
        if isinstance(p, int):
            parts[-1] = f"{parts[-1]}[{p}]" if parts else f"[{p}]"
        else:
            parts.append(str(p))
    return ".".join(parts) or "spec"


def _readable(error, schema: dict) -> str:
    """Traduce un error de jsonschema a un mensaje entendible por el usuario (y por la IA en
    el reintento)."""
    path = _path(error)
    is_column = path.endswith(("field", "pivot")) or "dimensions[" in path or "pivot[" in path
    if error.validator == "enum" and is_column:
        value = error.instance
        if value in schema["all_fields"] and value not in schema["numeric_fields"]:
            return (f"{path}: la columna '{value}' no es numérica; solo se puede usar con "
                    f"agg 'count'/'count_distinct' o en filtros eq/ne/in.")
        return f"{path}: la columna '{value}' no existe en la hoja."
    if error.validator == "maxItems" and path.endswith("dimensions"):
        if error.validator_value == 0:
            return f"{path}: este tipo de widget no admite dimensión."
        if error.validator_value == 1:
            return f"{path}: solo se admite una dimensión (las tablas admiten hasta {MAX_DIMENSIONS})."
        return f"{path}: se admiten como máximo {error.validator_value} dimensiones."
    if error.validator == "minItems" and path.endswith("dimensions"):
        return f"{path}: se requiere una dimensión (campo por el que agrupar)."
    if error.validator == "type" and error.validator_value == "null" and path.endswith("pivot"):
        return f"{path}: este tipo de widget no admite pivote."
    if error.validator == "type" and error.validator_value == ["null", "string"] and path.endswith("pivot"):
        return f"{path}: los gráficos admiten un solo pivote (las tablas hasta {MAX_PIVOTS})."
    if error.validator == "maxItems" and path.endswith("pivot"):
        return f"{path}: se admiten como máximo {error.validator_value} columnas de pivote."
    if error.validator == "maxItems" and path.endswith("metrics"):
        return f"{path}: se permiten como máximo {error.validator_value} métricas aquí."
    if error.validator == "pattern" and path.endswith(".as"):
        return f"{path}: '{error.instance}' debe ser snake_case en minúsculas (ej. 'total_ventas')."
    return f"{path}: {error.message}"


def _pivot_multimetric_error(widget_type: str, data_spec: dict) -> str | None:
    if widget_type in PIVOT_MULTIMETRIC_TYPES:
        return None
    pivot = ", ".join(pivots_of(data_spec)) if isinstance(data_spec, dict) else None
    metrics = data_spec.get("metrics") if isinstance(data_spec, dict) else None
    if pivot and isinstance(metrics, list) and len(metrics) > 1:
        return (f"{PIVOT_MULTIMETRIC_MSG}. Elige entre desagregar por «{pivot}» "
                f"o mostrar varias métricas.")
    return None


def _semantic_errors(data_spec: dict) -> list[str]:
    errors = []
    dimensions = data_spec["dimensions"]
    pivots = pivots_of(data_spec)
    names = [m["as"] for m in data_spec["metrics"]]

    duplicated = sorted({n for n in names if names.count(n) > 1})
    if duplicated:
        errors.append(f"metrics: nombres 'as' repetidos: {', '.join(duplicated)}.")

    reserved = set(dimensions) | set(pivots)
    for n in names:
        if n in reserved:
            errors.append(f"metrics: el nombre '{n}' choca con el nombre de la dimensión o del pivote.")

    if len(set(dimensions)) < len(dimensions):
        errors.append("dimensions: no se puede repetir una columna.")
    if len(set(pivots)) < len(pivots):
        errors.append("pivot: no se puede repetir una columna.")
    if set(pivots) & set(dimensions):
        errors.append("pivot: no puede ser la misma columna que la dimensión.")

    sort = data_spec["sort"]
    if sort and sort["by"] not in set(dimensions) | set(names):
        allowed = ", ".join([*dimensions, *names])
        errors.append(f"sort.by: '{sort['by']}' no es válido; usa una de: {allowed}.")
    return errors


def validate_widget_spec(widget_type: str, data_spec: dict, schema: dict, source: str | None) -> list[str]:
    """
    Valida `data_spec` para un `widget_type` contra el schema construido desde la hoja real.
    Retorna la lista de errores legibles (vacía si es válido). Nunca lanza.
    """
    friendly = _pivot_multimetric_error(widget_type, data_spec)
    if friendly:
        return [friendly]

    validator = Draft202012Validator(build_envelope_schema(schema, source))
    errors = sorted(
        validator.iter_errors({"widget_type": widget_type, "data_spec": data_spec}),
        key=lambda e: list(e.absolute_path),
    )
    if errors:
        # Los if/then generan un error por rama; deduplicamos mensajes.
        return list(dict.fromkeys(_readable(e, schema) for e in _leaf_errors(errors)))
    return _semantic_errors(data_spec)


def _leaf_errors(errors):
    """Baja por los errores compuestos (anyOf) hasta los errores concretos."""
    for e in errors:
        if e.context:
            # En un anyOf [null, X] con un valor no nulo, el error de la rama null no aporta.
            relevant = [c for c in e.context if not (c.validator == "type" and c.validator_value == "null")]
            yield from _leaf_errors(relevant or e.context)
        else:
            yield e


def assert_valid_widget_spec(widget_type: str, data_spec: dict, schema: dict, source: str | None) -> None:
    errors = validate_widget_spec(widget_type, data_spec, schema, source)
    if errors:
        raise SpecValidationError(errors)


def pivots_of(data_spec: dict) -> list[str]:
    """`pivot` como lista: null -> [], "mes" -> ["mes"], ["anio", "mes"] -> igual."""
    pivot = data_spec.get("pivot")
    if not pivot:
        return []
    return [pivot] if isinstance(pivot, str) else list(pivot)


def normalize_metric(metric: dict, data_spec: dict) -> dict:
    """Métrica con `show_as` explícito, traduciendo el formato anterior de porcentajes
    (agg pct_count/pct_sum + of) al actual: el % era por fila con pivote, sobre el total con
    of="total", y sin pivote cada grupo sobre el total de los grupos."""
    metric = dict(metric)
    legacy_of = metric.pop("of", None)
    if metric["agg"] in LEGACY_PERCENT_AGGS:
        metric["agg"] = LEGACY_PERCENT_AGGS[metric["agg"]]
        if legacy_of == "total" or not data_spec.get("dimensions"):
            metric["show_as"] = "pct_total"
        else:
            metric["show_as"] = "pct_row" if data_spec.get("pivot") else "pct_column"
    metric.setdefault("show_as", "value")
    return metric


def humanize(name: str) -> str:
    text = str(name).replace("_", " ").strip()
    return text[:1].upper() + text[1:]


def build_view_spec(widget_type: str, data_spec: dict, options: dict | None = None) -> dict:
    """
    Deriva `view_spec` de `data_spec` + tipo. `options` admite:
      title   -> título de la tarjeta
      labels  -> {as|columna: "Texto legible"} para cabeceras, series y etiqueta del KPI
      stacked -> barras apiladas (solo visual, nunca cambia el cálculo)
      display -> preferencias puramente de UI del frontend (ancho de barra, paginación, ...)
    """
    options = options or {}
    labels = {k: v for k, v in (options.get("labels") or {}).items() if isinstance(v, str) and v.strip()}

    def label(name):
        return labels.get(name) or humanize(name)

    metric_names = [m["as"] for m in data_spec["metrics"]]
    dimension = data_spec["dimensions"][0] if data_spec["dimensions"] else None
    pivot = data_spec["pivot"]

    if widget_type == "kpi":
        view = {"widget": "kpi", "metric": metric_names[0], "label": label(metric_names[0])}
    elif widget_type in ("bar", "line"):
        view = {"widget": widget_type, "x": dimension, "seriesBy": pivot}
        if len(metric_names) == 1:
            view["metric"] = metric_names[0]
        else:
            view["metrics"] = metric_names
        if widget_type == "bar":
            # Apilar solo tiene sentido con series por pivote.
            view["stacked"] = bool(options.get("stacked", False)) and bool(pivot)
    elif widget_type == "donut":
        view = {"widget": "donut", "x": dimension, "metric": metric_names[0]}
    elif widget_type == "table":
        columns = [{"header": label(d), "field": d} for d in data_spec["dimensions"]]
        if pivot:
            # Los valores del pivote solo se conocen al ejecutar: compile_view expande
            # `pivotOf` en `children` en cada render.
            columns.append({"header": label(metric_names[0]), "pivotOf": metric_names[0]})
        else:
            columns += [{"header": label(n), "field": n} for n in metric_names]
        view = {"widget": "table", "columns": columns}
    else:
        raise ValueError(f"Tipo de widget desconocido: {widget_type}")

    # Métricas que son porcentajes: el frontend les agrega el sufijo "%".
    view["percent"] = [
        m["as"] for m in data_spec["metrics"] if normalize_metric(m, data_spec)["show_as"] != "value"
    ]
    view["title"] = (options.get("title") or "").strip() or _default_title(widget_type, data_spec, label)
    view["labels"] = labels
    view["display"] = options.get("display") or {}
    return view


def _default_title(widget_type, data_spec, label) -> str:
    metric = label(data_spec["metrics"][0]["as"])
    if data_spec["dimensions"]:
        return f"{metric} por {' y '.join(data_spec['dimensions'])}"
    return metric
