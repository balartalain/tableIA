"""
Generación del `WidgetForm` (fields + style) a partir de un prompt en lenguaje natural, vía
Gemini con function calling forzado. La IA NUNCA calcula números ni toca los datos: solo decide
QUÉ calcular, devolviendo un JSON validado contra un schema construido en cada llamada desde
las columnas reales de la hoja y el `style_schema` del widget. Nada de lo que devuelve se
ejecuta tal cual.
"""
import copy
import json
import logging
import re

from django.conf import settings
from google import genai
from google.genai import types

from sheets_reports.engine.steps.filter import condition_errors
from sheets_reports.engine.context import SheetContext
from sheets_reports.utils.validation import MAX_IN_VALUES
from sheets_reports.engine import AGGREGATIONS
from sheets_reports.widgets import WIDGETS
from sheets_reports.widgets.presentation import AGG_LABELS
from sheets_reports.widgets.schemas import WidgetForm

logger = logging.getLogger(__name__)
audit_logger = logging.getLogger("tableia.ai_audit")

GEMINI_TIMEOUT_MS = 120_000
CREATE_TOOL = "create_widget"
REJECT_TOOL = "reject_request"

# Claves de JSON Schema que se quitan de la declaración de la tool: condicionales y límites que
# Gemini no soporta en tools (o que la gramática de decodificación rechaza con "too many states").
# Las reglas que expresan se siguen aplicando: la validación real es la nuestra, sobre el JSON
# completo. Los `enum` de columnas SÍ se conservan: son los que impiden que la IA invente columnas.
_GEMINI_UNSUPPORTED_KEYS = {
    "allOf", "if", "then", "not", "$comment", "$schema", "maxItems", "minItems",
    "pattern", "maxLength", "minimum", "maximum", "default",
}

# Métricas calculadas sobre columnas ya agregadas.
WINDOW_TYPES = ("percent_of_total", "percent_of_row", "running_total", "pct_change")
AGG_LABELS_DESC = ", ".join(f"{k}={v.lower()}" for k, v in sorted(AGG_LABELS.items()))
NUMERIC_AGGS = {"sum", "avg", "median", "min", "max", "std"}
ALIAS_RE = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
EXPR_RE = re.compile(r"^[A-Za-z0-9_\s+\-*/().,%]+$")

MAX_LIMIT = 500


class SpecGenerationError(Exception):
    """Error legible para el usuario: la IA no produjo un WidgetForm válido."""


CORE_PROMPT = """\
Eres el asistente de un constructor de tableros de reportes sobre una hoja de cálculo. El
usuario describe en español un widget y tú respondes SIEMPRE llamando a una función:
- `create_widget` con el formulario del widget, o
- `reject_request` con un motivo breve y amable si el pedido no se puede representar
  (ej. pide una columna que no existe, o algo que no es un widget).

Tú NO calculas nada ni ves los datos: solo describes QUÉ calcular. El backend ejecuta la
consulta.

## fields (los datos)
- dimensions: columnas por las que agrupar (categorías del eje X / filas de la tabla).
- columns: SOLO la tabla de datos: columnas de la hoja a mostrar tal cual, en orden, cada una
  como {"field": "mes", "label": "Mes"}; `label` es opcional (nombre a mostrar en la cabecera,
  sin él se muestra la columna). La tabla NO agrupa ni resume: no lleva dimensions ni metrics.
- pivots: columnas para desagregar además de la dimensión (columnas de una tabla dinámica,
  series de un gráfico). [] si no aplica.
- filters: condiciones sobre las FILAS que entran al widget. [] si no hay.
- trend_by: columna de la mini tendencia (sparkline) bajo el número del KPI (ej. "mes");
  solo en widgets con tendencia. Omitir si no aplica.
- metrics: lista de métricas, en orden. Cada una lleva un `alias` único en snake_case que es el
  nombre de la columna con la que se calcula (ej. "total_ventas") y, si hace falta un texto más
  claro para la persona que mira el widget, un `label` (nombre a mostrar, ej. "Costos totales";
  sin label se muestra el agg en español con la columna, ej. «Promedio Ventas»):
  - {"agg", "field", "alias"}: resume una columna.
    - agg "count": "cuántos", "cantidad de". Cuenta filas y NO lleva field.
    - agg "count_distinct": "cuántos distintos" de una columna (cualquier tipo).
    - agg "sum"/"avg"/"median"/"min"/"max"/"std": SOLO sobre columnas numéricas.
    - filters (opcional): condiciones SOLO para esa métrica. Sirven para poner en el mismo
      widget "ventas 2026" y "ventas 2025", o "ventas de Hogar" junto al total.
    - window (opcional): {"type": "percent_of_total" | "percent_of_row" | "running_total" |
      "pct_change"} sobre esa métrica ya agregada. "participación", "qué % representa cada...",
      "acumulado", "variación respecto al anterior".
  - {"type": "formula", "alias", "expression"}: cálculo entre columnas YA agregadas, usando sus
    alias (ej. margen = "total_ganancia / total_ventas"). El orden de la lista decide el orden
    de las columnas; el cálculo se evalúa después de agregar.
- sort_by: columna u alias por el que ordenar, con "-" delante para descendente (ej.
  "-total_ventas"). Null si no importa.
- limit: máximo de filas/grupos a mostrar ("top 5" → 5). Null si no aplica.

## style (la apariencia)
Diccionario plano con SOLO las claves del widget. Cada control tiene un tipo fijo:
`text` (texto), `number` (número), `checkbox` (booleano), `select` (uno de sus `options`).
Los select marcados como «alias de una de las métricas» (los roles del KPI: `primary`,
`compare`, `targetMetric`) llevan el `alias` de una de las métricas del propio widget, o ""
para la opción por defecto. `targetMetric` además acepta "fixed" para una meta de valor
fijo (el número va en `target`).
No inventes claves: las que no están en el schema no existen. Puedes omitir `style` si no hace
falta cambiar nada (usa los valores por defecto).

## Condiciones (filters)
{"field", "op", "value"} o {"field", "op", "relative"}:
- op "eq"/"ne": igual/distinto de un valor. "lt"/"lte"/"gt"/"gte": comparación numérica
  (solo columnas numéricas). "in"/"not_in": lista de valores. "between": [desde, hasta]
  numérico. "contains": el texto contiene "value". "is_empty"/"not_empty": sin valor.
- relative (en vez de value) para valores que dependen de la fecha o de los datos:
  "current_year", "previous_year", "current_month", "max" (el último valor de la columna),
  "second_max", "min". Úsalo para "este año", "el último mes" en vez de fijar un número.
Usa los valores de ejemplo de las columnas para escribir el valor exacto.
"""

PIVOT_PROMPT = """\
Con pivots, los gráficos permiten UNA métrica; si el usuario pide varias y un cruce en un
gráfico, incluye todo tal como lo pidió: el sistema te pedirá que corrijas la propuesta."""

TITLE_PROMPT = """\
- title: título corto y claro para la tarjeta, en español."""


def capabilities_text(widget) -> str:
    """Qué admite un widget, en una línea, desde su `capabilities` declarativo."""
    caps = widget.capabilities or {}
    parts = []
    columns = caps.get("columns")
    if columns:
        low, high = columns
        if low == high:
            parts.append(f"columnas {low}")
        else:
            parts.append(f"columnas de {low} a {high}")
    for key, label in (("dimensions", "dimensiones"), ("pivots", "pivotes"), ("metrics", "métricas")):
        rng = caps.get(key)
        if not rng:
            parts.append(f"sin {label}")
        else:
            low, high = rng
            if low == 0 and high == 0:
                parts.append(f"sin {label}")
            elif low == high:
                parts.append(f"{label} {low}")
            else:
                parts.append(f"{label} de {low} a {high}")
    for key, label in (("sort", "orden"), ("limit", "límite"), ("filters", "filtros")):
        if caps.get(key):
            parts.append(label)
        else:
            parts.append(f"sin {label}")
    if caps.get("trend"):
        parts.append("tendencia")
    return ", ".join(parts)


def _style_schema_docs(widget) -> list[str]:
    """Una línea por control de estilo: clave, tipo y opciones."""
    lines = []
    for control in widget.style_schema or []:
        kind = control.get("type")
        if control.get("options_from") == "metrics":
            detail = (f"select con el alias de una de las métricas del widget "
                      f"(o vacío: {control.get('label', control['key'])})")
        elif kind == "choice":
            options = ", ".join(f'"{o["value"]}"' for o in control.get("options", []))
            detail = f"select de {options}"
        else:
            detail = f"{kind} ({control.get('label', control['key'])})"
        lines.append(f"- style.{control['key']}: {detail}")
    return lines


def _widgets_for(widget_type: str | None) -> list:
    if widget_type:
        return [WIDGETS.get(widget_type)]
    return [w for w in WIDGETS if w.ai_enabled]


def build_system_prompt(widgets=None) -> str:
    """El prompt de la IA: lo del core más lo que declara cada widget que la IA puede proponer
    (cuándo usarlo, qué admite, sus controles de estilo y sus ejemplos)."""
    widgets = list(widgets) if widgets is not None else [w for w in WIDGETS if w.ai_enabled]
    types_section = [
        f"- {w.key}: {w.ai_doc + ' ' if w.ai_doc else ''}Admite: {capabilities_text(w)}." for w in widgets
    ]
    style_docs = []
    for widget in widgets:
        if widget.style_schema:
            style_docs.append(f"- {widget.key}:")
            style_docs += [f"  {line}" for line in _style_schema_docs(widget)]
    examples = [
        f'Prompt: "{prompt}"\ncreate_widget({json.dumps(args, ensure_ascii=False)})'
        for w in widgets for prompt, args in w.ai_examples
    ]
    return "\n".join([
        CORE_PROMPT,
        "## Tipos de widget (si no viene fijado)",
        *types_section,
        PIVOT_PROMPT,
        TITLE_PROMPT,
        "",
        "## style de cada tipo",
        *style_docs,
        "",
        "## Ejemplos (columnas ilustrativas; usa SOLO las columnas reales de la hoja)",
        "",
        "\n\n".join(examples),
    ]) + "\n"


def gemini_client(api_key: str) -> genai.Client:
    return genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(
            timeout=GEMINI_TIMEOUT_MS,
            retry_options=types.HttpRetryOptions(attempts=2),
        ),
    )


def _to_gemini_schema(node):
    """Adapta el JSON Schema de validación al subconjunto que acepta la declaración de tools."""
    if isinstance(node, list):
        return [_to_gemini_schema(n) for n in node]
    if not isinstance(node, dict):
        return node
    out = {}
    for key, value in node.items():
        if key in _GEMINI_UNSUPPORTED_KEYS:
            continue
        if key == "properties":
            out[key] = {
                name: (_scalar_or_list_schema() if sub is True else _to_gemini_schema(sub))
                for name, sub in value.items()
            }
        elif key == "type" and isinstance(value, list):
            return {"anyOf": [{"type": t} for t in value]}
        else:
            out[key] = _to_gemini_schema(value)
    return out


def _scalar_or_list_schema():
    scalar = [{"type": "string"}, {"type": "number"}, {"type": "boolean"}]
    return {"anyOf": [*scalar, {"type": "array", "items": {"anyOf": scalar}}]}


def _style_schema(style_schema: list[dict]) -> tuple[dict, list[str]]:
    """Schema de `style` desde los controles declarados, más sus claves obligatorias."""
    properties: dict = {}
    for control in style_schema or []:
        kind = control.get("type")
        if control.get("options_from") == "metrics":
            # El valor es un alias que la propia propuesta define: no puede vivir en un enum.
            properties[control["key"]] = {
                "type": "string",
                "description": (f"{control.get('label', control['key'])}: alias de una de las "
                                f"métricas del widget (vacío = la opción por defecto)."),
            }
        elif kind == "choice":
            properties[control["key"]] = {
                "enum": [o["value"] for o in control.get("options", [])],
                "description": control.get("label", control["key"]),
            }
        elif kind == "number":
            properties[control["key"]] = {"type": "number", "description": control.get("label", control["key"])}
        elif kind == "boolean":
            properties[control["key"]] = {"type": "boolean", "description": control.get("label", control["key"])}
        else:
            properties[control["key"]] = {"type": "string", "description": control.get("label", control["key"])}
    return properties, []


def build_tool_parameters(ctx: SheetContext, widget_type: str | None) -> dict:
    """Schema de parámetros de `create_widget`, construido en cada llamada desde la hoja real
    (enum de columnas), los registros de widgets (capabilities + style_schema) y el motor
    (agregaciones y ventanas disponibles)."""
    widgets = [w for w in _widgets_for(widget_type) if w]
    metric_properties = {
        "agg": {"enum": sorted(set(AGGREGATIONS) - {"mean"}),
                "description": "Agregación: " + AGG_LABELS_DESC},
        "field": {"type": "string", "enum": list(ctx.fields),
                  "description": "Columna de la hoja a agregar. Omitir solo en agg=count."},
        "alias": {"type": "string",
                  "description": "Nombre único en snake_case de la columna resultante (ej. total_ventas)."},
        "label": {"type": "string", "maxLength": 80,
                  "description": "Nombre a mostrar de la métrica en el widget (ej. 'Costos totales'); "
                                 "vacío o ausente = el alias en texto."},
        "type": {"enum": ["formula"],
                 "description": "Solo para métricas calculadas: obliga a usar expression en vez de agg."},
        "expression": {"type": "string",
                       "description": "Fórmula entre alias ya agregados (ej. 'total_ganancia / total_ventas')."},
        "filters": {**condition_schema_for(ctx),
                    "description": "Condiciones SOLO para esta métrica."},
        "window": {
            "type": "object",
            "additionalProperties": False,
            "required": ["type"],
            "properties": {"type": {"enum": list(WINDOW_TYPES),
                                    "description": "Cálculo sobre la métrica ya agregada."}},
            "description": "Transformación sobre la métrica (porcentaje del total, acumulado...).",
        },
    }
    fields_properties = {
        "dimensions": {"type": "array", "items": {"type": "string", "enum": list(ctx.fields)},
                       "description": "Columnas para agrupar / mostrar como filas."},
        "pivots": {"type": "array", "items": {"type": "string", "enum": list(ctx.fields)},
                   "description": "Columnas para desagregar (series / columnas cruzadas)."},
        "columns": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                               "required": ["field"],
                                               "properties": {
                                                   "field": {"type": "string", "enum": list(ctx.fields),
                                                             "description": "Columna de la hoja a mostrar."},
                                                   "label": {"type": "string",
                                                             "description": "Nombre a mostrar en la cabecera; "
                                                                            "vacío o ausente = la columna."},
                                               }},
                    "description": "Columnas a mostrar tal cual, en orden (tabla de datos); "
                                   "no agrupa ni resume."},
        "metrics": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                               "required": ["agg", "alias"],
                                               "properties": metric_properties},
                    "description": "Métricas, en el orden en que se muestran."},
        "filters": {**condition_schema_for(ctx),
                    "description": "Condiciones sobre las filas del widget."},
        "trend_by": {"type": "string", "enum": list(ctx.fields),
                     "description": "Columna de la mini tendencia (sparkline) del número; "
                                    "solo los widgets con tendencia. Omitir si no aplica."},
        "sort_by": {"type": "string",
                    "description": "Columna u alias de orden, con '-' delante para descendente."},
        "limit": {"type": "integer", "description": f"Máximo de filas/grupos (1 a {MAX_LIMIT})."},
    }

    # Sin tipo fijado el estilo depende del widget elegido: se acepta como objeto abierto y se
    # valida contra su style_schema después.
    style = {"type": "object", "description": "Apariencia del widget. Opcional: sin estilo se "
                                              "usan los valores por defecto."}
    if widget_type and widgets:
        style_properties, _ = _style_schema(widgets[0].style_schema)
        if style_properties:
            style = {"type": "object", "additionalProperties": False, "properties": style_properties}

    return _to_gemini_schema({
        "type": "object",
        "additionalProperties": False,
        "required": ["widget_type", "title", "fields"],
        "properties": {
            "widget_type": {"enum": [w.key for w in widgets]},
            "title": {"type": "string", "description": "Título corto en español."},
            "fields": {"type": "object", "additionalProperties": False,
                       "properties": fields_properties},
            "style": style,
        },
    })


def condition_schema_for(ctx: SheetContext) -> dict:
    from sheets_reports.engine.steps.filter import conditions_schema
    return conditions_schema(ctx, MAX_IN_VALUES)


def _tools(ctx: SheetContext, widget_type: str | None) -> list[types.Tool]:
    return [types.Tool(function_declarations=[
        types.FunctionDeclaration(
            name=CREATE_TOOL,
            description="Crea el formulario (fields + style) de un widget del tablero.",
            parameters_json_schema=build_tool_parameters(ctx, widget_type),
        ),
        types.FunctionDeclaration(
            name=REJECT_TOOL,
            description="Rechaza el pedido cuando no se puede representar como widget con las columnas disponibles.",
            parameters_json_schema={
                "type": "object",
                "required": ["reason"],
                "properties": {"reason": {"type": "string", "description": "Motivo breve, en español, dirigido al usuario."}},
            },
        ),
    ])]


def _columns_context(ctx: SheetContext) -> str:
    lines = []
    for field in ctx.fields:
        kind = "numérica" if ctx.is_numeric(field) else "texto"
        line = f"- {json.dumps(field, ensure_ascii=False)} ({kind})"
        if ctx.samples.get(field):
            line += f" — valores de ejemplo: {json.dumps(ctx.samples[field], ensure_ascii=False)}"
        lines.append(line)
    return "Columnas de la hoja:\n" + "\n".join(lines)


def _user_message(prompt: str, widget_type: str | None, ctx: SheetContext) -> str:
    fixed = f"El tipo de widget está fijado en: {widget_type}.\n\n" if widget_type else ""
    return f"{_columns_context(ctx)}\n\n{fixed}Pedido del usuario:\n{prompt}"


def _call_model(contents: str, ctx: SheetContext, widget_type: str | None) -> tuple[str, dict]:
    api_key = settings.GEMINI_API_KEY
    if not api_key:
        raise SpecGenerationError("GEMINI_API_KEY no está configurado.")
    # Mantener la referencia al cliente: si se recolecta, cierra su conexión HTTP a mitad de la llamada.
    client = gemini_client(api_key)
    response = client.models.generate_content(
        model=settings.GEMINI_MODEL,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=build_system_prompt(),
            tools=_tools(ctx, widget_type),
            tool_config=types.ToolConfig(function_calling_config=types.FunctionCallingConfig(
                mode=types.FunctionCallingConfigMode.ANY,
                allowed_function_names=[CREATE_TOOL, REJECT_TOOL],
            )),
            temperature=0,
        ),
    )
    calls = response.function_calls or []
    if not calls:
        raise SpecGenerationError("La IA no devolvió una especificación. Intenta reformular el pedido.")
    return calls[0].name, dict(calls[0].args or {})


def _audit(prompt, widget_type, attempt, call_name, args, errors):
    audit_logger.info(json.dumps({
        "prompt": prompt,
        "widget_type": widget_type,
        "attempt": attempt,
        "tool": call_name,
        "args": args,
        "valid": not errors,
        "errors": errors,
    }, ensure_ascii=False, default=str))


# ---------------------------------------------------------------- validación
def _range_label(name: str, rng) -> str:
    low, high = rng
    if low == high:
        return f"{name}: exactamente {low}"
    return f"{name}: entre {low} y {high}"


def _metric_errors(metric, index: int, ctx, seen_aliases) -> list[str]:
    path = f"metrics[{index}]"
    if not isinstance(metric, dict):
        return [f"{path}: debe ser un objeto."]
    errors = []
    agg = metric.get("agg")
    alias = metric.get("alias")
    is_formula = metric.get("type") == "formula"

    if is_formula:
        if not metric.get("expression"):
            errors.append(f"{path}: las métricas con type='formula' necesitan 'expression'.")
        elif not EXPR_RE.match(str(metric["expression"])):
            errors.append(f"{path}: 'expression' solo admite columnas, números y operadores (+ - * / ( )).")
        elif not metric.get("field"):
            # ok: la fórmula trabaja sobre alias
            pass
        if metric.get("window"):
            errors.append(f"{path}: una métrica con formula no admite 'window'.")
    else:
        if agg not in AGGREGATIONS:
            allowed = ", ".join(sorted(AGGREGATIONS))
            errors.append(f"{path}: agg '{agg}' no existe; usa uno de {allowed}.")
        if agg == "count" and not metric.get("field"):
            pass  # count sin campo: cuenta filas
        elif not metric.get("field"):
            errors.append(f"{path}: falta 'field'.")
        elif metric["field"] not in ctx.fields:
            errors.append(f"{path}: la columna '{metric['field']}' no existe en la hoja.")
        elif agg in NUMERIC_AGGS and not ctx.is_numeric(metric["field"]):
            errors.append(f"{path}: '{agg}' solo se aplica a columnas numéricas; "
                          f"'{metric['field']}' es de texto.")
        if metric.get("window"):
            w = metric["window"].get("type") if isinstance(metric["window"], dict) else None
            if w not in WINDOW_TYPES:
                errors.append(f"{path}: window.type '{w}' no existe; usa uno de {', '.join(WINDOW_TYPES)}.")

    if not alias:
        errors.append(f"{path}: falta 'alias' (snake_case único, ej. total_ventas).")
    elif not ALIAS_RE.match(str(alias)):
        errors.append(f"{path}: alias '{alias}' debe ser minúsculas, sin espacios y empezar por "
                      f"una letra (ej. total_ventas).")
    elif alias in seen_aliases:
        errors.append(f"{path}: alias '{alias}' repetido.")
    elif alias in ctx.fields:
        errors.append(f"{path}: alias '{alias}' ya es el nombre de una columna de la hoja; usa otro.")

    label = metric.get("label")
    if label is not None and label != "" and (not isinstance(label, str) or len(label) > 80):
        errors.append(f"{path}: 'label' (nombre a mostrar) debe ser texto de hasta 80 caracteres.")

    if metric.get("filters"):
        errors += condition_errors(metric["filters"], ctx, path=f"{path}.filters",
                                   max_in_values=MAX_IN_VALUES)
    return errors


def _column_errors(column, index: int, ctx, seen_columns) -> list[str]:
    """Una columna de `fields.columns`: "mes" o {"field": "mes", "label": "Mes"}."""
    path = f"columns[{index}]"
    if isinstance(column, str):
        column = {"field": column}
    if not isinstance(column, dict):
        return [f"{path}: debe ser el nombre de una columna o un objeto {{field, label}}."]
    errors = []
    unknown = sorted(set(column) - {"field", "label"})
    if unknown:
        errors.append(f"{path}: claves desconocidas: {', '.join(unknown)}.")
    field = str(column.get("field") or "").strip()
    if not field:
        errors.append(f"{path}: falta 'field'.")
        return errors
    if field not in ctx.fields:
        errors.append(f"{path}: la columna '{field}' no existe en la hoja.")
    elif field in seen_columns:
        errors.append(f"{path}: la columna '{field}' está repetida.")
    else:
        seen_columns.append(field)
    label = column.get("label")
    if label is not None and label != "" and (not isinstance(label, str) or len(label) > 80):
        errors.append(f"{path}: 'label' (nombre a mostrar) debe ser texto de hasta 80 caracteres.")
    return errors


def form_errors(data: dict, ctx: SheetContext, widget_type: str | None) -> list[str]:
    """Valida un WidgetForm propuesto contra la hoja y las capacidades del widget."""
    resolved = data.get("widget_type")
    if not resolved or resolved not in WIDGETS:
        allowed = ", ".join(w.key for w in WIDGETS if w.ai_enabled)
        return [f"widget_type: '{resolved}' no es válido; usa uno de {allowed}."]
    if widget_type and resolved != widget_type:
        return [f"widget_type: debe ser '{widget_type}'."]
    if not WIDGETS.get(resolved).ai_enabled:
        return [f"widget_type: '{resolved}' no lo propone la IA; usa uno de "
                f"{', '.join(w.key for w in WIDGETS if w.ai_enabled)}."]

    definition = WIDGETS.get(resolved)
    caps = definition.capabilities or {}
    fields = data.get("fields") if isinstance(data.get("fields"), dict) else {}
    errors: list[str] = []

    for key in fields:
        if key not in {"dimensions", "pivots", "metrics", "columns", "filters",
                       "sort_by", "limit", "trend_by"}:
            errors.append(f"fields: '{key}' no es un campo de datos válido.")

    dimensions = fields.get("dimensions") or []
    if not isinstance(dimensions, list):
        errors.append("fields.dimensions debe ser una lista.")
        dimensions = []
    if "dimensions" in caps:
        if not caps["dimensions"][0] <= len(dimensions) <= caps["dimensions"][1]:
            errors.append(f"fields.dimensions: {_range_label('dimensiones', caps['dimensions'])}.")
    for dim in dimensions:
        if dim not in ctx.fields:
            errors.append(f"fields.dimensions: la columna '{dim}' no existe en la hoja.")

    trend_by = fields.get("trend_by")
    if trend_by:
        if not caps.get("trend"):
            errors.append(f"fields.trend_by: «{definition.label}» no muestra tendencia.")
        elif trend_by not in ctx.fields:
            errors.append(f"fields.trend_by: la columna '{trend_by}' no existe en la hoja.")
        elif trend_by in dimensions:
            errors.append("fields.trend_by: ya está en las dimensiones.")

    pivots = fields.get("pivots") or []
    if not isinstance(pivots, list):
        errors.append("fields.pivots debe ser una lista.")
        pivots = []
    if "pivots" in caps:
        if not caps["pivots"][0] <= len(pivots) <= caps["pivots"][1]:
            errors.append(f"fields.pivots: {_range_label('pivotes', caps['pivots'])}.")
    for pivot in pivots:
        if pivot not in ctx.fields:
            errors.append(f"fields.pivots: la columna '{pivot}' no existe en la hoja.")
    if pivots and not caps.get("pivots"):
        errors.append("fields.pivots: este widget no admite pivotes.")
    overlap = sorted({str(p) for p in pivots} & {str(d) for d in dimensions})
    if overlap:
        errors.append(f"fields.pivots: {', '.join(overlap)} no puede estar también en las dimensiones.")

    columns = fields.get("columns") or []
    if not isinstance(columns, list):
        errors.append("fields.columns debe ser una lista.")
        columns = []
    if columns and not caps.get("columns"):
        errors.append("fields.columns: este widget no admite columnas.")
    if "columns" in caps and not caps["columns"][0] <= len(columns) <= caps["columns"][1]:
        errors.append(f"fields.columns: {_range_label('columnas', caps['columns'])}.")
    seen_columns: list[str] = []
    for i, column in enumerate(columns):
        errors += _column_errors(column, i, ctx, seen_columns)

    metrics = fields.get("metrics") or []
    if not isinstance(metrics, list):
        errors.append("fields.metrics debe ser una lista.")
        metrics = []
    low, high = caps.get("metrics", (0, 0))
    if not low <= len(metrics) <= high:
        errors.append(f"fields.metrics: {_range_label('métricas', caps.get('metrics', (0, 0)))}.")
    aliases: list[str] = []
    for i, metric in enumerate(metrics):
        errors += _metric_errors(metric, i, ctx, aliases)
        if isinstance(metric, dict) and metric.get("alias"):
            aliases.append(metric["alias"])

    if resolved in {"bar", "line"} and pivots and len(aliases) > 1:
        errors.append("fields: con pivotes los gráficos admiten UNA métrica; quita las demás "
                      "o quita los pivotes.")

    if fields.get("filters"):
        if not caps.get("filters"):
            errors.append("fields.filters: este widget no admite filtros propios.")
        errors += condition_errors(fields["filters"], ctx, path="fields.filters",
                                   max_in_values=MAX_IN_VALUES)

    if fields.get("sort_by"):
        if not caps.get("sort"):
            errors.append("fields.sort_by: este widget no admite orden.")
        column = str(fields["sort_by"]).lstrip("-")
        if column not in ctx.fields and column not in aliases:
            errors.append(f"fields.sort_by: '{column}' no es una columna ni un alias de métrica.")
    if fields.get("limit") is not None:
        if not caps.get("limit"):
            errors.append("fields.limit: este widget no admite límite.")
        elif not isinstance(fields["limit"], int) or not 1 <= fields["limit"] <= MAX_LIMIT:
            errors.append(f"fields.limit: debe ser un número entre 1 y {MAX_LIMIT}.")

    style = data.get("style") if isinstance(data.get("style"), dict) else {}
    known = {c["key"]: c for c in definition.style_schema or []}
    for key, value in style.items():
        if key not in known:
            errors.append(f"style: '{key}' no es un control de este widget.")
            continue
        control = known[key]
        if control.get("type") == "choice":
            if control.get("options_from") == "metrics":
                # Roles del KPI: el valor es un alias de las métricas propuestas, una
                # opción estática del schema (ej. «Valor fijo») o vacío.
                allowed = [""] + [o["value"] for o in control.get("options", []) if o["value"]] + aliases
                if value not in allowed:
                    errors.append(f"style.{key}: '{value}' no es una métrica de este widget; "
                                  f"usa uno de sus alias ({', '.join(a for a in aliases) or 'ninguno'}).")
            else:
                allowed = [o["value"] for o in control.get("options", [])]
                if value not in allowed:
                    errors.append(f"style.{key}: '{value}' no es válido; usa uno de "
                                  f"{', '.join(str(v) for v in allowed)}.")
        elif control.get("type") == "number" and not isinstance(value, (int, float)):
            errors.append(f"style.{key}: debe ser un número.")
        elif control.get("type") == "boolean" and not isinstance(value, bool):
            errors.append(f"style.{key}: debe ser true o false.")
        elif control.get("type") == "string" and not isinstance(value, str):
            errors.append(f"style.{key}: debe ser un texto.")

    if style.get("statusBasis") == "target_pct" and not _has_target(style, aliases):
        errors.append("style.statusBasis: «% de la meta» necesita una meta (target o targetMetric).")

    if not str(data.get("title") or "").strip():
        errors.append("title: el widget necesita un título.")

    return errors


def _has_target(style: dict, aliases: list[str]) -> bool:
    """¿El style define una meta: por métrica elegida o por valor fijo?"""
    metric = style.get("targetMetric")
    if metric == "fixed":
        return style.get("target") not in (None, "", 0)
    if metric:
        return metric in aliases
    return False


def _normalize(args: dict) -> dict:
    """La respuesta de la tool, en el formato del request del builder."""
    data = copy.deepcopy(args) if isinstance(args, dict) else {}
    fields = data.get("fields") if isinstance(data.get("fields"), dict) else {}
    style = data.get("style") if isinstance(data.get("style"), dict) else {}
    return {
        "widget_type": data.get("widget_type"),
        "title": str(data.get("title") or "").strip(),
        "fields": {k: v for k, v in fields.items() if v is not None},
        "style": style,
    }


def generate_widget_form(prompt: str, widget_type: str | None, ctx: SheetContext) -> dict:
    """
    Genera `{widget_type, title, fields, style}` para `prompt` sobre la hoja de `ctx` (con
    `samples` para que la IA escriba los valores exactos).

    Si la primera propuesta no pasa la validación, reintenta UNA vez pasándole a la IA los
    errores; si vuelve a fallar, lanza SpecGenerationError con un mensaje legible. Nunca
    devuelve un form inválido.
    """
    if widget_type is not None and widget_type not in WIDGETS:
        raise SpecGenerationError(f"Tipo de widget desconocido: {widget_type}.")
    if not ctx.fields:
        raise SpecGenerationError("La hoja no tiene columnas.")

    contents = _user_message(prompt, widget_type, ctx)
    errors: list[str] = []
    for attempt in (1, 2):
        call_name, args = _call_model(contents, ctx, widget_type)

        if call_name == REJECT_TOOL:
            _audit(prompt, widget_type, attempt, call_name, args, [])
            raise SpecGenerationError(args.get("reason") or "La IA no pudo interpretar el pedido.")

        data = _normalize(args)
        errors = form_errors(data, ctx, widget_type)
        _audit(prompt, widget_type, attempt, call_name, args, errors)

        if not errors:
            # El form validado se re-parsea por el dataclass: lo que guarda es lo que se ejecuta.
            WidgetForm.from_dict({"fields": data["fields"], "style": data["style"]})
            return data

        contents = (
            f"{_user_message(prompt, widget_type, ctx)}\n\n"
            f"Tu respuesta anterior fue:\n{json.dumps(args, ensure_ascii=False)}\n\n"
            f"No es válida por estos errores:\n- " + "\n- ".join(errors) +
            "\n\nCorrígela y vuelve a llamar a create_widget."
        )

    raise SpecGenerationError(
        "No se pudo generar un widget válido para ese pedido. Detalle: " + " ".join(errors)
    )
