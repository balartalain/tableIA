"""Errores de validación del DSL y su traducción a mensajes legibles (para el usuario y para
la IA en el reintento)."""
import re

from jsonschema import Draft202012Validator

from sheets_reports.dsl.context import SheetContext


class SpecValidationError(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def unique(messages) -> list[str]:
    return list(dict.fromkeys(messages))


def _path(error) -> str:
    parts = []
    for p in error.absolute_path:
        if isinstance(p, int):
            parts[-1] = f"{parts[-1]}[{p}]" if parts else f"[{p}]"
        else:
            parts.append(str(p))
    return ".".join(parts) or "spec"


_METRIC_TYPE_PATH = re.compile(r"(^|\.)metrics\[\d+\]\.type$")


def _readable(error, ctx: SheetContext, parts) -> str:
    path = _path(error)
    # Primero la pieza dueña de la clave (dimensions, metrics, ...): conoce sus mensajes.
    key = path.split(".")[0].split("[")[0]
    part = next((p for p in parts if p.key == key), None)
    if part is not None:
        message = part.readable(error, path, ctx)
        if message:
            return message
    if error.validator == "enum" and path.endswith(("field", "group_by")):
        value = error.instance
        if value in ctx.fields and not ctx.is_numeric(value):
            return (f"{path}: la columna '{value}' no es numérica; solo se puede usar con "
                    f"agg 'count'/'count_distinct' o en condiciones de igualdad.")
        return f"{path}: la columna '{value}' no existe en la hoja."
    if error.validator == "enum" and _METRIC_TYPE_PATH.search(path):
        from sheets_reports.dsl.metrics import METRICS
        label = METRICS.get(error.instance).label if error.instance in METRICS else f"'{error.instance}'"
        return f"{path}: las métricas {label} no están disponibles en este tipo de widget."
    if error.validator == "pattern" and path.endswith(".as"):
        return f"{path}: '{error.instance}' debe ser snake_case en minúsculas (ej. 'total_ventas')."
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


def schema_errors(schema: dict, instance, ctx: SheetContext, parts=()) -> list[str]:
    """Errores de `instance` contra `schema`, como mensajes legibles y sin repetir (los if/then
    generan un error por rama). `parts`: las piezas del data_spec, que dan los mensajes de sus
    claves."""
    errors = sorted(Draft202012Validator(schema).iter_errors(instance), key=lambda e: list(e.absolute_path))
    return unique(_readable(e, ctx, parts) for e in _leaf_errors(errors))
