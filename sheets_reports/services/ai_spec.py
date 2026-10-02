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

from sheets_reports.dsl.context import SheetContext
from sheets_reports.dsl.parts import PIVOT_MULTIMETRIC_MSG, SPEC_PARTS
from sheets_reports.dsl.spec import union_schema
from sheets_reports.widgets import WIDGETS, ViewOptions

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


CORE_PROMPT = """\
Eres el asistente de un constructor de tableros de reportes sobre una hoja de cálculo. El
usuario describe en español un widget y tú respondes SIEMPRE llamando a una función:
- `create_widget` con la especificación del widget, o
- `reject_request` con un motivo breve y amable si el pedido no se puede representar
  (ej. pide una columna que no existe, o algo que no es un widget).

Tú NO calculas nada ni ves los datos: solo describes QUÉ calcular. El backend ejecuta la
consulta.

## data_spec
Lleva siempre todas sus claves. Cada tipo de widget admite solo algunas (ver «Tipos de
widget»): las que no admite van vacías ([] o null).
- dimensions: columnas por las que agrupar (categorías del eje X / filas de la tabla).
- pivots: columnas para desagregar además de la dimensión (columnas en una tabla dinámica,
  series en un gráfico). [] si no aplica.
- columns: columnas de la hoja que se muestran tal cual, sin agrupar, en orden.
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
       "pct_total" (% del total general). Sin dimensiones (un número suelto), cualquier
       porcentaje es el valor con las condiciones (del widget y de la métrica) sobre el valor
       sin ellas: NECESITA condiciones.
     - filters propios (opcional): condiciones SOLO para esa métrica. Sirven para poner en el
       mismo widget "ventas 2026" y "ventas 2025", o "ventas de Hogar" junto al total.
  2. {"type": "calc", "as", "op", "left", "right"}: cálculo entre otras métricas de la lista
     (left/right son su "as"; right también puede ser un número). El orden de la lista no
     importa para el cálculo: solo decide el orden de las columnas.
     op: "add", "sub", "mul", "div", "ratio_pct" (left/right×100: margen, % de cumplimiento),
     "diff_pct" ((left−right)/right×100: variación, crecimiento).
  3. {"type": "grouped", "as", "group_by", "inner", "inner_having", "result", "value"?, "filters"?}:
     agrupa por `group_by`, calcula las métricas `inner` (agg o calc) de cada grupo, se queda
     con los grupos que cumplen `inner_having` (condiciones sobre sus métricas internas; no
     confundir con el `having` del widget) y los resume en un número:
     - result "count": cuántos grupos cumplen ("cuántos vendedores no cumplieron el plan").
     - result "pct_groups": qué % de los grupos cumple.
     - result "sum"/"avg"/"min"/"max": de la métrica interna `value` ("venta promedio por
       vendedor").
     - result "top"/"bottom": el grupo con el mayor/menor `value` ("la categoría que más
       vendió", "el vendedor con menos ventas"); el widget muestra el nombre del grupo.
     `value` es el "as" de una métrica interna; count y pct_groups no lo llevan.
- having: condiciones sobre los GRUPOS de la primera dimensión, con las métricas del widget:
  [{"left": "total_ventas", "op": "lt", "right": "total_plan"}] o contra un número
  ("right": 1000). op: eq ne lt lte gt gte. Ej. "vendedores que no llegaron a la meta" en una
  tabla o barras. [] si no aplica.
- sort: {by, dir} o null. by es una dimensión, una columna o el "as" de una métrica.
- limit: {"n": 5, "others": false} o null: Top N grupos de la dimensión ("top 5", "los 10
  mejores"). Requiere sort por una métrica. others true agrega el resto como «Otros».
- trend_by: columna para una mini tendencia bajo el número ("por mes"); o null.

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
"""

PIVOT_PROMPT = """\
Con pivots, los gráficos admiten UNA métrica; si el usuario pide varias y un cruce en un
gráfico, incluye todo tal como lo pidió: el sistema le pedirá que elija."""

VIEW_PROMPT = """\
- title: título corto y claro para la tarjeta, en español.
- labels: texto legible para cada métrica (`name` = su "as") y, si hace falta, para la
  dimensión (ej. {"name": "total_ventas", "label": "Total de ventas"})."""


def capabilities_text(widget) -> str:
    """Qué admite un widget, en una línea, desde las piezas de su data_spec: lo que describe
    cada una que tiene, y "sin X" por las que no tiene (las que lo piden) y por lo que alguna
    de las suyas no admite (ej. show_as en la dona)."""
    spec_cls = widget.spec_cls
    admits, without = [], []
    for part_cls in SPEC_PARTS:
        part = spec_cls.part(part_cls.key)
        if part is not None:
            admits += part.describe()
        elif part_cls.describe_absent:
            without.append(part_cls.key)
    without += [name for part in spec_cls.parts for name in part.missing()]
    text = ", ".join(admits)
    return f"{text}; sin {', '.join(without)}" if without else text


def _view_docs(widgets) -> list[str]:
    """El ai_doc de cada clase de opciones que lo define, con los widgets a los que aplica."""
    owners: dict[type, list[str]] = {}
    for widget in widgets:
        for cls in widget.options_cls.__mro__:
            if "ai_doc" in vars(cls) and cls.ai_doc:
                owners.setdefault(cls, []).append(widget.key)
    return [f"{cls.ai_doc} (solo {', '.join(keys)})" for cls, keys in owners.items()]


def build_system_prompt(widgets=None) -> str:
    """El prompt de la IA: lo del core más lo que declara cada widget que la IA puede proponer
    (cuándo usarlo, qué admite, sus opciones de vista y sus ejemplos)."""
    widgets = list(widgets) if widgets is not None else [w for w in WIDGETS if w.ai_enabled]
    types_section = [
        f"- {w.key}: {w.ai_doc + ' ' if w.ai_doc else ''}Admite: {capabilities_text(w)}." for w in widgets
    ]
    examples = [
        f'Prompt: "{prompt}"\ncreate_widget({json.dumps(args, ensure_ascii=False)})'
        for w in widgets for prompt, args in w.ai_examples
    ]
    return "\n".join([
        CORE_PROMPT,
        "## Tipos de widget (si no viene fijado)",
        *types_section,
        PIVOT_PROMPT,
        "",
        "## view_options",
        VIEW_PROMPT,
        *_view_docs(widgets),
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


def build_tool_parameters(ctx: SheetContext, widget_type: str | None) -> dict:
    """Schema de parámetros de `create_widget`, construido en cada llamada desde los registros:
    los `enum` de columnas salen de la hoja real, los tipos de widget de WIDGETS y las opciones
    de vista de cada ViewOptions. Con el tipo fijado, el data_spec trae sus capacidades."""
    widgets = [WIDGETS.get(widget_type)] if widget_type else [w for w in WIDGETS if w.ai_enabled]
    data_spec = (widgets[0].data_schema(ctx, for_ai=True) if widget_type
                 else union_schema([w.spec_cls for w in widgets], ctx, for_ai=True))
    view_properties = {
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
    }
    view_required = ["labels"]
    for widget in widgets:
        view_properties.update(widget.options_cls.ai_properties())
    # Con el tipo fijado, sus opciones obligatorias; sin fijar, ninguna (no aplican a todos).
    if widget_type:
        view_required += widgets[0].options_cls.ai_required()
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["widget_type", "title", "data_spec", "view_options"],
        "properties": {
            "widget_type": {"enum": [w.key for w in widgets]},
            "title": {"type": "string"},
            "data_spec": _to_gemini_schema(data_spec),
            "view_options": {
                "type": "object",
                "additionalProperties": False,
                "required": view_required,
                "properties": view_properties,
            },
        },
    }


def _tools(ctx: SheetContext, widget_type: str | None) -> list[types.Tool]:
    return [types.Tool(function_declarations=[
        types.FunctionDeclaration(
            name=CREATE_TOOL,
            description="Crea la especificación (data_spec + opciones de vista) de un widget del tablero.",
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


def _normalize(args: dict, source: str) -> tuple[str, dict, dict]:
    """Separa la respuesta de la tool en (widget_type, data_spec, opciones de vista en el
    formato del request del builder). Las opciones las traduce el `options_cls` del widget."""
    data_spec = copy.deepcopy(args.get("data_spec")) if isinstance(args.get("data_spec"), dict) else {}
    data_spec = {"source": source, **data_spec}
    view_options = args.get("view_options") if isinstance(args.get("view_options"), dict) else {}
    widget_type = args.get("widget_type")
    options_cls = WIDGETS.get(widget_type).options_cls if widget_type in WIDGETS else ViewOptions
    options = {"title": args.get("title") or "", **options_cls.from_ai(view_options)}
    return widget_type, data_spec, options


def generate_widget_spec(prompt: str, widget_type: str | None, ctx: SheetContext) -> dict:
    """
    Genera {widget_type, data_spec, view_spec} para `prompt` sobre la hoja de `ctx` (con
    `samples` para que la IA escriba los valores exactos).

    Si el primer spec no pasa la validación, reintenta UNA vez pasándole a la IA los errores;
    si vuelve a fallar, lanza SpecGenerationError con un mensaje legible. Nunca devuelve un
    spec inválido.
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

        resolved_type, raw, options = _normalize(args, ctx.source)
        if resolved_type not in WIDGETS or not WIDGETS.get(resolved_type).ai_enabled:
            allowed = ", ".join(w.key for w in WIDGETS if w.ai_enabled)
            errors = [f"widget_type: '{resolved_type}' no es válido; usa uno de {allowed}."]
        else:
            if widget_type is None:
                # Sin tipo fijado, la tool no exige ninguna clave: se completan las del elegido.
                raw = WIDGETS.get(resolved_type).spec_cls.with_defaults(raw)
            errors = WIDGETS.get(resolved_type).errors(raw, ctx)
        _audit(prompt, widget_type, attempt, call_name, args, errors)

        if not errors:
            definition = WIDGETS.get(resolved_type)
            spec = definition.spec_cls.from_dict(definition.spec_cls.normalize(raw))
            return {
                "widget_type": resolved_type,
                "data_spec": spec.to_dict(),
                "view_spec": definition.build_view(spec, definition.options(options)),
            }

        if errors[0].startswith(PIVOT_MULTIMETRIC_MSG):
            # Regla de negocio: el usuario tiene que elegir; no dejamos que la IA elija por él.
            raise SpecGenerationError(errors[0])

        contents = (
            f"{_user_message(prompt, widget_type, ctx)}\n\n"
            f"Tu respuesta anterior fue:\n{json.dumps(args, ensure_ascii=False)}\n\n"
            f"No es válida por estos errores:\n- " + "\n- ".join(errors) +
            "\n\nCorrígela y vuelve a llamar a create_widget."
        )

    raise SpecGenerationError(
        "No se pudo generar un widget válido para ese pedido. Detalle: " + " ".join(errors)
    )
