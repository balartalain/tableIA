from dataclasses import dataclass, field, replace
from typing import ClassVar

import pandas as pd

from sheets_reports.dsl.conditions import (
    Condition,
    apply_filters,
    conditions_errors,
    conditions_schema,
    parse_conditions,
)
from sheets_reports.dsl.context import SheetContext
from sheets_reports.dsl.groups import (
    GroupCondition,
    group_conditions_errors,
    group_conditions_schema,
    having_mask,
    parse_group_conditions,
)
from sheets_reports.dsl.metrics.base import (
    METRICS,
    Metric,
    ScalarContext,
    metric_union,
    metrics_errors,
    parse_metric,
)
from sheets_reports.dsl.metrics.evaluation import flat_table
from sheets_reports.dsl.registry import Registry
from sheets_reports.dsl.schema import MAX_INNER_METRICS, REF, enum_of, field_enum
from sheets_reports.dsl.values import percent, to_key, to_python

GROUP_RESULTS: Registry["GroupResult"] = Registry("Resultado de grupo")


class GroupResult:
    """Cómo una métrica «por grupo» resume los grupos que cumplen `inner_having` en un valor."""
    key: ClassVar[str]
    # Usa una métrica interna (`value`); los que cuentan grupos no.
    needs_value: ClassVar[bool] = True
    # Devuelve un grupo ({label, value}) en vez de un número.
    ranking: ClassVar[bool] = False
    is_percent: ClassVar[bool] = False

    def summarize(self, matching: pd.DataFrame, table: pd.DataFrame, metric: "GroupedMetric"):
        raise NotImplementedError

    @staticmethod
    def values(matching: pd.DataFrame, metric: "GroupedMetric") -> pd.Series:
        return pd.to_numeric(matching[metric.value], errors="coerce")


@GROUP_RESULTS.register
class CountGroups(GroupResult):
    key, needs_value = "count", False

    def summarize(self, matching, table, metric):
        return len(matching)


@GROUP_RESULTS.register
class PctGroups(GroupResult):
    key, needs_value, is_percent = "pct_groups", False, True

    def summarize(self, matching, table, metric):
        return percent(len(matching), len(table))


class _Summary(GroupResult):
    """De la métrica interna `value` entre los grupos que cumplen."""
    pandas_method: ClassVar[str]

    def summarize(self, matching, table, metric):
        values = self.values(matching, metric)
        if values.dropna().empty:
            return None
        return to_python(getattr(values, self.pandas_method)())


@GROUP_RESULTS.register
class SumGroups(_Summary):
    key, pandas_method = "sum", "sum"


@GROUP_RESULTS.register
class AvgGroups(_Summary):
    key, pandas_method = "avg", "mean"


@GROUP_RESULTS.register
class MinGroups(_Summary):
    key, pandas_method = "min", "min"


@GROUP_RESULTS.register
class MaxGroups(_Summary):
    key, pandas_method = "max", "max"


class _Ranking(GroupResult):
    """El grupo con el mayor/menor `value`: {"label", "value"}; el primero de la hoja si hay
    empate (idxmax/idxmin)."""
    ranking = True
    pick: ClassVar[str]

    def summarize(self, matching, table, metric):
        values = self.values(matching, metric).dropna()
        if values.empty:
            return None
        index = getattr(values, self.pick)()
        return {"label": to_key(matching.loc[index, metric.group_by]), "value": to_python(values[index])}


@GROUP_RESULTS.register
class TopGroup(_Ranking):
    key, pick = "top", "idxmax"


@GROUP_RESULTS.register
class BottomGroup(_Ranking):
    key, pick = "bottom", "idxmin"


@METRICS.register
@dataclass(frozen=True)
class GroupedMetric(Metric):
    """
    Agrupa por `group_by`, calcula las métricas internas de cada grupo, se queda con los que
    cumplen `inner_having` y los resume en un valor (GroupResult):
      count / pct_groups      -> cuántos grupos cumplen (o qué % de los grupos)
      sum / avg / min / max   -> de la métrica interna `value` entre los que cumplen
      top / bottom            -> el grupo con el mayor/menor `value`: {"label", "value"}
    """
    key: ClassVar[str] = "grouped"
    label: ClassVar[str] = "«por grupo»"
    # Tipos de métrica que admite como internas.
    inner_types: ClassVar[tuple[str, ...]] = ("agg", "calc")

    alias: str
    group_by: str
    inner: tuple[Metric, ...]
    result: GroupResult
    inner_having: tuple[GroupCondition, ...] = field(default_factory=tuple)
    value: str | None = None
    filters: tuple[Condition, ...] = field(default_factory=tuple)

    @classmethod
    def schema(cls, ctx: SheetContext, *, for_ai=False, nested=False) -> dict:
        inner = metric_union(ctx, only=cls.inner_types, nested=True, for_ai=for_ai)
        return {
            "type": "object",
            "additionalProperties": False,
            "required": ["type", "as", "group_by", "inner", "inner_having", "result"],
            "properties": {
                "type": {"enum": [cls.key]},
                "as": REF,
                "group_by": field_enum(ctx.fields),
                "filters": conditions_schema(ctx),
                "inner": {"type": "array", "minItems": 1, "maxItems": MAX_INNER_METRICS, "items": inner},
                "inner_having": group_conditions_schema(),
                "result": enum_of(GROUP_RESULTS),
                "value": REF,
            },
        }

    @classmethod
    def from_dict(cls, raw):
        return cls(
            alias=raw["as"],
            group_by=raw["group_by"],
            inner=tuple(parse_metric(m) for m in raw["inner"]),
            result=GROUP_RESULTS.get(raw["result"]),
            inner_having=tuple(parse_group_conditions(raw.get("inner_having"))),
            value=raw.get("value"),
            filters=tuple(parse_conditions(raw.get("filters"))),
        )

    def to_dict(self):
        out = {
            "type": self.key, "as": self.alias, "group_by": self.group_by,
            "inner": [m.to_dict() for m in self.inner],
            "inner_having": [c.to_dict() for c in self.inner_having],
            "result": self.result.key,
        }
        if self.value is not None:
            out["value"] = self.value
        if self.filters:
            out["filters"] = [c.to_dict() for c in self.filters]
        return out

    def validate(self, ctx, scope, path):
        errors = metrics_errors(list(self.inner), ctx, f"{path}.inner")
        errors += conditions_errors(self.filters, ctx, f"{path}.filters")
        names = {m.alias for m in self.inner}
        errors += group_conditions_errors(list(self.inner_having), names, f"{path}.inner_having")
        if not self.result.needs_value:
            if self.value is not None:
                errors.append(f"{path}.value: '{self.result.key}' cuenta grupos y no lleva 'value'.")
        elif self.value not in names:
            errors.append(f"{path}.value: '{self.result.key}' necesita 'value' con una de sus métricas "
                          f"internas ({', '.join(sorted(names))}).")
        return errors

    def is_percent(self):
        return self.result.is_percent

    def is_numeric(self):
        return not self.result.ranking

    def resolved(self, df):
        return replace(
            self,
            inner=tuple(m.resolved(df) for m in self.inner),
            filters=tuple(c.resolved(df) for c in self.filters),
        )

    def without_eq_filter_on(self, column):
        if not self.filters:
            return self
        return replace(self, filters=tuple(
            c for c in self.filters if not (c.field == column and c.op.key == "eq")
        ))

    def scalar(self, ev: ScalarContext):
        frame = apply_filters(ev.df, self.filters) if self.filters else ev.df
        table = flat_table(frame, self.group_by, list(self.inner))
        matching = table[having_mask(table, list(self.inner_having))]
        return self.result.summarize(matching, table, self)
