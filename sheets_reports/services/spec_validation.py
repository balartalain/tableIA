"""
DSL de widgets: construcción dinámica del JSON Schema de `data_spec`, validación completa
(jsonschema + reglas semánticas) y derivación de `view_spec`.

Lo comparten los dos caminos que producen specs: la IA (services/ai_spec.py) y el builder
manual (views.update_widget_spec). Los `enum` de columnas SIEMPRE salen de `schema`, que a su
vez sale de `df.columns` real de la hoja (services/sheets.get_sheet_schema) en el momento de la
llamada, nunca de una lista fija.

data_spec = {
  "source": gid,
  "dimensions": [col, ...],        # filas / eje X; [] en el KPI
  "pivots": [col, ...],            # columnas / series; [] si no aplica
  "filters": [Condition],          # filas que entran al widget (WHERE)
  "metrics": [Metric],             # unión discriminada por "type": agg | calc | grouped
  "having": [GroupCondition],      # grupos de la primera dimensión que se muestran
  "sort": {"by", "dir"} | null,
  "limit": {"n", "others"} | null, # Top N grupos (+ el resto como «Otros»)
  "trend_by": col | null,          # solo KPI: serie de la sparkline
}
"""
from jsonschema import Draft202012Validator

WIDGET_TYPES = ["kpi", "bar", "line", "donut", "table"]
DIMENSION_TYPES = ["bar", "line", "donut", "table"]

METRIC_TYPES = ["agg", "calc", "grouped"]
AGGS = ["sum", "avg", "count", "count_distinct", "min", "max", "median"]
NUMERIC_AGGS = ["sum", "avg", "min", "max", "median"]
# "Mostrar como" de Sheets: el valor, o su % del total de la fila, la columna o el general.
SHOW_AS = ["value", "pct_row", "pct_column", "pct_total"]
# Operaciones de una métrica calculada: left <op> right.
CALC_OPS = ["add", "sub", "mul", "div", "ratio_pct", "diff_pct"]
PERCENT_CALC_OPS = ["ratio_pct", "diff_pct"]
# Resultado de una métrica agrupada: resume los grupos que cumplen `having` en un escalar.
GROUP_RESULTS = ["count", "pct_groups", "sum", "avg", "min", "max", "top", "bottom"]
# Resultados que devuelven un grupo ({label, value}) en vez de un número.
RANKING_RESULTS = ["top", "bottom"]
# Resultados que no necesitan `value` (cuentan grupos).
COUNT_RESULTS = ["count", "pct_groups"]

FILTER_OPS = ["eq", "ne", "lt", "lte", "gt", "gte", "in", "not_in", "between", "contains", "is_empty", "not_empty"]
ORDER_OPS = ["lt", "lte", "gt", "gte"]
COMPARE_OPS = ["eq", "ne", "lt", "lte", "gt", "gte"]
LIST_OPS = ["in", "not_in"]
EMPTY_OPS = ["is_empty", "not_empty"]
# Valores de un filtro que se calculan al ejecutar: del reloj o de los datos de la columna.
RELATIVE_VALUES = ["current_year", "previous_year", "current_month", "max", "second_max", "min"]

AS_PATTERN = "^[a-z][a-z0-9_]{0,62}$"
MAX_METRICS = 5
MAX_KPI_METRICS = 4
MAX_FILTERS = 20
MAX_IN_VALUES = 200
MAX_HAVING = 5
MAX_INNER_METRICS = 3
MAX_LIMIT = 100

# Tablas dinámicas: filas y columnas anidadas. Los gráficos usan una sola de cada una.
MAX_DIMENSIONS = 3
MAX_PIVOTS = 2

PIVOT_MULTIMETRIC_MSG = "Con pivote solo se permite una métrica"
# Solo la tabla admite varias métricas con pivote (una subcolumna por métrica en cada valor).
PIVOT_MULTIMETRIC_TYPES = ["table"]

SCALAR = {"type": ["string", "number", "boolean"]}
REF = {"type": "string", "pattern": AS_PATTERN}
# Lado derecho de una comparación o de un cálculo: otra métrica (su `as`) o un número.
REF_OR_NUMBER = {"anyOf": [REF, {"type": "number"}]}


class SpecValidationError(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def _field_enum(fields: list[str]) -> dict:
    return {"enum": list(fields)}


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

def filter_schema(schema: dict) -> dict:
    """Una condición {field, op, value | relative}. Las reglas que dependen del operador
    (qué valor lleva) están en _condition_errors, con mensajes legibles."""
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["field", "op"],
        "properties": {
            "field": _field_enum(schema["all_fields"]),
            "op": {"enum": FILTER_OPS},
            "value": {"anyOf": [SCALAR, {"type": "array", "maxItems": MAX_IN_VALUES, "items": SCALAR}]},
            "relative": {"enum": RELATIVE_VALUES},
        },
    }


def _filters_schema(schema: dict) -> dict:
    return {"type": "array", "maxItems": MAX_FILTERS, "items": filter_schema(schema)}


def group_condition_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["left", "op", "right"],
        "properties": {"left": REF, "op": {"enum": COMPARE_OPS}, "right": REF_OR_NUMBER},
    }


def _having_schema() -> dict:
    return {"type": "array", "maxItems": MAX_HAVING, "items": group_condition_schema()}


def agg_metric_schema(schema: dict, with_filters: bool = True) -> dict:
    properties = {
        "type": {"enum": ["agg"]},
        "as": REF,
        "agg": {"enum": AGGS},
        "field": _field_enum(schema["all_fields"]),
        "show_as": {"enum": SHOW_AS},
    }
    if with_filters:
        properties["filters"] = _filters_schema(schema)
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["type", "as", "agg"],
        "properties": properties,
        "allOf": [
            {
                "$comment": "sum/avg/min/max/median solo sobre columnas numéricas; count_distinct acepta cualquier campo.",
                "if": {"properties": {"agg": {"enum": NUMERIC_AGGS}}},
                "then": {"properties": {"field": _field_enum(schema["numeric_fields"])}},
            }
        ],
    }


def calc_metric_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["type", "as", "op", "left", "right"],
        "properties": {
            "type": {"enum": ["calc"]},
            "as": REF,
            "op": {"enum": CALC_OPS},
            "left": REF,
            "right": REF_OR_NUMBER,
        },
    }


def grouped_metric_schema(schema: dict, for_ai: bool = False) -> dict:
    # A la IA se le dan métricas internas simples (agg sin filtros): menos estados para la
    # gramática de Gemini. El builder y la validación aceptan también cálculos.
    inner = ({"anyOf": [agg_metric_schema(schema, with_filters=False), calc_metric_schema()]} if for_ai
             else _union({"agg": agg_metric_schema(schema, with_filters=False), "calc": calc_metric_schema()}))
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["type", "as", "group_by", "inner", "result"],
        "properties": {
            "type": {"enum": ["grouped"]},
            "as": REF,
            "group_by": _field_enum(schema["all_fields"]),
            "filters": _filters_schema(schema),
            "inner": {"type": "array", "minItems": 1, "maxItems": MAX_INNER_METRICS, "items": inner},
            "having": _having_schema(),
            "result": {"enum": GROUP_RESULTS},
            "value": REF,
        },
    }


def _union(branches: dict) -> dict:
    """Unión discriminada por "type" con if/then: jsonschema solo reporta los errores de la
    rama que corresponde (un anyOf reportaría los de todas)."""
    return {
        "type": "object",
        "required": ["type"],
        "properties": {"type": {"enum": list(branches)}},
        "allOf": [
            {"if": {"properties": {"type": {"const": name}}, "required": ["type"]}, "then": branch}
            for name, branch in branches.items()
        ],
    }


def metric_schema(schema: dict, for_ai: bool = False) -> dict:
    branches = {
        "agg": agg_metric_schema(schema),
        "calc": calc_metric_schema(),
        "grouped": grouped_metric_schema(schema, for_ai),
    }
    if for_ai:
        # Gemini no soporta if/then: la unión va como anyOf (cada rama fija su "type").
        return {"anyOf": list(branches.values())}
    return _union(branches)


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


def limit_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["n"],
        "properties": {
            "n": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT},
            "others": {"type": "boolean"},
        },
    }


def build_data_spec_schema(schema: dict, source: str | None, for_ai: bool = False) -> dict:
    """
    JSON Schema de `data_spec`. Con `source=None` se omite la propiedad `source` (versión que
    se le da a la IA: el backend la completa). Sin $ref: todo va en línea para que el mismo
    dict sirva tanto a jsonschema como a la declaración de la tool de Gemini.
    """
    fields = schema["all_fields"]
    properties = {
        "dimensions": {"type": "array", "items": _field_enum(fields), "maxItems": MAX_DIMENSIONS},
        "pivots": {"type": "array", "items": _field_enum(fields), "maxItems": MAX_PIVOTS},
        "filters": _filters_schema(schema),
        "metrics": {"type": "array", "minItems": 1, "maxItems": MAX_METRICS, "items": metric_schema(schema, for_ai)},
        "having": _having_schema(),
        "sort": {"anyOf": [{"type": "null"}, sort_schema()]},
        "limit": {"anyOf": [{"type": "null"}, limit_schema()]},
        "trend_by": {"anyOf": [{"type": "null"}, _field_enum(fields)]},
    }
    required = ["dimensions", "pivots", "filters", "metrics", "having", "sort", "limit", "trend_by"]
    if source is not None:
        properties = {"source": {"const": source}, **properties}
        required = ["source", *required]
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": required,
        "properties": properties,
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
                    "pivots": {"maxItems": 0},
                    "metrics": {"maxItems": MAX_KPI_METRICS},
                }}}},
            },
            {
                "if": {"properties": {"widget_type": {"enum": DIMENSION_TYPES}}},
                "then": {"properties": {"data_spec": {"properties": {"dimensions": {"minItems": 1}}}}},
            },
            {
                "$comment": "Solo la tabla anida varias filas o columnas; los gráficos usan una de cada.",
                "if": {"properties": {"widget_type": {"enum": ["bar", "line", "donut"]}}},
                "then": {"properties": {"data_spec": {"properties": {
                    "dimensions": {"maxItems": 1},
                    "pivots": {"maxItems": 1},
                }}}},
            },
            {
                "$comment": "La dona reparte UNA métrica entre las categorías de la dimensión: sin pivote.",
                "if": {"properties": {"widget_type": {"const": "donut"}}},
                "then": {"properties": {"data_spec": {"properties": {
                    "pivots": {"maxItems": 0},
                    "metrics": {"maxItems": 1},
                }}}},
            },
        ],
    }


# ---------------------------------------------------------------------------
# Mensajes legibles
# ---------------------------------------------------------------------------

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
    is_column = path.endswith(("field", "group_by", "trend_by")) or "dimensions[" in path or "pivots[" in path
    if error.validator == "enum" and is_column:
        value = error.instance
        if value in schema["all_fields"] and value not in schema["numeric_fields"]:
            return (f"{path}: la columna '{value}' no es numérica; solo se puede usar con "
                    f"agg 'count'/'count_distinct' o en condiciones de igualdad.")
        return f"{path}: la columna '{value}' no existe en la hoja."
    if error.validator == "maxItems" and path.endswith("dimensions"):
        if error.validator_value == 0:
            return f"{path}: este tipo de widget no admite dimensión."
        if error.validator_value == 1:
            return f"{path}: solo se admite una dimensión (las tablas admiten hasta {MAX_DIMENSIONS})."
        return f"{path}: se admiten como máximo {error.validator_value} dimensiones."
    if error.validator == "minItems" and path.endswith("dimensions"):
        return f"{path}: se requiere una dimensión (campo por el que agrupar)."
    if error.validator == "maxItems" and path.endswith("pivots"):
        if error.validator_value == 0:
            return f"{path}: este tipo de widget no admite pivote."
        if error.validator_value == 1:
            return f"{path}: los gráficos admiten un solo pivote (las tablas hasta {MAX_PIVOTS})."
        return f"{path}: se admiten como máximo {error.validator_value} columnas de pivote."
    if error.validator == "maxItems" and path.endswith("metrics"):
        return f"{path}: se permiten como máximo {error.validator_value} métricas aquí."
    if error.validator == "pattern" and path.endswith(".as"):
        return f"{path}: '{error.instance}' debe ser snake_case en minúsculas (ej. 'total_ventas')."
    if error.validator == "additionalProperties":
        return f"{path}: {error.message}"
    return f"{path}: {error.message}"


def _leaf_errors(errors):
    """Baja por los errores compuestos (anyOf) hasta los errores concretos."""
    for e in errors:
        if e.context:
            # En un anyOf [null, X] con un valor no nulo, el error de la rama null no aporta.
            relevant = [c for c in e.context if not (c.validator == "type" and c.validator_value == "null")]
            yield from _leaf_errors(relevant or e.context)
        else:
            yield e


# ---------------------------------------------------------------------------
# Reglas semánticas
# ---------------------------------------------------------------------------

def _condition_errors(f: dict, schema: dict, path: str) -> list[str]:
    """Qué valor lleva cada operador; se asume que `f` ya pasó el schema."""
    op, field = f["op"], f["field"]
    has_value, has_relative = "value" in f, "relative" in f
    value = f.get("value")
    numeric = field in schema["numeric_fields"]
    if has_value and has_relative:
        return [f"{path}: usa 'value' o 'relative', no ambos."]
    if op in EMPTY_OPS:
        return [f"{path}: '{op}' no lleva valor."] if has_value or has_relative else []
    if op in LIST_OPS:
        if has_relative or not isinstance(value, list) or not value:
            return [f"{path}: '{op}' lleva una lista de valores en 'value'."]
        return []
    if op == "between":
        if not numeric:
            return [f"{path}: 'between' solo se usa con columnas numéricas y '{field}' no lo es."]
        if (has_relative or not isinstance(value, list) or len(value) != 2
                or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value)):
            return [f"{path}: 'between' lleva una lista de dos números [desde, hasta] en 'value'."]
        return []
    if op == "contains":
        if has_relative or not isinstance(value, str) or not value:
            return [f"{path}: 'contains' lleva un texto en 'value'."]
        return []
    # eq, ne y comparaciones de orden: un escalar o un valor relativo.
    if not has_value and not has_relative:
        return [f"{path}: falta 'value' (o 'relative')."]
    if isinstance(value, list):
        return [f"{path}: '{op}' lleva un único valor; para varios usa 'in'."]
    if op in ORDER_OPS:
        if not numeric:
            return [f"{path}: la columna '{field}' no es numérica; '{op}' solo compara números."]
        if has_value and (not isinstance(value, (int, float)) or isinstance(value, bool)):
            return [f"{path}: '{op}' compara contra un número."]
    return []


def condition_errors(filters: list, schema: dict, path: str = "filters") -> list[str]:
    """Valida una lista de condiciones (filtros del widget, de una métrica o del tablero)."""
    validator = Draft202012Validator({"type": "object", "properties": {"filters": _filters_schema(schema)}})
    errors = [_readable(e, schema).replace("filters", path, 1)
              for e in _leaf_errors(validator.iter_errors({"filters": filters}))]
    if errors:
        return list(dict.fromkeys(errors))
    out = []
    for i, f in enumerate(filters):
        out += _condition_errors(f, schema, f"{path}[{i}]")
    return out


def _metric_errors(metrics: list, schema: dict, path: str, scope: set[str] | None = None) -> list[str]:
    """Reglas de una lista de métricas: alias únicos, columnas según agg y referencias de
    los cálculos a métricas anteriores."""
    errors = []
    seen: dict[str, dict] = {}
    for i, m in enumerate(metrics):
        where = f"{path}[{i}]"
        name = m["as"]
        if name in seen:
            errors.append(f"{where}.as: el nombre '{name}' está repetido.")
        if m["type"] == "agg":
            if m["agg"] == "count" and "field" in m:
                errors.append(f"{where}: 'count' cuenta filas y no lleva 'field'.")
            elif m["agg"] != "count" and "field" not in m:
                errors.append(f"{where}: falta 'field' (la columna a resumir).")
            errors += condition_errors(m.get("filters") or [], schema, f"{where}.filters")
        elif m["type"] == "calc":
            for side in ("left", "right"):
                ref = m[side]
                if isinstance(ref, str):
                    if ref not in seen:
                        errors.append(f"{where}.{side}: '{ref}' no es una métrica anterior a esta.")
                    elif seen[ref]["type"] == "grouped" and seen[ref]["result"] in RANKING_RESULTS:
                        errors.append(f"{where}.{side}: '{ref}' devuelve un grupo, no un número.")
        elif m["type"] == "grouped":
            errors += _grouped_errors(m, schema, where)
        seen[name] = m
    return errors


def _grouped_errors(m: dict, schema: dict, where: str) -> list[str]:
    errors = _metric_errors(m["inner"], schema, f"{where}.inner")
    errors += condition_errors(m.get("filters") or [], schema, f"{where}.filters")
    inner = {x["as"] for x in m["inner"]}
    errors += _having_errors(m.get("having") or [], inner, f"{where}.having")
    if m["result"] in COUNT_RESULTS:
        if "value" in m:
            errors.append(f"{where}.value: '{m['result']}' cuenta grupos y no lleva 'value'.")
    elif m.get("value") not in inner:
        errors.append(f"{where}.value: '{m['result']}' necesita 'value' con una de sus métricas "
                      f"internas ({', '.join(sorted(inner))}).")
    return errors


def _having_errors(having: list, names: set[str], path: str) -> list[str]:
    errors = []
    for i, h in enumerate(having):
        for side in ("left", "right"):
            ref = h[side]
            if isinstance(ref, str) and ref not in names:
                errors.append(f"{path}[{i}].{side}: '{ref}' no es una de las métricas "
                              f"({', '.join(sorted(names)) or 'ninguna'}).")
    return errors


def _pivot_multimetric_error(widget_type: str, data_spec: dict) -> str | None:
    if widget_type in PIVOT_MULTIMETRIC_TYPES or not isinstance(data_spec, dict):
        return None
    pivots = data_spec.get("pivots")
    metrics = data_spec.get("metrics")
    if pivots and isinstance(pivots, list) and isinstance(metrics, list) and len(metrics) > 1:
        return (f"{PIVOT_MULTIMETRIC_MSG}. Elige entre desagregar por «{', '.join(map(str, pivots))}» "
                f"o mostrar varias métricas.")
    return None


def _semantic_errors(widget_type: str, data_spec: dict, schema: dict) -> list[str]:
    errors = []
    dimensions = data_spec["dimensions"]
    pivots = data_spec["pivots"]
    metrics = data_spec["metrics"]
    names = [m["as"] for m in metrics]

    errors += condition_errors(data_spec["filters"], schema)
    errors += _metric_errors(metrics, schema, "metrics")

    reserved = set(dimensions) | set(pivots)
    for n in names:
        if n in reserved:
            errors.append(f"metrics: el nombre '{n}' choca con el nombre de la dimensión o del pivote.")

    if len(set(dimensions)) < len(dimensions):
        errors.append("dimensions: no se puede repetir una columna.")
    if len(set(pivots)) < len(pivots):
        errors.append("pivots: no se puede repetir una columna.")
    if set(pivots) & set(dimensions):
        errors.append("pivots: no puede ser la misma columna que la dimensión.")
    if pivots and not dimensions:
        errors.append("pivots: con pivote hace falta una dimensión.")

    grouped = [m["as"] for m in metrics if m["type"] == "grouped"]
    if widget_type == "kpi":
        if data_spec["having"]:
            errors.append("having: el KPI no agrupa; usa una métrica «por grupo» (type 'grouped').")
        if data_spec["limit"]:
            errors.append("limit: el KPI no agrupa; usa una métrica «por grupo» con result 'top'.")
        if data_spec["sort"]:
            errors.append("sort: el KPI no se ordena.")
        if data_spec["trend_by"] and all(m["type"] == "grouped" for m in metrics):
            errors.append("trend_by: las métricas «por grupo» no tienen tendencia.")
    else:
        if grouped:
            errors.append(f"metrics: las métricas «por grupo» ({', '.join(grouped)}) son solo para el KPI; "
                          f"aquí el grupo ya es la dimensión (usa 'having' o 'limit').")
        if data_spec["trend_by"]:
            errors.append("trend_by: la tendencia es solo para el KPI.")
        errors += _having_errors(data_spec["having"], set(names), "having")

    sort = data_spec["sort"]
    if sort and sort["by"] not in set(dimensions) | set(names):
        allowed = ", ".join([*dimensions, *names])
        errors.append(f"sort.by: '{sort['by']}' no es válido; usa una de: {allowed}.")
    if data_spec["limit"] and not (sort and sort["by"] in names):
        errors.append("limit: el Top N necesita ordenar por una métrica (sort.by).")
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
        messages = [_readable(e, schema).removeprefix("data_spec.") for e in _leaf_errors(errors)]
        return list(dict.fromkeys(messages))
    return list(dict.fromkeys(_semantic_errors(widget_type, data_spec, schema)))


def assert_valid_widget_spec(widget_type: str, data_spec: dict, schema: dict, source: str | None) -> None:
    errors = validate_widget_spec(widget_type, data_spec, schema, source)
    if errors:
        raise SpecValidationError(errors)


# ---------------------------------------------------------------------------
# view_spec
# ---------------------------------------------------------------------------

def humanize(name: str) -> str:
    text = str(name).replace("_", " ").strip()
    return text[:1].upper() + text[1:]


def is_percent_metric(metric: dict) -> bool:
    """La métrica se muestra como porcentaje (el frontend le agrega "%")."""
    if metric["type"] == "agg":
        return metric.get("show_as", "value") != "value"
    if metric["type"] == "calc":
        return metric["op"] in PERCENT_CALC_OPS
    return metric["result"] == "pct_groups"


def _is_numeric_metric(metric: dict) -> bool:
    return not (metric["type"] == "grouped" and metric["result"] in RANKING_RESULTS)


STATUS_BASES = ["target_pct", "value"]
COMPARE_MODES = ["pct", "abs"]


def kpi_view(data_spec: dict, options: dict | None) -> dict:
    """
    Roles de las métricas de un KPI (solo presentación; nunca cambia el cálculo):
      primary          -> la métrica grande (por defecto, la primera)
      compare          -> métrica contra la que se muestra la variación ▲/▼
      compare_mode     -> "pct" (variación %) o "abs" (diferencia)
      target           -> meta: `as` de una métrica o un número (barra de progreso)
      higher_is_better -> si subir es bueno (colores de la variación y del semáforo)
      status           -> semáforo {"basis": "target_pct"|"value", "good": n, "warn": n}
    Las referencias a métricas que no existen (o no son números) se descartan.
    """
    options = options or {}
    metrics = {m["as"]: m for m in data_spec["metrics"]}
    names = list(metrics)
    numeric = [n for n in names if _is_numeric_metric(metrics[n])]

    primary = options.get("primary") if options.get("primary") in metrics else names[0]
    others = [n for n in numeric if n != primary]
    compare = options.get("compare") if options.get("compare") in others else None
    target = options.get("target")
    if isinstance(target, bool) or not (isinstance(target, (int, float)) or target in others):
        target = None
    view = {
        "primary": primary,
        "compare": compare,
        "compare_mode": options.get("compare_mode") if options.get("compare_mode") in COMPARE_MODES else "pct",
        "target": target,
        "higher_is_better": options.get("higher_is_better", True) is not False,
        "status": None,
    }
    status = options.get("status")
    if (isinstance(status, dict) and status.get("basis") in STATUS_BASES
            and all(isinstance(status.get(k), (int, float)) and not isinstance(status.get(k), bool)
                    for k in ("good", "warn"))
            and (status["basis"] != "target_pct" or target is not None)
            and _is_numeric_metric(metrics[primary])):
        view["status"] = {"basis": status["basis"], "good": status["good"], "warn": status["warn"]}
    return view


def build_view_spec(widget_type: str, data_spec: dict, options: dict | None = None) -> dict:
    """
    Deriva `view_spec` de `data_spec` + tipo. `options` admite:
      title   -> título de la tarjeta
      labels  -> {as|columna: "Texto legible"} para cabeceras, series y etiquetas del KPI
      stacked -> barras apiladas (solo visual, nunca cambia el cálculo)
      kpi     -> roles de las métricas del KPI (ver kpi_view)
      display -> preferencias puramente de UI del frontend (ancho de barra, paginación, ...)
    """
    options = options or {}
    labels = {k: v for k, v in (options.get("labels") or {}).items() if isinstance(v, str) and v.strip()}

    def label(name):
        return labels.get(name) or humanize(name)

    metric_names = [m["as"] for m in data_spec["metrics"]]
    dimension = data_spec["dimensions"][0] if data_spec["dimensions"] else None
    pivots = data_spec["pivots"]
    pivot = pivots[0] if pivots else None

    if widget_type == "kpi":
        view = {"widget": "kpi", **kpi_view(data_spec, options.get("kpi"))}
    elif widget_type in ("bar", "line"):
        view = {"widget": widget_type, "x": dimension, "seriesBy": pivot, "metrics": metric_names}
        if widget_type == "bar":
            # Apilar solo tiene sentido con series por pivote.
            view["stacked"] = bool(options.get("stacked", False)) and bool(pivot)
    elif widget_type == "donut":
        view = {"widget": "donut", "x": dimension, "metrics": metric_names}
    elif widget_type == "table":
        view = {"widget": "table"}
    else:
        raise ValueError(f"Tipo de widget desconocido: {widget_type}")

    view["percent"] = [m["as"] for m in data_spec["metrics"] if is_percent_metric(m)]
    view["title"] = (options.get("title") or "").strip() or _default_title(data_spec, label)
    view["labels"] = labels
    view["display"] = options.get("display") or {}
    return view


def _default_title(data_spec, label) -> str:
    metric = label(data_spec["metrics"][0]["as"])
    if data_spec["dimensions"]:
        return f"{metric} por {' y '.join(data_spec['dimensions'])}"
    return metric
