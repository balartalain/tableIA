import dataclasses
from dataclasses import dataclass, replace
from typing import ClassVar

import pandas as pd

from sheets_reports.dsl.aggregations import AGGREGATIONS, Aggregation
from sheets_reports.dsl.conditions import (
    Condition,
    apply_filters,
    conditions_errors,
    conditions_schema,
    parse_conditions,
)
from sheets_reports.dsl.context import SheetContext
from sheets_reports.dsl.metrics.base import METRICS, FlatContext, Metric, ScalarContext
from sheets_reports.dsl.schema import REF, enum_of, field_enum
from sheets_reports.dsl.values import percent, to_python

# "Mostrar como" de Sheets: el valor, o su % del total de la fila, la columna o el general.
SHOW_AS = ["value", "pct_row", "pct_column", "pct_total"]


@METRICS.register
@dataclass(frozen=True)
class AggMetric(Metric):
    """
    Resume una columna (o cuenta filas) con una Aggregation, opcionalmente sobre sus propias
    condiciones (como CALCULATE: "ventas de 2026" y "ventas de 2025" en el mismo widget).

    `show_as` (como "Mostrar como" en las tablas dinámicas de Sheets):
    - value: el valor resumido.
    - pct_row: % del total de su fila de la dimensión (solo cambia algo con pivote).
    - pct_column: % del total de su columna (sin pivote: del total de todos los grupos).
    - pct_total: % del total general.
    En un KPI cualquier porcentaje es: valor con las condiciones sobre el valor sin ellas.
    Los totales se calculan desde los datos (no sumando celdas): el total de un promedio es el
    promedio real.
    """
    key: ClassVar[str] = "agg"
    label: ClassVar[str] = "«de resumen»"

    alias: str
    agg: Aggregation
    field: str | None = None
    show_as: str = "value"
    filters: tuple[Condition, ...] = dataclasses.field(default_factory=tuple)

    @classmethod
    def schema(cls, ctx: SheetContext, *, for_ai=False, nested=False) -> dict:
        numeric_aggs = [a.key for a in AGGREGATIONS if a.numeric_only]
        properties = {
            "type": {"enum": [cls.key]},
            "as": REF,
            "agg": enum_of(AGGREGATIONS),
            "field": field_enum(ctx.fields),
            "show_as": {"enum": SHOW_AS},
        }
        if not nested:
            properties["filters"] = conditions_schema(ctx)
        return {
            "type": "object",
            "additionalProperties": False,
            "required": ["type", "as", "agg"],
            "properties": properties,
            "allOf": [{
                "$comment": "Las agregaciones numéricas solo van sobre columnas numéricas.",
                "if": {"properties": {"agg": {"enum": numeric_aggs}}},
                "then": {"properties": {"field": field_enum(ctx.ordered_numeric_fields)}},
            }],
        }

    @classmethod
    def from_dict(cls, raw):
        return cls(
            alias=raw["as"],
            agg=AGGREGATIONS.get(raw["agg"]),
            field=raw.get("field"),
            show_as=raw.get("show_as") or "value",
            filters=tuple(parse_conditions(raw.get("filters"))),
        )

    def to_dict(self):
        out = {"type": self.key, "as": self.alias, "agg": self.agg.key}
        if self.field is not None:
            out["field"] = self.field
        if self.show_as != "value":
            out["show_as"] = self.show_as
        if self.filters:
            out["filters"] = [c.to_dict() for c in self.filters]
        return out

    # --- reglas

    def validate(self, ctx, scope, path):
        errors = []
        if not self.agg.needs_field and self.field is not None:
            errors.append(f"{path}: '{self.agg.key}' cuenta filas y no lleva 'field'.")
        elif self.agg.needs_field and self.field is None:
            errors.append(f"{path}: falta 'field' (la columna a resumir).")
        return errors + conditions_errors(self.filters, ctx, f"{path}.filters")

    def is_percent(self):
        return self.show_as != "value"

    # --- preparación

    def resolved(self, df):
        if not self.filters:
            return self
        return replace(self, filters=tuple(c.resolved(df) for c in self.filters))

    def without_eq_filter_on(self, column):
        if not self.filters:
            return self
        return replace(self, filters=tuple(
            c for c in self.filters if not (c.field == column and c.op.key == "eq")
        ))

    # --- ejecución

    @property
    def empty_value(self):
        return self.agg.empty_value

    def frame(self, df: pd.DataFrame) -> pd.DataFrame:
        """Filas que resume: las recibidas recortadas por sus propias condiciones."""
        return apply_filters(df, self.filters) if self.filters else df

    def aggregate(self, df, by):
        return self.agg.by_group(self.frame(df).groupby(by, sort=False), self.field)

    def grand_total(self, df):
        return to_python(self.agg.total(self.frame(df), self.field))

    def scalar(self, ev: ScalarContext):
        part = self.agg.total(self.frame(ev.df), self.field)
        if self.show_as == "value":
            return to_python(part)
        return percent(part, self.agg.total(ev.universe, self.field))

    def flat(self, fc: FlatContext):
        frame = self.frame(fc.df)
        values = self.agg.by_group(frame.groupby(fc.dimension, sort=False), self.field).reindex(fc.keys)
        if self.empty_value is not None:
            values = values.fillna(self.empty_value)
        grand = self.agg.total(frame, self.field)
        if self.show_as == "value":
            return values, to_python(grand)
        if self.show_as == "pct_row":
            # Sin columnas de pivote, cada fila es el 100% de sí misma (como en Sheets).
            return values.where(values.isna(), 100.0), (100.0 if to_python(grand) is not None else None)
        shown = pd.Series([percent(v, grand) for v in values], index=fc.keys, dtype="float64")
        return shown, percent(grand, grand)
