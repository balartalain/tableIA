"""
DataSpec: la consulta de un widget como objeto de valor tipado. Se guarda como JSON en
Widget.data_spec (to_dict / from_dict) y el código trabaja siempre con el objeto.

    data_spec = {
      "source": gid,
      "dimensions": [col, ...],        # filas / eje X (agrupa)
      "pivots": [col, ...],            # columnas / series (agrupa)
      "columns": [col, ...],           # columnas que se muestran tal cual, sin agrupar
      "filters": [Condition],          # filas que entran al widget (WHERE)
      "metrics": [Metric],             # unión discriminada por "type" (ver METRICS)
      "having": [GroupCondition],      # grupos de la primera dimensión que se muestran
      "sort": {"by", "dir"} | null,
      "limit": {"n", "others"} | null, # Top N grupos (+ el resto como «Otros»)
      "trend_by": col | null,          # serie de una tendencia (sparkline)
    }
"""
from dataclasses import dataclass, field, replace

import pandas as pd

from sheets_reports.dsl.conditions import Condition, conditions_schema, parse_conditions
from sheets_reports.dsl.context import SheetContext
from sheets_reports.dsl.groups import GroupCondition, group_conditions_schema, parse_group_conditions
from sheets_reports.dsl.metrics import Metric, metric_union, parse_metric
from sheets_reports.dsl.schema import (
    MAX_COLUMNS,
    MAX_DIMENSIONS,
    MAX_LIMIT,
    MAX_METRICS,
    MAX_PIVOTS,
    field_enum,
    nullable,
)

SPEC_KEYS = ["dimensions", "pivots", "columns", "filters", "metrics", "having", "sort", "limit", "trend_by"]


@dataclass(frozen=True)
class Sort:
    by: str
    dir: str

    @property
    def descending(self) -> bool:
        return self.dir == "desc"

    def to_dict(self) -> dict:
        return {"by": self.by, "dir": self.dir}


@dataclass(frozen=True)
class Limit:
    n: int
    others: bool = False

    def to_dict(self) -> dict:
        return {"n": self.n, "others": self.others}


def _columns_schema(fields, bounds: tuple[int, int]) -> dict:
    low, high = bounds
    schema = {"type": "array", "items": field_enum(fields), "maxItems": high}
    if low:
        schema["minItems"] = low
    return schema


@dataclass(frozen=True)
class DataSpec:
    source: str
    dimensions: list[str]
    pivots: list[str]
    metrics: list[Metric]
    columns: list[str] = field(default_factory=list)
    filters: list[Condition] = field(default_factory=list)
    having: list[GroupCondition] = field(default_factory=list)
    sort: Sort | None = None
    limit: Limit | None = None
    trend_by: str | None = None

    @staticmethod
    def schema(ctx: SheetContext, *, for_ai: bool = False,
               dimensions: tuple[int, int] = (0, MAX_DIMENSIONS),
               pivots: tuple[int, int] = (0, MAX_PIVOTS),
               columns: tuple[int, int] = (0, MAX_COLUMNS),
               metrics: tuple[int, int] = (1, MAX_METRICS),
               metric_types=None) -> dict:
        """
        JSON Schema de `data_spec`, con los límites que pida quien lo usa (cada widget pasa sus
        capacidades). Con `for_ai` se omite `source` (el backend la completa) y las uniones van
        como anyOf.
        """
        min_metrics, max_metrics = metrics
        if metric_types is not None and not metric_types:
            # Sin tipos de métrica (ej. la tabla de datos): la lista va vacía.
            metrics_schema = {"type": "array", "maxItems": 0, "items": {"type": "object"}}
        else:
            metrics_schema = {
                "type": "array", "maxItems": max_metrics,
                "items": metric_union(ctx, only=metric_types, for_ai=for_ai),
            }
            if min_metrics:
                metrics_schema["minItems"] = min_metrics
        properties = {
            "dimensions": _columns_schema(ctx.fields, dimensions),
            "pivots": _columns_schema(ctx.fields, pivots),
            "columns": _columns_schema(ctx.fields, columns),
            "filters": conditions_schema(ctx),
            "metrics": metrics_schema,
            "having": group_conditions_schema(),
            "sort": nullable({
                "type": "object",
                "additionalProperties": False,
                "required": ["by", "dir"],
                "properties": {"by": {"type": "string"}, "dir": {"enum": ["asc", "desc"]}},
            }),
            "limit": nullable({
                "type": "object",
                "additionalProperties": False,
                "required": ["n"],
                "properties": {
                    "n": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT},
                    "others": {"type": "boolean"},
                },
            }),
            "trend_by": nullable(field_enum(ctx.fields)),
        }
        required = list(SPEC_KEYS)
        if not for_ai:
            properties = {"source": {"const": ctx.source}, **properties}
            required = ["source", *required]
        return {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "additionalProperties": False,
            "required": required,
            "properties": properties,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "DataSpec":
        """Desde un dict que ya pasó el schema (o uno guardado, que lo pasó al guardarse)."""
        sort, limit = raw.get("sort"), raw.get("limit")
        return cls(
            source=str(raw.get("source", "")),
            dimensions=list(raw.get("dimensions") or []),
            pivots=list(raw.get("pivots") or []),
            columns=list(raw.get("columns") or []),
            metrics=[parse_metric(m) for m in raw.get("metrics") or []],
            filters=parse_conditions(raw.get("filters")),
            having=parse_group_conditions(raw.get("having")),
            sort=Sort(sort["by"], sort["dir"]) if sort else None,
            limit=Limit(limit["n"], bool(limit.get("others"))) if limit else None,
            trend_by=raw.get("trend_by"),
        )

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "dimensions": list(self.dimensions),
            "pivots": list(self.pivots),
            "columns": list(self.columns),
            "filters": [c.to_dict() for c in self.filters],
            "metrics": [m.to_dict() for m in self.metrics],
            "having": [h.to_dict() for h in self.having],
            "sort": self.sort.to_dict() if self.sort else None,
            "limit": self.limit.to_dict() if self.limit else None,
            "trend_by": self.trend_by,
        }

    @property
    def aliases(self) -> list[str]:
        return [m.alias for m in self.metrics]

    def metric(self, alias: str) -> Metric | None:
        return next((m for m in self.metrics if m.alias == alias), None)

    def resolved(self, df: pd.DataFrame) -> "DataSpec":
        """Copia con cada valor relativo convertido en su valor concreto, calculado UNA vez
        sobre `df` (la hoja con los filtros del tablero): "el último año" es el de la hoja, no
        el de cada grupo o punto de la tendencia."""
        return replace(
            self,
            filters=[c.resolved(df) for c in self.filters],
            metrics=[m.resolved(df) for m in self.metrics],
        )
