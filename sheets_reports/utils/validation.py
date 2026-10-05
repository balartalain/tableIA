"""Errores de validación legibles (para el usuario y para la IA) y límites del lenguaje de consulta."""
from __future__ import annotations

import re
from jsonschema import Draft202012Validator

from sheets_reports.engine.context import SheetContext


class SpecValidationError(Exception):
    """La configuración del widget no es válida; `errors` son mensajes legibles."""
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def unique(messages) -> list[str]:
    return list(dict.fromkeys(messages))


def _path(error) -> str:
    parts: list[str] = []
    for p in error.absolute_path:
        if isinstance(p, int):
            parts[-1] = f"{parts[-1]}[{p}]" if parts else f"[{p}]"
        else:
            parts.append(str(p))
    return ".".join(parts) or "spec"


def _readable(error, ctx: SheetContext) -> str:
    path = _path(error)
    if error.validator == "enum" and path.endswith("field"):
        value = error.instance
        if value in ctx.fields and not ctx.is_numeric(value):
            return (f"{path}: la columna '{value}' no es numérica; solo se puede usar con "
                    f"agg 'count' o en condiciones de igualdad.")
        return f"{path}: la columna '{value}' no existe en la hoja."
    if error.validator == "pattern" and path.endswith("alias"):
        return f"{path}: '{error.instance}' debe ser snake_case en minúsculas (ej. 'total_ventas')."
    return f"{path}: {error.message}"


def _leaf_errors(errors):
    """Baja por los errores compuestos (anyOf) hasta los errores concretos."""
    for e in errors:
        if e.context:
            relevant = [c for c in e.context if not (c.validator == "type" and c.validator_value == "null")]
            yield from _leaf_errors(relevant or e.context)
        else:
            yield e


def schema_errors(schema: dict, instance, ctx: SheetContext) -> list[str]:
    """Errores de `instance` contra `schema`, como mensajes legibles y sin repetir."""
    errors = sorted(Draft202012Validator(schema).iter_errors(instance), key=lambda e: list(e.absolute_path))
    return unique(_readable(e, ctx) for e in _leaf_errors(errors))


# --- Límites y piezas comunes de los JSON Schema ---
SCALAR = {"type": ["string", "number", "boolean"]}

MAX_METRICS = 5
MAX_FILTERS = 20
MAX_IN_VALUES = 200
MAX_BOARD_IN_VALUES = 5000
MAX_LIMIT = 100
MAX_DIMENSIONS = 3
MAX_PIVOTS = 2
MAX_COLUMNS = 50

AS_PATTERN = r"^[a-z][a-z0-9_]{0,62}$"


def field_enum(fields) -> dict:
    return {"enum": list(fields)}


def enum_of(registry) -> dict:
    return {"enum": registry.keys()}
