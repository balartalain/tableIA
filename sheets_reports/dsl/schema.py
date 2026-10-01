"""
Piezas comunes del JSON Schema del DSL. Los schemas se construyen en cada llamada a partir de
la hoja (SheetContext) y de los registros: nunca hay listas fijas de columnas ni de claves.

Sin $ref: todo va en línea para que el mismo dict sirva tanto a jsonschema como a la
declaración de la tool de Gemini (que además no soporta if/then: con `for_ai` las uniones
discriminadas se expresan con anyOf).
"""
AS_PATTERN = "^[a-z][a-z0-9_]{0,62}$"

SCALAR = {"type": ["string", "number", "boolean"]}
REF = {"type": "string", "pattern": AS_PATTERN}
# Lado derecho de una comparación o de un cálculo: otra métrica (su `as`) o un número.
REF_OR_NUMBER = {"anyOf": [REF, {"type": "number"}]}

# Límites globales del lenguaje; cada widget los acota más en sus capacidades.
MAX_METRICS = 5
MAX_FILTERS = 20
MAX_IN_VALUES = 200
# Valores de un `in` en los filtros del tablero (selector múltiple de la caja de filtros):
# tantos como opciones puede ofrecer un selector.
MAX_BOARD_IN_VALUES = 5000
MAX_HAVING = 5
MAX_INNER_METRICS = 3
MAX_LIMIT = 100
# Tablas dinámicas: filas y columnas anidadas.
MAX_DIMENSIONS = 3
MAX_PIVOTS = 2
# Columnas que se muestran tal cual, sin agrupar (tabla de datos).
MAX_COLUMNS = 50


def field_enum(fields) -> dict:
    return {"enum": list(fields)}


def enum_of(registry) -> dict:
    return {"enum": registry.keys()}


def union(branches: dict, *, for_ai: bool = False) -> dict:
    """Unión discriminada por "type". Para jsonschema va con if/then, así solo se reportan los
    errores de la rama que corresponde (un anyOf reportaría los de todas). Gemini no soporta
    if/then: para la IA va como anyOf (cada rama fija su "type")."""
    if for_ai:
        return {"anyOf": list(branches.values())}
    return {
        "type": "object",
        "required": ["type"],
        "properties": {"type": {"enum": list(branches)}},
        "allOf": [
            {"if": {"properties": {"type": {"const": name}}, "required": ["type"]}, "then": branch}
            for name, branch in branches.items()
        ],
    }


def nullable(schema: dict) -> dict:
    return {"anyOf": [{"type": "null"}, schema]}
