"""
Generación del `WidgetForm` (fields + style) a partir de un prompt en lenguaje natural, vía
Gemini con function calling forzado. La IA NUNCA calcula números ni toca los datos: solo decide
QUÉ calcular, devolviendo un JSON validado contra un schema construido en cada llamada desde
las columnas reales de la hoja y el `style_schema` del widget. Nada de lo que devuelve se
ejecuta tal cual.
"""
import copy
import dataclasses
import json
import logging
import re

from django.conf import settings
from google import genai
from google.genai import types

from sheets_reports.engine.steps.filter import condition_errors
from sheets_reports.engine.context import SheetContext
from sheets_reports.engine.formulas import FORMATS, FormulaError, compile_formula, formula_tree
from sheets_reports.services.source_columns import map_columns
from sheets_reports.utils.validation import MAX_IN_VALUES
from sheets_reports.engine import AGGREGATIONS
from sheets_reports.engine.steps.aggregation import METRIC_FORMATS
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

MAX_LIMIT = 500
# Campos calculados nuevos que la IA puede proponer en una respuesta.
MAX_NEW_CALCULATED = 3


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
- dimensions: columnas por las que agrupar (categorías del eje X / filas de la tabla). Si el
  widget exige dimensión y el usuario no dice cómo agrupar, usa una columna de texto con pocos
  valores distintos (ej. departamento, campus); nunca una fecha suelta ni un identificador.
- columns: SOLO la tabla de datos: columnas de la hoja a mostrar tal cual, en orden, cada una
  como {"field": "mes", "label": "Mes"}; `label` es opcional (nombre a mostrar en la cabecera,
  sin él se muestra la columna). La tabla NO agrupa ni resume: no lleva dimensions ni metrics.
- pivots: columnas para desagregar además de la dimensión (columnas de una tabla dinámica,
  series de un gráfico). [] si no aplica.
- filters: condiciones sobre las FILAS que entran al widget. [] si no hay.
- trend_by: columna de TIEMPO (año, mes, fecha) de la mini tendencia (sparkline) bajo el
  número del KPI (ej. "mes"); nunca una categoría. Solo en widgets con tendencia. Omitir si no
  aplica.
- metrics: lista de métricas, en orden. Cada una lleva un `alias` único en snake_case que es el
  nombre de la columna con la que se calcula (ej. "total_ventas") y, si hace falta un texto más
  claro para la persona que mira el widget, un `label` (nombre a mostrar, ej. "Costos totales";
  sin label se muestra el agg en español con la columna, ej. «Promedio Ventas»):
  - {"agg", "field", "alias"}: resume una columna.
    - agg "count": "cuántos", "cantidad de". Cuenta filas y NO lleva field.
    - agg "count_distinct": "cuántos distintos" de una columna (cualquier tipo).
    - agg "sum"/"avg"/"median"/"min"/"max"/"std": SOLO sobre columnas numéricas.
    - agg "auto": SOLO con un campo calculado agregado: uno de los que lista el mensaje o uno
      que propongas en `calculated_fields` (ver abajo). Ya traen su agregación.
    - filters (opcional): condiciones SOLO para esa métrica. Sirven para poner en el mismo
      widget "ventas 2026" y "ventas 2025", o "ventas de Hogar" junto al total. Solo en
      widgets con "condiciones por métrica" (los que admiten más de una métrica); con una
      sola métrica, usa fields.filters.
    - window (opcional): {"type": "percent_of_total" | "percent_of_row" | "running_total" |
      "pct_change"} sobre esa métrica ya agregada. "participación", "qué % representa cada...",
      "acumulado", "variación respecto al anterior". Solo las que el widget lista en
      «ventanas». Con pivotes solo valen "percent_of_total" (cada celda sobre el total de su
      valor del pivote) y "percent_of_row" (cada celda sobre el total de su fila: "de cada X,
      qué % es de cada Y"), que además los necesita. Los dos % y "running_total" suman los
      grupos: solo con agg "sum" o "count" (un promedio, un máximo o un campo calculado no se
      suman); "pct_change" vale con cualquiera. Un widget de un solo
      número no lleva ventanas: la participación es un campo calculado agregado (ej.
      SUM(IF([categoria] = "Hogar", [ventas], 0)) / SUM([ventas]) * 100), y la variación frente
      al periodo anterior son dos métricas filtradas con relative "latest" y "previous" sobre la
      columna de tiempo y style.compare con el alias de la anterior.
- sort_by: columna u alias por el que ordenar, con "-" delante para descendente (ej.
  "-total_ventas"). Null si no importa.

## calculated_fields (campos calculados nuevos, opcional)
Cuando el pedido necesita un cálculo entre totales (una diferencia, un cociente, un %, un
margen, una participación, un promedio de una condición) y no hay un campo calculado que ya lo
haga, propón el campo en `calculated_fields` y úsalo como métrica con agg "auto" y `field` igual
a su `name`. Se crea en la fuente al aplicar la propuesta y queda para todo el tablero.
- Antes de proponer uno, revisa las fórmulas de los campos calculados que ya existen (el mensaje
  las muestra): si alguno calcula lo pedido, aunque su nombre no lo diga, úsalo (agg "auto" si
  es agregado; como una columna más si es por fila). No propongas uno con la misma fórmula.
- {"name", "formula", "format"}: `name` corto y claro (ej. "% Ejecución", "Ganancia"), distinto
  de las columnas; `format` "percent" si el resultado es un porcentaje (la fórmula multiplica
  por 100), si no "number".
- `formula` SIEMPRE agregada: cada columna dentro de SUM, AVG, COUNT, COUNT_DISTINCT, MIN o MAX.
  Columnas entre corchetes ([Ventas]), textos entre comillas, + - * / ( ), comparaciones
  (= != > >= < <=), AND OR NOT e IF(condición, sí, no) dentro de las agregaciones.
  Ej.: SUM([Ventas]) - SUM([Costo]); SUM([Gasto]) / SUM([Presupuesto]) * 100;
  AVG(IF([Respuesta] = "Sí", 1, 0)) * 100; SUM(IF([categoria] = "Hogar", [ventas], 0)) / SUM([ventas]) * 100.
- No combines columnas sueltas con agregaciones (SUM([a]) / [b] no vale).
- Dentro de una agregación cada columna es el valor de la fila, no un total. Para contar filas
  que cumplen una condición usa SUM(IF(condición, 1, 0)): COUNT cuenta valores no vacíos (el 0 también).
- limit: máximo de filas/grupos a mostrar ("top 5" → 5). Null si no aplica.

## style (la apariencia)
Diccionario plano con SOLO las claves del widget. Cada clave tiene un tipo fijo:
`string` (texto), `number` (número), `boolean` (true/false), `choice` (uno de sus `options`).
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
  "current_year", "previous_year", "current_month" (del reloj); "latest" (el periodo más
  reciente de la columna), "previous" (el anterior a ese) y "earliest" (el más antiguo), solo
  en columnas de tiempo (años, meses, fechas). Úsalo para "este año", "el último mes" en vez
  de fijar un número.
Para comparar periodos ("variación anual", "frente al año anterior") sin que el usuario diga
cuáles, usa el año del reloj: una métrica con "current_year" y otra con "previous_year". Pon en
el `label` de cada una su periodo ("Este año", "Año anterior") para que se lea qué se compara.
Usa los valores de ejemplo de las columnas para escribir el valor exacto.
"""

PIVOT_PROMPT = """\
Con pivots, los gráficos permiten UNA métrica; si el usuario pide varias y un cruce en un
gráfico, incluye todo tal como lo pidió: el sistema te pedirá que corrijas la propuesta."""

# El título de la tarjeta lo pone el usuario: la IA no lo genera ni lo ve en `style`.
NOT_AI_STYLE_KEYS = {"title"}

# Con el estado actual del widget, la IA no crea desde cero: ajusta lo que ya hay.
MODIFY_PROMPT = """\
El widget ya existe: su estado actual está en `current_widget`. El usuario pide un cambio
sobre él. Devuelve con create_widget el formulario COMPLETO ya ajustado: conserva todo lo que
el usuario no pidió cambiar (dimensiones, métricas con sus alias, filtros, orden, límite y
style) y quita o reemplaza algo solo si lo pide. Si la conversación previa aclara a
qué se refiere ("eso", "ahora por mes"), úsala."""

# Mensajes previos del hilo que se le pasan a la IA (los más recientes).
MAX_HISTORY_MESSAGES = 10


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
    if admits_metric_filters(widget):
        parts.append("condiciones por métrica")
    if (caps.get("metrics") or (0, 0))[1] > 0:
        windows = window_types_for(widget)
        parts.append("ventanas: " + ", ".join(WINDOW_LABELS[w] for w in windows) if windows
                     else "sin ventanas")
    return ", ".join(parts)


WINDOW_LABELS = {"percent_of_total": "% del total de la columna (participación)",
                 "percent_of_row": "% del total de la fila",
                 "running_total": "acumulado", "pct_change": "variación"}


# Ventanas que se calculan por celda y por eso valen con pivotes (engine/steps/window.py).
PIVOT_WINDOWS = ("percent_of_total", "percent_of_row")
# Ventanas que suman los valores de los grupos (el % de un total, el acumulado): solo valen
# con agregaciones aditivas. Un promedio, un máximo o un cociente no se suman.
ADDITIVE_WINDOWS = ("percent_of_total", "percent_of_row", "running_total")
ADDITIVE_AGGS = ("sum", "count")


def window_types_for(widget) -> tuple:
    """Ventanas que el widget dibuja bien (`capabilities["windows"]`)."""
    return tuple((widget.capabilities or {}).get("windows") or ())


def _window_error(path: str, w_type: str, widget, pivots: list, agg: str | None = None) -> str | None:
    """Una ventana válida en el motor pero que en este widget se ignoraría o daría números
    falsos. El mensaje guía a la IA en el reintento."""
    caps = widget.capabilities or {}
    allowed = window_types_for(widget)
    if w_type not in allowed:
        if caps.get("dimensions", (0, 0))[1] == 0 and caps.get("metrics", (0, 0))[1] > 0:
            # Un solo número: no hay otras filas (ni total de grupos, ni anterior, ni acumulado).
            return (f"{path}: este widget da un solo número y no admite 'window'. Para una "
                    f"participación propón un campo calculado agregado (ej. SUM(IF([cat] = \"X\", "
                    f"[valor], 0)) / SUM([valor]) * 100) y úsalo con agg 'auto'. Para comparar con el "
                    f"periodo anterior usa dos métricas con filters relative 'latest' y 'previous' "
                    f"sobre la columna de tiempo y style.compare con el alias de la anterior.")
        if not allowed:
            return f"{path}: este widget no admite 'window'; quítalo."
        return f"{path}: '{w_type}' no está disponible en este widget; usa {', '.join(allowed)} o quítalo."
    if pivots and w_type not in PIVOT_WINDOWS:
        # Con pivote cada valor es una celda del cruce: solo los porcentajes se calculan por celda.
        usable = [w for w in allowed if w in PIVOT_WINDOWS]
        hint = f"usa {', '.join(usable)}, " if usable else ""
        return f"{path}: con pivotes '{w_type}' no se aplica; {hint}quita los pivotes o la ventana."
    if w_type == "percent_of_row" and not pivots:
        return (f"{path}: 'percent_of_row' reparte cada fila entre las columnas cruzadas y "
                f"necesita pivotes; sin ellos usa 'percent_of_total'.")
    if w_type in ADDITIVE_WINDOWS and agg not in ADDITIVE_AGGS:
        return (f"{path}: '{w_type}' suma los valores de los grupos y solo vale con agg "
                f"{' o '.join(ADDITIVE_AGGS)}; con '{agg}' el total no significa nada. Quita la "
                f"ventana o usa 'pct_change'.")
    return None


def panel_options(widget) -> dict:
    """Lo que el panel puede ofrecer en las métricas de `widget`, calculado con las mismas
    reglas que valida `form_errors` (así el panel nunca ofrece algo que al guardar se rechaza):
    - `windows`: «Mostrar como» por agregación, sin pivote (`flat`) y con pivote (`pivot`);
    - `numeric_aggs`: agregaciones que solo valen sobre columnas numéricas;
    - `metric_formats`: formatos que puede elegir una métrica."""
    aggs = [agg for agg in AGGREGATIONS if agg != "mean"]
    windows = {
        mode: {agg: [w for w in WINDOW_TYPES if _window_error("", w, widget, pivots, agg) is None]
               for agg in aggs}
        for mode, pivots in (("flat", []), ("pivot", ["pivote"]))
    }
    return {"windows": windows, "numeric_aggs": sorted(NUMERIC_AGGS),
            "metric_formats": list(METRIC_FORMATS)}


def admits_metric_filters(widget) -> bool:
    """Condiciones propias de una métrica: solo junto a otras métricas (Electrónica vs total);
    con una sola métrica equivalen a los filtros del widget."""
    return (widget.capabilities or {}).get("metrics", (0, 0))[1] > 1


def _style_schema_docs(widget) -> list[str]:
    """Una línea por control de estilo: clave, tipo y opciones."""
    lines = []
    for control in widget.style_schema or []:
        if control["key"] in NOT_AI_STYLE_KEYS:
            continue
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


def _without_title(form: dict) -> dict:
    """Un WidgetForm sin título ni `style.title`: así lo ve (y lo devuelve) la IA."""
    form = {k: v for k, v in form.items() if k != "title"}
    if isinstance(form.get("style"), dict):
        form["style"] = {k: v for k, v in form["style"].items() if k not in NOT_AI_STYLE_KEYS}
    return form


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
        f'Prompt: "{prompt}"\ncreate_widget({json.dumps(_without_title(args), ensure_ascii=False)})'
        for w in widgets for prompt, args in w.ai_examples
    ]
    return "\n".join([
        CORE_PROMPT,
        "## Tipos de widget (si no viene fijado)",
        *types_section,
        PIVOT_PROMPT,
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


def generate_json(contents: str, schema: dict, temperature: float = 0) -> object:
    """Respuesta JSON libre (sin tools) que cumple `schema`: para pedidos auxiliares a la IA,
    como las sugerencias del chat. Lanza si no hay API key o la respuesta no es JSON."""
    api_key = settings.GEMINI_API_KEY
    if not api_key:
        raise SpecGenerationError("GEMINI_API_KEY no está configurado.")
    client = gemini_client(api_key)
    response = client.models.generate_content(
        model=settings.GEMINI_MODEL,
        contents=contents,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_json_schema=schema,
            temperature=temperature,
        ),
    )
    return json.loads(response.text or "null")


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
        if control["key"] in NOT_AI_STYLE_KEYS:
            continue
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


def _admits_field(widget, key: str) -> bool:
    """¿El tipo admite la clave `key` de `fields`, según sus capabilities?"""
    caps = widget.capabilities or {}
    if key in ("dimensions", "pivots", "metrics", "columns"):
        return (caps.get(key) or [0, 0])[1] > 0
    if key == "trend_by":
        return bool(caps.get("trend"))
    if key == "sort_by":
        return bool(caps.get("sort"))
    if key in ("limit", "filters"):
        return bool(caps.get(key))
    return True


def build_tool_parameters(ctx: SheetContext, widget_type: str | None) -> dict:
    """Schema de parámetros de `create_widget`, construido en cada llamada desde los registros
    de widgets (capabilities + style_schema) y el motor (agregaciones y ventanas disponibles).
    Las columnas van como texto libre, no como enum: con muchas columnas o nombres largos
    (preguntas de un formulario) el enum repetido en cada campo supera el límite de estados
    de Gemini. Los nombres exactos van en el mensaje (`columns_context`) y `form_errors`
    rechaza los que no existen, con reintento."""
    widgets = [w for w in _widgets_for(widget_type) if w]
    metric_properties = {
        "agg": {"enum": sorted(set(AGGREGATIONS) - {"mean"}),
                "description": "Agregación: " + AGG_LABELS_DESC},
        "field": _column_schema("Columna de la hoja a agregar. Omitir solo en agg=count."),
        "alias": {"type": "string",
                  "description": "Nombre único en snake_case de la columna resultante (ej. total_ventas)."},
        "label": {"type": "string", "maxLength": 80,
                  "description": "Nombre a mostrar de la métrica en el widget (ej. 'Costos totales'); "
                                 "vacío o ausente = el alias en texto."},
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
    if widget_type and widgets and not admits_metric_filters(widgets[0]):
        # Con una sola métrica sus condiciones serían las del widget: se ofrece fields.filters.
        del metric_properties["filters"]
    if widget_type and widgets:
        windows = list(window_types_for(widgets[0]))
        if windows:
            metric_properties["window"]["properties"]["type"]["enum"] = windows
        else:
            del metric_properties["window"]
    fields_properties = {
        "dimensions": {"type": "array", "items": _column_schema(),
                       "description": "Columnas para agrupar / mostrar como filas."},
        "pivots": {"type": "array", "items": _column_schema(),
                   "description": "Columnas para desagregar (series / columnas cruzadas)."},
        "columns": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                               "required": ["field"],
                                               "properties": {
                                                   "field": _column_schema("Columna de la hoja a mostrar."),
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
        "trend_by": _column_schema("Columna de TIEMPO (año, mes, fecha) de la mini tendencia "
                                   "(sparkline) del número; solo los widgets con tendencia. "
                                   "Omitir si no aplica."),
        "sort_by": {"type": "string",
                    "description": "Columna u alias de orden, con '-' delante para descendente."},
        "limit": {"type": "integer", "description": f"Máximo de filas/grupos (1 a {MAX_LIMIT})."},
    }
    if widget_type and widgets:
        # Con el tipo fijado, la IA solo ve los campos que ese tipo admite: no puede proponer,
        # por ejemplo, una tendencia en un gráfico de dona (form_errors la rechazaría).
        fields_properties = {key: schema for key, schema in fields_properties.items()
                             if _admits_field(widgets[0], key)}

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
        "required": ["widget_type", "fields"],
        "properties": {
            "widget_type": {"enum": [w.key for w in widgets]},
            "calculated_fields": {
                "type": "array", "maxItems": MAX_NEW_CALCULATED,
                "description": "Campos calculados agregados nuevos que usan las métricas (agg 'auto').",
                "items": {"type": "object", "additionalProperties": False,
                          "required": ["name", "formula", "format"],
                          "properties": {
                              "name": {"type": "string", "description": "Nombre del campo."},
                              "formula": {"type": "string",
                                          "description": "Fórmula agregada, ej. SUM([Ventas]) - SUM([Costo])."},
                              "format": {"enum": list(FORMATS)},
                          }},
            },
            "fields": {"type": "object", "additionalProperties": False,
                       "properties": fields_properties},
            "style": style,
        },
    })


def _column_schema(description: str = "") -> dict:
    """Una columna de la hoja en el schema de la tool: texto, sin enum (ver build_tool_parameters)."""
    hint = "Nombre exacto de una de las columnas listadas en el mensaje."
    return {"type": "string", "description": f"{description} {hint}".strip()}


def condition_schema_for(ctx: SheetContext) -> dict:
    """Las condiciones del motor para la tool: igual que las que valida, pero con la columna
    como texto libre."""
    from sheets_reports.engine.steps.filter import conditions_schema
    schema = conditions_schema(ctx, MAX_IN_VALUES)
    items = schema["items"]
    return {**schema, "items": {**items, "properties": {**items["properties"],
                                                        "field": _column_schema()}}}


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


FORMAT_LABELS = {"percent": "porcentaje", "currency": "moneda", "progress": "porcentaje"}


def columns_context(ctx: SheetContext) -> str:
    """Las columnas con su tipo y ejemplos, y los campos calculados con su fórmula: así la IA
    sabe qué calcula cada uno aunque su nombre no lo diga."""
    lines = []
    for field in ctx.fields:
        kind = "numérica" if ctx.is_numeric(field) else "texto"
        line = f"- {json.dumps(field, ensure_ascii=False)} ({kind})"
        calculated = ctx.calculated.get(field)
        if calculated and calculated["kind"] == "row":
            line += f" — campo calculado por fila: {calculated['formula']}"
        if ctx.samples.get(field):
            line += f" — valores de ejemplo: {json.dumps(ctx.samples[field], ensure_ascii=False)}"
        lines.append(line)
    text = "Columnas de la hoja:\n" + "\n".join(lines)
    if ctx.aggregated_fields:
        def describe(name):
            info = ctx.calculated.get(name) or {}
            fmt = FORMAT_LABELS.get(info.get("format"))
            line = f"- {json.dumps(name, ensure_ascii=False)}{f' ({fmt})' if fmt else ''}"
            return f"{line}: {info['formula']}" if info.get("formula") else line
        text += ("\n\nCampos calculados agregados (solo como métrica, con agg \"auto\"), con su fórmula:\n"
                 + "\n".join(describe(name) for name in sorted(ctx.aggregated_fields)))
    return text


def _history_text(history: list[dict] | None) -> str:
    """Los mensajes previos del hilo: pedidos del usuario y formularios que propuso la IA."""
    lines = []
    for message in (history or [])[-MAX_HISTORY_MESSAGES:]:
        if not isinstance(message, dict):
            continue
        if message.get("role") == "user" and str(message.get("text") or "").strip():
            lines.append(f"- Usuario: {str(message['text']).strip()}")
        elif message.get("role") == "assistant" and isinstance(message.get("proposal"), dict):
            lines.append(f"- Tú propusiste: {json.dumps(_without_title(message['proposal']), ensure_ascii=False)}")
    return "Conversación previa:\n" + "\n".join(lines) if lines else ""


def _user_message(prompt: str, widget_type: str | None, ctx: SheetContext,
                  current: dict | None = None, history: list[dict] | None = None) -> str:
    parts = [columns_context(ctx)]
    if widget_type:
        parts.append(f"El tipo de widget está fijado en: {widget_type}.")
    if current:
        parts.append(MODIFY_PROMPT)
        parts.append("current_widget:\n" + json.dumps(_without_title(current), ensure_ascii=False))
    previous = _history_text(history)
    if previous:
        parts.append(previous)
    parts.append(f"Pedido del usuario:\n{prompt}")
    return "\n\n".join(parts)


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

    if ctx.is_aggregated(metric.get("field")) or agg == "auto":
        # Campo calculado agregado: la agregación ya viene en su fórmula.
        if not ctx.is_aggregated(metric.get("field")):
            errors.append(f"{path}: agg 'auto' solo se usa con un campo calculado agregado.")
        elif agg != "auto":
            errors.append(f"{path}: '{metric['field']}' es un campo calculado agregado: "
                          f"usa agg 'auto' (la agregación ya está en su fórmula).")
        if metric.get("window"):
            w = metric["window"].get("type") if isinstance(metric["window"], dict) else None
            if w not in WINDOW_TYPES:
                errors.append(f"{path}: window.type '{w}' no existe; usa uno de {', '.join(WINDOW_TYPES)}.")
    else:
        if agg not in AGGREGATIONS:
            allowed = ", ".join(sorted(set(AGGREGATIONS) - {"auto"}))
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
    if metric.get("format") not in (None, "", *METRIC_FORMATS):
        errors.append(f"{path}: format '{metric['format']}' no existe; usa uno de {', '.join(METRIC_FORMATS)}.")

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


def _same_formula(text: str, ctx: SheetContext) -> str | None:
    """El campo calculado de la fuente con la misma fórmula (mismo árbol: da igual cómo esté
    escrita), o None."""
    tree = formula_tree(text)
    for name, info in ctx.calculated.items():
        if tree is not None and formula_tree(info["formula"]) == tree:
            return name
    return None


def _calculated_fields_errors(items, ctx: SheetContext) -> tuple[list[str], list[str]]:
    """Los `calculated_fields` de una propuesta: [{name, formula, format}], siempre agregados.
    → (nombres válidos, errores)."""
    if items in (None, []):
        return [], []
    if not isinstance(items, list):
        return [], ["calculated_fields debe ser una lista."]
    if len(items) > MAX_NEW_CALCULATED:
        return [], [f"calculated_fields: como mucho {MAX_NEW_CALCULATED}."]
    names, errors = [], []
    for i, item in enumerate(items):
        path = f"calculated_fields[{i}]"
        if not isinstance(item, dict):
            errors.append(f"{path}: debe ser un objeto {{name, formula, format}}.")
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            errors.append(f"{path}: falta 'name'.")
            continue
        if name in ctx.fields or name in ctx.aggregated_fields or name in names:
            errors.append(f"{path}: ya existe una columna o un campo llamado '{name}'; usa otro nombre.")
            continue
        if item.get("format", "number") not in FORMATS:
            errors.append(f"{path}: format debe ser uno de {', '.join(FORMATS)}.")
        try:
            formula = compile_formula(str(item.get("formula") or ""), ctx.fields, ctx.aggregated_fields)
        except FormulaError as e:
            errors.append(f"{path}: {e}")
            continue
        if not formula.aggregated:
            errors.append(f"{path}: la fórmula debe ser agregada (cada columna dentro de SUM, AVG, "
                          f"COUNT, COUNT_DISTINCT, MIN o MAX).")
            continue
        same = _same_formula(formula.text, ctx)
        if same:
            errors.append(f"{path}: ya existe el campo '{same}' con esa fórmula: no lo propongas, "
                          f"úsalo con agg 'auto' y field '{same}'.")
            continue
        names.append(name)
    return names, errors


def form_errors(data: dict, ctx: SheetContext, widget_type: str | None,
                require_title: bool = True) -> list[str]:
    """Valida un WidgetForm propuesto contra la hoja y las capacidades del widget. La propuesta
    de la IA no trae título (`require_title=False`): lo pone el usuario."""
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

    # Los campos calculados que propone la IA: válidos contra la hoja, y desde aquí el resto
    # del form los puede usar como métricas «auto».
    new_fields, calc_errors = _calculated_fields_errors(data.get("calculated_fields"), ctx)
    errors += calc_errors
    if new_fields:
        ctx = dataclasses.replace(ctx, aggregated_fields=ctx.aggregated_fields | set(new_fields))

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
        elif trend_by not in ctx.time_fields:
            errors.append(f"fields.trend_by: '{trend_by}' no es una columna de tiempo (año, mes, "
                          f"fecha): la tendencia es la evolución en el tiempo. Usa una de tiempo u "
                          f"omite trend_by.")
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
    metric_filters_ok = admits_metric_filters(definition)
    for i, metric in enumerate(metrics):
        errors += _metric_errors(metric, i, ctx, aliases)
        if isinstance(metric, dict) and metric.get("filters") and not metric_filters_ok:
            errors.append(f"metrics[{i}].filters: con una sola métrica usa fields.filters.")
        window = metric.get("window") if isinstance(metric, dict) else None
        if isinstance(window, dict) and window.get("type") in WINDOW_TYPES:
            error = _window_error(f"metrics[{i}].window", window["type"], definition, pivots,
                                  metric.get("agg"))
            if error:
                errors.append(error)
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

    if require_title and not str(data.get("title") or "").strip():
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
    calculated = data.get("calculated_fields") if isinstance(data.get("calculated_fields"), list) else []
    return {
        "widget_type": data.get("widget_type"),
        "title": "",
        "calculated_fields": [
            {"name": str(f.get("name") or "").strip(), "formula": str(f.get("formula") or "").strip(),
             "format": f.get("format") or "number"}
            for f in calculated if isinstance(f, dict)
        ],
        "fields": {k: v for k, v in fields.items() if v is not None},
        "style": {k: v for k, v in style.items() if k not in NOT_AI_STYLE_KEYS},
    }


def _column_key(name) -> str:
    """Clave para comparar nombres de columna sin importar espacios ni mayúsculas."""
    return " ".join(str(name).split()).casefold()


def _resolve_columns(data: dict, ctx: SheetContext) -> dict:
    """Cambia las columnas de la propuesta por el nombre real de la hoja cuando solo difieren en
    espacios o mayúsculas: la IA no copia los encabezados con dobles espacios («recomendaría  la
    UAPA»). Lo que no coincide queda igual y lo rechaza la validación."""
    known = (*ctx.fields, *sorted(ctx.aggregated_fields))
    matches: dict[str, list[str]] = {}
    for field in known:
        matches.setdefault(_column_key(field), []).append(field)
    real = {key: names[0] for key, names in matches.items() if len(names) == 1}

    def resolve(value: str) -> str:
        return value if value in known else real.get(_column_key(value), value)

    data["fields"], _ = map_columns(data["fields"], {}, resolve)
    return data


def generate_widget_form(prompt: str, widget_type: str | None, ctx: SheetContext,
                         current: dict | None = None, history: list[dict] | None = None) -> dict:
    """
    Genera `{widget_type, title, fields, style}` para `prompt` sobre la hoja de `ctx` (con
    `samples` para que la IA escriba los valores exactos). `title` va siempre vacío: el título
    de la tarjeta lo pone el usuario, la IA no lo genera.

    Con `current` (`{title, fields, style}` del widget que se edita) la IA ajusta ese estado en
    vez de crear desde cero; `history` son los mensajes previos del hilo
    (`{"role": "user", "text"}` / `{"role": "assistant", "proposal"}`).

    Si la primera propuesta no pasa la validación, reintenta UNA vez pasándole a la IA los
    errores; si vuelve a fallar, lanza SpecGenerationError con un mensaje legible. Nunca
    devuelve un form inválido.
    """
    if widget_type is not None and widget_type not in WIDGETS:
        raise SpecGenerationError(f"Tipo de widget desconocido: {widget_type}.")
    if not ctx.fields:
        raise SpecGenerationError("La hoja no tiene columnas.")

    contents = _user_message(prompt, widget_type, ctx, current, history)
    errors: list[str] = []
    for attempt in (1, 2):
        call_name, args = _call_model(contents, ctx, widget_type)

        if call_name == REJECT_TOOL:
            _audit(prompt, widget_type, attempt, call_name, args, [])
            raise SpecGenerationError(args.get("reason") or "La IA no pudo interpretar el pedido.")

        data = _resolve_columns(_normalize(args), ctx)
        errors = form_errors(data, ctx, widget_type, require_title=False)
        _audit(prompt, widget_type, attempt, call_name, args, errors)

        if not errors:
            # El form validado se re-parsea por el dataclass: lo que guarda es lo que se ejecuta.
            WidgetForm.from_dict({"fields": data["fields"], "style": data["style"]})
            return data

        contents = (
            f"{_user_message(prompt, widget_type, ctx, current, history)}\n\n"
            f"Tu respuesta anterior fue:\n{json.dumps(args, ensure_ascii=False)}\n\n"
            f"No es válida por estos errores:\n- " + "\n- ".join(errors) +
            "\n\nCorrígela y vuelve a llamar a create_widget."
        )

    raise SpecGenerationError(
        "No se pudo generar un widget válido para ese pedido. Detalle: " + " ".join(errors)
    )
