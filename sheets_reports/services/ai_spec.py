"""
Generación de specs de widget a partir de un prompt en lenguaje natural, vía Gemini con
function calling forzado. La IA NUNCA calcula números ni toca los datos: solo decide QUÉ
calcular, devolviendo un JSON que se valida contra un schema cerrado construido en cada
llamada desde las columnas reales de la hoja. Nada de lo que devuelve se ejecuta.
"""
import copy
import json
import logging

from django.conf import settings
from google import genai
from google.genai import types

from sheets_reports.services.spec_validation import (
    PIVOT_MULTIMETRIC_MSG,
    WIDGET_TYPES,
    build_data_spec_schema,
    build_view_spec,
    validate_widget_spec,
)

logger = logging.getLogger(__name__)
audit_logger = logging.getLogger("tableia.ai_audit")

GEMINI_TIMEOUT_MS = 120_000
CREATE_TOOL = "create_widget"
REJECT_TOOL = "reject_request"
# Claves de JSON Schema que se quitan de la declaración de la tool: condicionales que Gemini
# no soporta, y límites/patrones que inflan la gramática de decodificación hasta que Gemini la
# rechaza ("too many states for serving"). Las reglas que expresan se siguen aplicando: la
# validación real es la de jsonschema sobre el schema completo. Los `enum` de columnas SÍ se
# conservan: son los que impiden que la IA invente columnas.
_GEMINI_UNSUPPORTED_KEYS = {
    "allOf", "if", "then", "not", "$comment", "$schema", "maxItems", "minItems", "pattern", "maxLength",
    "minimum", "maximum",
}


class SpecGenerationError(Exception):
    """Error legible para el usuario: la IA no produjo un spec válido."""


SYSTEM_PROMPT = """\
Eres el asistente de un constructor de tableros de reportes sobre una hoja de cálculo. El
usuario describe en español un widget y tú respondes SIEMPRE llamando a una función:
- `create_widget` con la especificación del widget, o
- `reject_request` con un motivo breve y amable si el pedido no se puede representar
  (ej. pide una columna que no existe, o algo que no es un widget).

Tú NO calculas nada ni ves los datos: solo describes QUÉ calcular. El backend ejecuta la
consulta.

## data_spec
- dimensions: columnas por las que agrupar (categorías del eje X / filas de la tabla). En
  gráficos, UNA sola. En una tabla, hasta 3 anidadas de la más general a la más detallada
  (ej. ["sede", "carrera"]). Lista VACÍA solo para widget_type "kpi".
- pivots: lista de columnas para desagregar además de la dimensión (columnas en una tabla,
  series en un gráfico). [] si no aplica. Tabla: hasta 2 (ej. ["anio", "mes"]); gráficos: 1.
- filters: condiciones sobre las FILAS que entran al widget. [] si no hay.
- metrics: lista de métricas; cada una lleva "type" y un "as" único en snake_case
  (ej. "total_ventas", "cantidad"). Tipos:
  1. {"type": "agg", "as", "agg", "field", "show_as"?, "filters"?}: resume una columna.
     - agg "count": "cuántos", "cantidad de". Cuenta filas y NO lleva field.
     - agg "count_distinct": "cuántos distintos" de una columna (cualquier tipo).
     - agg "sum" / "avg" / "min" / "max" / "median": SOLO sobre columnas numéricas.
     - show_as (opcional, default "value"), como en las tablas dinámicas de Sheets:
       "pct_row" (% de su fila; solo con pivots), "pct_column" (% de su columna; sin pivots,
       cada grupo como % del total: "participación", "qué % representa cada..."),
       "pct_total" (% del total general). En un kpi, cualquier porcentaje es el valor con las
       condiciones (del widget y de la métrica) sobre el valor sin ellas: NECESITA condiciones.
     - filters propios (opcional): condiciones SOLO para esa métrica. Sirven para poner en el
       mismo widget "ventas 2026" y "ventas 2025", o "ventas de Hogar" junto al total.
  2. {"type": "calc", "as", "op", "left", "right"}: cálculo entre métricas ANTERIORES de la
     lista (left/right son su "as"; right también puede ser un número).
     op: "add", "sub", "mul", "div", "ratio_pct" (left/right×100: margen, % de cumplimiento),
     "diff_pct" ((left−right)/right×100: variación, crecimiento).
  3. {"type": "grouped", "as", "group_by", "inner", "having", "result", "value"?, "filters"?}:
     SOLO en kpi. Agrupa por `group_by`, calcula las métricas `inner` (agg o calc) de cada
     grupo, se queda con los grupos que cumplen `having` y los resume en un número:
     - result "count": cuántos grupos cumplen ("cuántos vendedores no cumplieron el plan").
     - result "pct_groups": qué % de los grupos cumple.
     - result "sum"/"avg"/"min"/"max": de la métrica interna `value` ("venta promedio por
       vendedor").
     - result "top"/"bottom": el grupo con el mayor/menor `value` ("la categoría que más
       vendió", "el vendedor con menos ventas"); el kpi muestra el nombre del grupo.
     `value` es el "as" de una métrica interna; count y pct_groups no lo llevan.
- having: condiciones sobre los GRUPOS de la primera dimensión, con las métricas del widget
  (solo widgets con dimensión): [{"left": "total_ventas", "op": "lt", "right": "total_plan"}]
  o contra un número ("right": 1000). op: eq ne lt lte gt gte. Ej. "vendedores que no
  llegaron a la meta" en una tabla o barras. [] si no aplica.
- sort: {by, dir} o null. by es la dimensión o el "as" de una métrica. En el kpi, null.
- limit: {"n": 5, "others": false} o null: Top N grupos de la dimensión ("top 5", "los 10
  mejores"). Requiere sort por una métrica. others true agrega el resto como «Otros».
- trend_by: solo kpi, columna para una mini tendencia bajo el número ("por mes"); o null.

## Condiciones (filters)
{"field", "op", "value"} o {"field", "op", "relative"}:
- op "eq"/"ne": igual/distinto de un valor. "lt"/"lte"/"gt"/"gte": comparación numérica
  (solo columnas numéricas). "in"/"not_in": lista de valores. "between": [desde, hasta]
  numérico. "contains": el texto contiene "value". "is_empty"/"not_empty": sin valor.
- relative (en vez de value) para valores que dependen de la fecha o de los datos:
  "current_year", "previous_year", "current_month" (1-12), "max" (el último valor de la
  columna: "el último año", "el periodo más reciente"), "second_max" (el anterior al
  último), "min". Úsalo para "este año", "el año actual", "el último mes" en vez de fijar
  un número.
Usa los valores de ejemplo de las columnas para escribir el valor exacto.

## Tipo de widget (si no viene fijado)
- kpi: uno o pocos números ("total de ventas", "cuántos vendedores...", "la categoría que
  más vendió", "ventas de este año vs el anterior"). Hasta 4 métricas.
- bar: comparar categorías ("ventas por región", "top 5 de productos").
- line: evolución en el tiempo ("por mes", "tendencia").
- donut: cómo se reparte un total entre pocas categorías. Una dimensión, UNA métrica, sin pivots.
- table: varias métricas por fila, detalle, o cuando el usuario pide "tabla"/"listado".
Con pivots, los gráficos admiten UNA métrica; si el usuario pide varias y un cruce en un
gráfico, incluye todo tal como lo pidió: el sistema le pedirá que elija.

## view_options
- title: título corto y claro para la tarjeta, en español.
- stacked: true solo si el usuario pide barras apiladas.
- labels: texto legible para cada métrica (`name` = su "as") y, si hace falta, para la
  dimensión (ej. {"name": "total_ventas", "label": "Total de ventas"}).
- kpi (solo kpi): {"primary": as de la métrica grande (por defecto la primera),
  "compare": as de la métrica contra la que se muestra la variación ▲/▼ ("vs el año
  anterior"), "compare_mode": "pct" | "abs", "target_metric": as de la meta, o
  "target_value": número de la meta ("meta de 50000"), "higher_is_better": false si menos es
  mejor (costos, quejas)}.

## Ejemplos (columnas ilustrativas; usa SOLO las columnas reales de la hoja)

Prompt: "Ventas de este año comparadas con el año anterior"
create_widget({"widget_type": "kpi", "title": "Ventas del año",
  "data_spec": {"dimensions": [], "pivots": [], "filters": [], "having": [], "sort": null,
    "limit": null, "trend_by": null,
    "metrics": [
      {"type": "agg", "as": "ventas_actual", "agg": "sum", "field": "ventas",
       "filters": [{"field": "anio", "op": "eq", "relative": "current_year"}]},
      {"type": "agg", "as": "ventas_anterior", "agg": "sum", "field": "ventas",
       "filters": [{"field": "anio", "op": "eq", "relative": "previous_year"}]}]},
  "view_options": {"stacked": false, "labels": [{"name": "ventas_actual", "label": "Ventas"},
    {"name": "ventas_anterior", "label": "Año anterior"}],
    "kpi": {"primary": "ventas_actual", "compare": "ventas_anterior", "compare_mode": "pct"}}})

Prompt: "Cuántos vendedores no cumplieron el plan de ventas"
create_widget({"widget_type": "kpi", "title": "Vendedores bajo el plan",
  "data_spec": {"dimensions": [], "pivots": [], "filters": [], "having": [], "sort": null,
    "limit": null, "trend_by": null,
    "metrics": [{"type": "grouped", "as": "vendedores_bajo_plan", "group_by": "vendedor",
      "inner": [{"type": "agg", "as": "ventas", "agg": "sum", "field": "ventas"},
                {"type": "agg", "as": "plan", "agg": "sum", "field": "plan"}],
      "having": [{"left": "ventas", "op": "lt", "right": "plan"}], "result": "count"}]},
  "view_options": {"stacked": false, "labels": [{"name": "vendedores_bajo_plan", "label": "Vendedores"}]}})

Prompt: "La categoría que más vendió"
create_widget({"widget_type": "kpi", "title": "Categoría líder",
  "data_spec": {"dimensions": [], "pivots": [], "filters": [], "having": [], "sort": null,
    "limit": null, "trend_by": null,
    "metrics": [{"type": "grouped", "as": "categoria_top", "group_by": "categoria",
      "inner": [{"type": "agg", "as": "ventas", "agg": "sum", "field": "ventas"}],
      "having": [], "result": "top", "value": "ventas"}]},
  "view_options": {"stacked": false, "labels": [{"name": "categoria_top", "label": "Ventas"}]}})

Prompt: "Margen de ganancia en porcentaje"
create_widget({"widget_type": "kpi", "title": "Margen",
  "data_spec": {"dimensions": [], "pivots": [], "filters": [], "having": [], "sort": null,
    "limit": null, "trend_by": null,
    "metrics": [{"type": "agg", "as": "ventas", "agg": "sum", "field": "ventas"},
                {"type": "agg", "as": "costo", "agg": "sum", "field": "costo"},
                {"type": "calc", "as": "ganancia", "op": "sub", "left": "ventas", "right": "costo"},
                {"type": "calc", "as": "margen", "op": "ratio_pct", "left": "ganancia", "right": "ventas"}]},
  "view_options": {"stacked": false, "labels": [{"name": "margen", "label": "Margen"}],
    "kpi": {"primary": "margen"}}})

Prompt: "Top 5 productos por ventas en 2026"
create_widget({"widget_type": "bar", "title": "Top 5 productos 2026",
  "data_spec": {"dimensions": ["producto"], "pivots": [], "having": [], "trend_by": null,
    "filters": [{"field": "anio", "op": "eq", "value": 2026}],
    "metrics": [{"type": "agg", "as": "total_ventas", "agg": "sum", "field": "ventas"}],
    "sort": {"by": "total_ventas", "dir": "desc"}, "limit": {"n": 5, "others": false}},
  "view_options": {"stacked": false, "labels": [{"name": "total_ventas", "label": "Ventas"}]}})

Prompt: "Tabla de vendedores que no llegaron a su plan, con ventas, plan y % de cumplimiento"
create_widget({"widget_type": "table", "title": "Vendedores bajo el plan",
  "data_spec": {"dimensions": ["vendedor"], "pivots": [], "filters": [], "limit": null, "trend_by": null,
    "metrics": [{"type": "agg", "as": "ventas", "agg": "sum", "field": "ventas"},
                {"type": "agg", "as": "plan", "agg": "sum", "field": "plan"},
                {"type": "calc", "as": "cumplimiento", "op": "ratio_pct", "left": "ventas", "right": "plan"}],
    "having": [{"left": "ventas", "op": "lt", "right": "plan"}],
    "sort": {"by": "cumplimiento", "dir": "asc"}},
  "view_options": {"stacked": false, "labels": [{"name": "cumplimiento", "label": "% cumplimiento"}]}})

Prompt: "Tabla de respuestas por categoría con la cantidad y el porcentaje de cada respuesta"
create_widget({"widget_type": "table", "title": "Respuestas por categoría",
  "data_spec": {"dimensions": ["categoria"], "pivots": ["respuesta"], "filters": [], "having": [],
    "sort": null, "limit": null, "trend_by": null,
    "metrics": [{"type": "agg", "as": "cantidad", "agg": "count"},
                {"type": "agg", "as": "pct_cantidad", "agg": "count", "show_as": "pct_row"}]},
  "view_options": {"stacked": false, "labels": [{"name": "cantidad", "label": "Cant."},
    {"name": "pct_cantidad", "label": "%"}]}})

Prompt: "Barras apiladas de ventas por categoría y por mes"
create_widget({"widget_type": "bar", "title": "Ventas por categoría y mes",
  "data_spec": {"dimensions": ["categoria"], "pivots": ["mes"], "filters": [], "having": [],
    "sort": null, "limit": null, "trend_by": null,
    "metrics": [{"type": "agg", "as": "total_ventas", "agg": "sum", "field": "ventas"}]},
  "view_options": {"stacked": true, "labels": [{"name": "total_ventas", "label": "Ventas"}]}})
"""


def gemini_client(api_key: str) -> genai.Client:
    return genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(
            timeout=GEMINI_TIMEOUT_MS,
            retry_options=types.HttpRetryOptions(attempts=2),
        ),
    )


def _to_gemini_schema(node):
    """Adapta el JSON Schema de validación al subconjunto que acepta la declaración de tools:
    quita condicionales/comentarios y reemplaza los schemas booleanos y los `type` múltiples."""
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


def build_tool_parameters(schema: dict, widget_type: str | None) -> dict:
    """Schema de parámetros de `create_widget`, construido en cada llamada: los `enum` de
    columnas salen de `schema` (columnas reales de la hoja)."""
    data_spec = _to_gemini_schema(build_data_spec_schema(schema, source=None, for_ai=True))
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["widget_type", "title", "data_spec", "view_options"],
        "properties": {
            "widget_type": {"enum": [widget_type] if widget_type else WIDGET_TYPES},
            "title": {"type": "string"},
            "data_spec": data_spec,
            "view_options": {
                "type": "object",
                "additionalProperties": False,
                "required": ["stacked", "labels"],
                "properties": {
                    "stacked": {"type": "boolean"},
                    "kpi": {
                        "type": "object",
                        "properties": {
                            "primary": {"type": "string"},
                            "compare": {"type": "string"},
                            "compare_mode": {"enum": ["pct", "abs"]},
                            "target_metric": {"type": "string"},
                            "target_value": {"type": "number"},
                            "higher_is_better": {"type": "boolean"},
                        },
                    },
                    "labels": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["name", "label"],
                            "properties": {
                                "name": {"type": "string", "description": "`as` de una métrica o nombre de columna"},
                                "label": {"type": "string"},
                            },
                        },
                    },
                },
            },
        },
    }


def _tools(schema: dict, widget_type: str | None) -> list[types.Tool]:
    return [types.Tool(function_declarations=[
        types.FunctionDeclaration(
            name=CREATE_TOOL,
            description="Crea la especificación (data_spec + opciones de vista) de un widget del tablero.",
            parameters_json_schema=build_tool_parameters(schema, widget_type),
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


def _columns_context(schema: dict) -> str:
    numeric = set(schema["numeric_fields"])
    samples = schema.get("sample_values") or {}
    lines = []
    for field in schema["all_fields"]:
        kind = "numérica" if field in numeric else "texto"
        line = f"- {json.dumps(field, ensure_ascii=False)} ({kind})"
        if samples.get(field):
            line += f" — valores de ejemplo: {json.dumps(samples[field], ensure_ascii=False)}"
        lines.append(line)
    return "Columnas de la hoja:\n" + "\n".join(lines)


def _user_message(prompt: str, widget_type: str | None, schema: dict) -> str:
    fixed = f"El tipo de widget está fijado en: {widget_type}.\n\n" if widget_type else ""
    return f"{_columns_context(schema)}\n\n{fixed}Pedido del usuario:\n{prompt}"


def _call_model(contents: str, schema: dict, widget_type: str | None) -> tuple[str, dict]:
    api_key = settings.GEMINI_API_KEY
    if not api_key:
        raise SpecGenerationError("GEMINI_API_KEY no está configurado.")
    # Mantener la referencia al cliente: si se recolecta, cierra su conexión HTTP a mitad de la llamada.
    client = gemini_client(api_key)
    response = client.models.generate_content(
        model=settings.GEMINI_MODEL,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            tools=_tools(schema, widget_type),
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


def _normalize(args: dict, source: str) -> tuple[str, dict, dict]:
    """Separa la respuesta de la tool en (widget_type, data_spec, opciones de vista)."""
    data_spec = copy.deepcopy(args.get("data_spec")) if isinstance(args.get("data_spec"), dict) else {}
    data_spec = {"source": source, **data_spec}
    view_options = args.get("view_options") if isinstance(args.get("view_options"), dict) else {}
    labels = {
        item["name"]: item["label"]
        for item in view_options.get("labels") or []
        if isinstance(item, dict) and isinstance(item.get("name"), str) and isinstance(item.get("label"), str)
    }
    options = {"title": args.get("title") or "", "stacked": bool(view_options.get("stacked")), "labels": labels}
    kpi = view_options.get("kpi")
    if isinstance(kpi, dict):
        options["kpi"] = {
            "primary": kpi.get("primary"),
            "compare": kpi.get("compare"),
            "compare_mode": kpi.get("compare_mode"),
            "target": kpi.get("target_metric") or kpi.get("target_value"),
            "higher_is_better": kpi.get("higher_is_better", True),
        }
    return args.get("widget_type"), data_spec, options


def generate_widget_spec(prompt: str, widget_type: str | None, schema: dict, source: str = "0") -> dict:
    """
    Genera {widget_type, data_spec, view_spec} para `prompt`. `schema` es el de
    sheets.get_sheet_schema (opcionalmente con "sample_values"); `source` es el gid de la hoja.

    Si el primer spec no pasa la validación, reintenta UNA vez pasándole a la IA los errores;
    si vuelve a fallar, lanza SpecGenerationError con un mensaje legible. Nunca devuelve un
    spec inválido.
    """
    if widget_type is not None and widget_type not in WIDGET_TYPES:
        raise SpecGenerationError(f"Tipo de widget desconocido: {widget_type}.")
    if not schema["all_fields"]:
        raise SpecGenerationError("La hoja no tiene columnas.")

    contents = _user_message(prompt, widget_type, schema)
    errors: list[str] = []
    for attempt in (1, 2):
        call_name, args = _call_model(contents, schema, widget_type)

        if call_name == REJECT_TOOL:
            _audit(prompt, widget_type, attempt, call_name, args, [])
            raise SpecGenerationError(args.get("reason") or "La IA no pudo interpretar el pedido.")

        resolved_type, data_spec, options = _normalize(args, source)
        if resolved_type not in WIDGET_TYPES:
            errors = [f"widget_type: '{resolved_type}' no es válido; usa uno de {', '.join(WIDGET_TYPES)}."]
        else:
            errors = validate_widget_spec(resolved_type, data_spec, schema, source)
        _audit(prompt, widget_type, attempt, call_name, args, errors)

        if not errors:
            return {
                "widget_type": resolved_type,
                "data_spec": data_spec,
                "view_spec": build_view_spec(resolved_type, data_spec, options),
            }

        if errors[0].startswith(PIVOT_MULTIMETRIC_MSG):
            # Regla de negocio: el usuario tiene que elegir; no dejamos que la IA elija por él.
            raise SpecGenerationError(errors[0])

        contents = (
            f"{_user_message(prompt, widget_type, schema)}\n\n"
            f"Tu respuesta anterior fue:\n{json.dumps(args, ensure_ascii=False)}\n\n"
            f"No es válida por estos errores:\n- " + "\n- ".join(errors) +
            "\n\nCorrígela y vuelve a llamar a create_widget."
        )

    raise SpecGenerationError(
        "No se pudo generar un widget válido para ese pedido. Detalle: " + " ".join(errors)
    )
