"""La mini tendencia de un número (trend_by)."""
from sheets_reports.dsl.parts.base import SPEC_PARTS, SpecPart, column_message
from sheets_reports.dsl.schema import field_enum, nullable


@SPEC_PARTS.register
class TrendBy(SpecPart):
    """Columna de una mini tendencia (sparkline) bajo un número."""
    key = "trend_by"

    def schema(self, ctx, *, for_ai=False):
        return nullable(field_enum(ctx.fields))

    def names(self, value):
        return [value] if value else []

    def readable(self, error, path, ctx):
        return column_message(error, path, ctx) if error.validator == "enum" else None

    def absent_hint(self, widget):
        return f"trend_by: «{widget.label}» no muestra tendencia."

    def manifest(self):
        return {"trend": True}

    @classmethod
    def absent_manifest(cls):
        return {"trend": False}

    def describe(self):
        return ["trend_by"]
