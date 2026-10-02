"""Piezas que recortan y ordenan lo que entra al widget: las filas (filters), los grupos
(having), el orden (sort) y los primeros N grupos (limit). Filters, having y limit son pasos
del pipeline, en ese orden."""
from dataclasses import dataclass

from sheets_reports.dsl.conditions import apply_filters, conditions_errors, conditions_schema, parse_conditions
from sheets_reports.dsl.groups import (
    group_conditions_errors,
    group_conditions_schema,
    having_mask,
    parse_group_conditions,
)
from sheets_reports.dsl.metrics import flat_table
from sheets_reports.dsl.ordering import OTHERS_LABEL, sorted_table
from sheets_reports.dsl.parts.base import SPEC_PARTS, Rows, SpecPart
from sheets_reports.dsl.rules import METRICS, REFERENCES, Rule
from sheets_reports.dsl.schema import MAX_LIMIT, nullable


def grouped_hint(widget, result: str) -> str:
    """Sugerencia para un widget que no agrupa pero admite métricas «por grupo» (el KPI)."""
    metrics = widget.spec_cls.part("metrics")
    if metrics is not None and metrics.types is not None and "grouped" in metrics.types:
        return f"; usa una métrica «por grupo» (type 'grouped'{result})."
    return "."


def _grouped_rows(spec, rows: Rows, aggregates: bool) -> bool:
    """Recortar grupos solo tiene sentido si el plan agrupa por la primera dimensión."""
    return aggregates and bool(spec.get("dimensions"))


# --- filters ---------------------------------------------------------------------------------

class ConditionsValid(Rule):
    priority = METRICS

    def check(self, spec, widget, ctx):
        return conditions_errors(spec.filters, ctx)


@SPEC_PARTS.register
class Filters(SpecPart):
    """Condiciones sobre las FILAS que entran al widget (WHERE)."""
    key = "filters"
    order = 10

    def schema(self, ctx, *, for_ai=False):
        return conditions_schema(ctx)

    def parse(self, raw):
        return parse_conditions(raw)

    def dump(self, value):
        return [c.to_dict() for c in value]

    def default(self):
        return []

    def resolve(self, value, df):
        return [c.resolved(df) for c in value]

    def prepare(self, spec, rows, *, aggregates):
        return Rows(apply_filters(rows.df, spec.filters), rows.has_others)

    def rules(self):
        return [ConditionsValid()]


# --- having ----------------------------------------------------------------------------------

class HavingRefsWidgetMetrics(Rule):
    priority = REFERENCES

    def check(self, spec, widget, ctx):
        return group_conditions_errors(spec.having, set(spec.aliases), "having")


@SPEC_PARTS.register
class Having(SpecPart):
    """Condiciones sobre los GRUPOS de la primera dimensión, con las métricas del widget."""
    key = "having"
    order = 20
    describe_absent = True

    def schema(self, ctx, *, for_ai=False):
        return group_conditions_schema()

    def parse(self, raw):
        return parse_group_conditions(raw)

    def dump(self, value):
        return [h.to_dict() for h in value]

    def default(self):
        return []

    def prepare(self, spec, rows, *, aggregates):
        """Se calculan las métricas por grupo y se recortan las FILAS a los grupos que cumplen:
        todo lo que sigue (Top N, pivotes, subtotales, %) se calcula sobre esos grupos."""
        if not (spec.having and _grouped_rows(spec, rows, aggregates)):
            return rows
        dimension = spec.dimensions[0]
        table = flat_table(rows.df, dimension, spec.metrics)
        kept = table[having_mask(table, spec.having)][dimension]
        return Rows(rows.df[rows.df[dimension].isin(kept)], rows.has_others)

    def rules(self):
        return [HavingRefsWidgetMetrics()]

    def absent_hint(self, widget):
        return f"having: «{widget.label}» no agrupa{grouped_hint(widget, '')}"

    def manifest(self):
        return {"having": True}

    @classmethod
    def absent_manifest(cls):
        return {"having": False}


# --- sort -----------------------------------------------------------------------------------

@dataclass(frozen=True)
class Sort:
    by: str
    dir: str

    @property
    def descending(self) -> bool:
        return self.dir == "desc"

    def to_dict(self) -> dict:
        return {"by": self.by, "dir": self.dir}


class SortTargetExists(Rule):
    """`sort.by` es algo por lo que se puede ordenar: lo dicen las demás piezas
    (sort_targets), así una pieza nueva puede ser destino de orden sin tocar esta."""
    priority = REFERENCES

    def check(self, spec, widget, ctx):
        if not spec.sort:
            return []
        targets = spec.sort_targets()
        if spec.sort.by not in targets:
            return [f"sort.by: '{spec.sort.by}' no es válido; usa una de: {', '.join(targets)}."]
        return []


@SPEC_PARTS.register
class OrderBy(SpecPart):
    key = "sort"
    describe_absent = True

    def schema(self, ctx, *, for_ai=False):
        return nullable({
            "type": "object",
            "additionalProperties": False,
            "required": ["by", "dir"],
            "properties": {"by": {"type": "string"}, "dir": {"enum": ["asc", "desc"]}},
        })

    def parse(self, raw):
        return Sort(raw["by"], raw["dir"]) if raw else None

    def dump(self, value):
        return value.to_dict() if value else None

    def rules(self):
        return [SortTargetExists()]

    def absent_hint(self, widget):
        return f"sort: «{widget.label}» no se ordena."

    def manifest(self):
        return {"sort": True}

    @classmethod
    def absent_manifest(cls):
        return {"sort": False}


# --- limit (Top N) ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Limit:
    n: int
    others: bool = False

    def to_dict(self) -> dict:
        return {"n": self.n, "others": self.others}


class LimitNeedsMetricSort(Rule):
    priority = REFERENCES

    def check(self, spec, widget, ctx):
        sort = spec.get("sort")
        if spec.limit and not (sort and sort.by in spec.aliases):
            return ["limit: el Top N necesita ordenar por una métrica (sort.by)."]
        return []


@SPEC_PARTS.register
class TopN(SpecPart):
    """Los N primeros grupos de la primera dimensión según `sort` (+ el resto como «Otros»)."""
    key = "limit"
    order = 30
    describe_absent = True

    def schema(self, ctx, *, for_ai=False):
        return nullable({
            "type": "object",
            "additionalProperties": False,
            "required": ["n"],
            "properties": {
                "n": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT},
                "others": {"type": "boolean"},
            },
        })

    def parse(self, raw):
        return Limit(raw["n"], bool(raw.get("others"))) if raw else None

    def dump(self, value):
        return value.to_dict() if value else None

    def prepare(self, spec, rows, *, aggregates):
        """Con `others`, los grupos fuera del Top N se renombran a «Otros» (sus valores se
        recalculan desde los datos, así funciona con cualquier agregación)."""
        if not (spec.limit and _grouped_rows(spec, rows, aggregates)):
            return rows
        dimension = spec.dimensions[0]
        table = flat_table(rows.df, dimension, spec.metrics)
        kept = list(table[dimension])
        top = list(sorted_table(table, spec.sort)[dimension][:spec.limit.n])
        rest = [k for k in kept if k not in set(top)]
        df = rows.df
        if not (spec.limit.others and rest):
            return Rows(df[df[dimension].isin(top)], rows.has_others)
        df = df[df[dimension].isin(kept)].copy()
        df[dimension] = df[dimension].astype(object).where(df[dimension].isin(top), OTHERS_LABEL)
        return Rows(df, True)

    def rules(self):
        return [LimitNeedsMetricSort()]

    def absent_hint(self, widget):
        return f"limit: «{widget.label}» no agrupa{grouped_hint(widget, ', result top')}"

    def manifest(self):
        return {"limit": True}

    @classmethod
    def absent_manifest(cls):
        return {"limit": False}
