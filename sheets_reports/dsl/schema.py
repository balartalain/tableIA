"""
Piezas comunes de los JSON Schema que se construyen en cada llamada a partir de la hoja
(`SheetContext`) y de los registros: nunca hay listas fijas de columnas ni de claves.
"""
AS_PATTERN = "^[a-z][a-z0-9_]{0,62}$"

SCALAR = {"type": ["string", "number", "boolean"]}

# Límites del lenguaje de consulta.
MAX_METRICS = 5
MAX_FILTERS = 20
MAX_IN_VALUES = 200
MAX_BOARD_IN_VALUES = 5000
MAX_LIMIT = 100
MAX_DIMENSIONS = 3
MAX_PIVOTS = 2
MAX_COLUMNS = 50


def field_enum(fields) -> dict:
    return {"enum": list(fields)}


def enum_of(registry) -> dict:
    return {"enum": registry.keys()}
