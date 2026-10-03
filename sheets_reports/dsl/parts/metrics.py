"""La pieza `metrics`: qué se mide. Qué tipos de métrica, cuántas y si se pueden «mostrar como»
porcentaje lo decide cada widget al declararla."""
from sheets_reports.dsl.metrics import metric_union, metrics_errors, parse_metric
from sheets_reports.dsl.parts.base import SPEC_PARTS, SpecPart, bounds_text
from sheets_reports.dsl.rules import BUSINESS, METRICS, Rule, Stage
from sheets_reports.dsl.schema import MAX_METRICS

PIVOT_MULTIMETRIC_MSG = "Con pivote solo se permite una métrica"

DEFAULT_TYPES = frozenset({"agg", "calc"})


class PivotMultiMetric(Rule):
    """Pivote y varias métricas a la vez: el usuario tiene que elegir (ni la IA ni el backend
    eligen por él). Corre antes del schema para que este sea el único mensaje."""
    stage, priority, blocking = Stage.PRE_SCHEMA, BUSINESS, True

    def check(self, raw, widget, ctx):
        if not isinstance(raw, dict):
            return []
        pivots, metrics = raw.get("pivots"), raw.get("metrics")
        if pivots and isinstance(pivots, list) and isinstance(metrics, list) and len(metrics) > 1:
            return [f"{PIVOT_MULTIMETRIC_MSG}. Elige entre desagregar por «{', '.join(map(str, pivots))}» "
                    f"o mostrar varias métricas."]
        return []


class MetricsValid(Rule):
    """Alias únicos, reglas de cada métrica (que delega en Metric.validate) y sin ciclos."""
    priority = METRICS

    def check(self, spec, widget, ctx):
        return metrics_errors(spec.metrics, ctx, "metrics")


class AliasNotColumn(Rule):
    priority = METRICS

    def check(self, spec, widget, ctx):
        reserved = set(spec.get("dimensions", [])) | set(spec.get("pivots", []))
        return [f"metrics: el nombre '{name}' choca con el nombre de la dimensión o del pivote."
                for name in spec.aliases if name in reserved]


class ShowAsAllowed(Rule):
    """Widgets que calculan sus propios porcentajes al presentar (la dona): sus métricas van
    siempre como valor."""

    def check(self, spec, widget, ctx):
        return [f"metrics[{i}].show_as: «{widget.label}» ya muestra la participación de cada porción; "
                f"usa 'value' (el porcentaje se elige al presentar el gráfico)."
                for i, m in enumerate(spec.metrics) if m.show_as != "value"]


@SPEC_PARTS.register
class Metrics(SpecPart):
    key = "metrics"

    ui = "metric-list"
    panel_order = 40
    label = "Métricas"
    hint = "lo que se mide y cómo se muestra"

    def __init__(self, low: int = 1, high: int = MAX_METRICS, *, types=DEFAULT_TYPES,
                 show_as: bool = True, multi_with_pivot: bool = False):
        """`types`: claves de METRICS que admite (None: todas). `show_as`: métricas «mostradas
        como» porcentaje. `multi_with_pivot`: varias métricas a la vez que un pivote."""
        self.low, self.high = low, high
        self.types = None if types is None else frozenset(types)
        self.show_as = show_as
        self.multi_with_pivot = multi_with_pivot

    @classmethod
    def loose(cls):
        return cls(1, MAX_METRICS, types=None)

    def schema(self, ctx, *, for_ai=False):
        schema = {"type": "array", "maxItems": self.high,
                  "items": metric_union(ctx, only=self.types, for_ai=for_ai)}
        if self.low:
            schema["minItems"] = self.low
        return schema

    def parse(self, raw):
        return [parse_metric(m) for m in raw or []]

    def dump(self, value):
        return [m.to_dict() for m in value]

    def default(self):
        return []

    def names(self, value):
        return [m.alias for m in value]

    def sort_targets(self, value):
        return self.names(value)

    def resolve(self, value, df):
        return [m.resolved(df) for m in value]

    def rules(self):
        rules = [MetricsValid(), AliasNotColumn()]
        if not self.multi_with_pivot:
            rules.insert(0, PivotMultiMetric())
        if not self.show_as:
            rules.append(ShowAsAllowed())
        return rules

    def readable(self, error, path, ctx):
        if error.validator == "maxItems" and path == self.key:
            return f"{path}: se permiten como máximo {error.validator_value} métricas aquí."
        return None

    def absent_hint(self, widget):
        return f"{self.key}: este tipo de widget no lleva métricas (muestra los datos tal cual)."

    def manifest(self):
        return {
            "metrics": [self.low, self.high],
            "metric_types": sorted(self.types) if self.types is not None else [],
            "show_as": self.show_as,
            "multi_metric_with_pivot": self.multi_with_pivot,
        }

    def panel_fields(self, columns):
        return {"min": self.low, "max": self.high}

    @classmethod
    def absent_manifest(cls):
        return {"metrics": [0, 0], "metric_types": [], "show_as": True, "multi_metric_with_pivot": False}

    def describe(self):
        text = bounds_text(self.key, self.low, self.high)
        if self.types is not None:
            text += f" ({', '.join(sorted(self.types))})"
        return [text, "varias métricas con pivots"] if self.multi_with_pivot else [text]

    def missing(self):
        return [] if self.show_as else ["show_as"]
