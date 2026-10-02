"""Piezas que son listas de columnas de la hoja: las que agrupan (dimensions, pivots) y las
que se muestran tal cual (columns)."""
from sheets_reports.dsl.parts.base import SPEC_PARTS, ColumnListPart
from sheets_reports.dsl.rules import Rule
from sheets_reports.dsl.schema import MAX_COLUMNS, MAX_DIMENSIONS, MAX_PIVOTS


@SPEC_PARTS.register
class Dimensions(ColumnListPart):
    """Filas / eje X: columnas por las que se agrupa, de la más general a la más detallada."""
    key = "dimensions"
    limit = MAX_DIMENSIONS

    def sort_targets(self, value):
        return list(value)

    def readable(self, error, path, ctx):
        if error.validator == "maxItems" and path == self.key:
            if error.validator_value == 1:
                return f"{path}: solo se admite una dimensión (las tablas admiten hasta {MAX_DIMENSIONS})."
            return f"{path}: se admiten como máximo {error.validator_value} dimensiones."
        if error.validator == "minItems" and path == self.key:
            return f"{path}: se requiere una dimensión (campo por el que agrupar)."
        return super().readable(error, path, ctx)

    def absent_hint(self, widget):
        return f"{self.key}: este tipo de widget no admite dimensión."


class PivotNeedsDimension(Rule):
    def check(self, spec, widget, ctx):
        return ["pivots: con pivote hace falta una dimensión."] if spec.pivots and not spec.get("dimensions") else []


class PivotNotDimension(Rule):
    def check(self, spec, widget, ctx):
        if set(spec.pivots) & set(spec.get("dimensions", [])):
            return ["pivots: no puede ser la misma columna que la dimensión."]
        return []


@SPEC_PARTS.register
class Pivots(ColumnListPart):
    """Columnas de una tabla dinámica / series de un gráfico: desagregan además de la
    dimensión."""
    key = "pivots"
    limit = MAX_PIVOTS

    def readable(self, error, path, ctx):
        if error.validator == "maxItems" and path == self.key:
            if error.validator_value == 1:
                return f"{path}: los gráficos admiten un solo pivote (las tablas hasta {MAX_PIVOTS})."
            return f"{path}: se admiten como máximo {error.validator_value} columnas de pivote."
        return super().readable(error, path, ctx)

    def absent_hint(self, widget):
        return f"{self.key}: este tipo de widget no admite pivote."

    def rules(self):
        return [*super().rules(), PivotNotDimension(), PivotNeedsDimension()]


@SPEC_PARTS.register
class Columns(ColumnListPart):
    """Columnas que se muestran tal cual, sin agrupar, en orden (tabla de datos, filtros)."""
    key = "columns"
    limit = MAX_COLUMNS

    def sort_targets(self, value):
        return list(value)

    def readable(self, error, path, ctx):
        if error.validator == "minItems" and path == self.key:
            return f"{path}: elige al menos una columna para mostrar."
        if error.validator == "maxItems" and path == self.key:
            return f"{path}: se pueden mostrar como máximo {error.validator_value} columnas."
        return super().readable(error, path, ctx)

    def absent_hint(self, widget):
        return f"{self.key}: este tipo de widget no muestra columnas sueltas (agrupa con dimensiones)."
