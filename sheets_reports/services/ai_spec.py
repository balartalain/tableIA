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
_GEMINI_UNSUPPORTED_KEYS = {"allOf", "if", "then", "$comment", "$schema", "maxItems", "pattern", "maxLength"}


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
- dimensions: lista con UNA columna por la que agrupar (categorías del eje X / filas de la
  tabla). Lista VACÍA solo para widget_type "kpi" (un único número total).
- pivot: columna opcional para desagregar además de la dimensión (columnas en una tabla,
  series en un gráfico). null si no aplica.
- metrics: una o más métricas {field, agg, as}.
  - agg "count": "cuántos", "cantidad de", "número de", "conteo". Cuenta filas; usa como
    field la propia dimensión (o cualquier columna si no hay dimensión).
  - agg "sum": "total de", "suma de", "monto", "acumulado" sobre una columna numérica.
  - agg "avg": "promedio", "media", "en promedio" sobre una columna numérica.
  - sum y avg SOLO sobre columnas numéricas.
  - as: nombre de la columna resultante en snake_case minúsculas, único
    (ej. "total_ventas", "cantidad", "promedio_nota").
- filters: condiciones {field, op, value}; op en eq|ne|lt|lte|gt|gte|in. lt/lte/gt/gte solo
  sobre columnas numéricas con valor numérico. "in" lleva una lista de valores. Lista vacía si
  no hay filtros. Usa los valores de ejemplo de las columnas para escribir el valor exacto.
- sort: {by, dir} o null. by es la dimensión o el `as` de una métrica. "los más altos",
  "ranking", "de mayor a menor" -> desc por la métrica.

## Cuándo usar pivot
Cuando el usuario pide cruzar dos columnas: "ventas por categoría y por mes", "desglosado
por mes", "comparando cada región por año". La primera columna es la dimensión, la segunda el
pivot. Con pivot solo se permite UNA métrica: si el usuario pide varias métricas y además un
cruce, NO descartes nada de lo que pidió: incluye todas las métricas y el pivot tal como las
pidió; el sistema le pedirá que elija.

## Tipo de widget (si no viene fijado)
- kpi: un único número ("total de ventas", "cuántos estudiantes hay").
- bar: comparar categorías ("ventas por región", "top de productos").
- line: evolución en el tiempo ("por mes", "por semana", "tendencia").
- donut: cómo se reparte un total entre pocas categorías ("distribución", "proporción",
  "porcentaje del total", "participación"). Una dimensión, UNA métrica y sin pivot.
- table: varias métricas por fila, detalle, o cuando el usuario pide "tabla"/"listado".

## view_options
- title: título corto y claro para la tarjeta, en español.
- stacked: true solo si el usuario pide barras apiladas.
- labels: texto legible para cada métrica (`name` = su `as`) y, si hace falta, para la
  dimensión (ej. {"name": "total_ventas", "label": "Total de ventas"}).

## Ejemplos (columnas ilustrativas; usa SOLO las columnas reales de la hoja)

Prompt: "Total vendido en 2026"
create_widget({"widget_type": "kpi", "title": "Total vendido 2026",
  "data_spec": {"dimensions": [], "pivot": null,
    "metrics": [{"field": "ventas", "agg": "sum", "as": "total_ventas"}],
    "filters": [{"field": "anio", "op": "eq", "value": 2026}], "sort": null},
  "view_options": {"stacked": false, "labels": [{"name": "total_ventas", "label": "Total de ventas"}]}})

Prompt: "Barras apiladas de ventas por categoría y por mes"
create_widget({"widget_type": "bar", "title": "Ventas por categoría y mes",
  "data_spec": {"dimensions": ["categoria"], "pivot": "mes",
    "metrics": [{"field": "ventas", "agg": "sum", "as": "total_ventas"}],
    "filters": [], "sort": null},
  "view_options": {"stacked": true, "labels": [{"name": "total_ventas", "label": "Ventas"}]}})

Prompt: "Tabla con la cantidad de ventas y el promedio vendido por región, de mayor a menor cantidad"
create_widget({"widget_type": "table", "title": "Ventas por región",
  "data_spec": {"dimensions": ["region"], "pivot": null,
    "metrics": [{"field": "region", "agg": "count", "as": "cantidad"},
                {"field": "ventas", "agg": "avg", "as": "promedio_ventas"}],
    "filters": [], "sort": {"by": "cantidad", "dir": "desc"}},
  "view_options": {"stacked": false, "labels": [{"name": "region", "label": "Región"},
    {"name": "cantidad", "label": "Cantidad"}, {"name": "promedio_ventas", "label": "Promedio de ventas"}]}})

Prompt: "Evolución mensual de las ventas de Electrónica y Hogar con monto mayor a 100"
create_widget({"widget_type": "line", "title": "Ventas mensuales Electrónica y Hogar",
  "data_spec": {"dimensions": ["mes"], "pivot": null,
    "metrics": [{"field": "ventas", "agg": "sum", "as": "total_ventas"}],
    "filters": [{"field": "categoria", "op": "in", "value": ["Electrónica", "Hogar"]},
                {"field": "ventas", "op": "gt", "value": 100}],
    "sort": null},
  "view_options": {"stacked": false, "labels": [{"name": "total_ventas", "label": "Ventas"}]}})
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
    data_spec = _to_gemini_schema(build_data_spec_schema(schema, source=None))
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
