"""
Reglas semánticas de un data_spec: lo que el JSON Schema no puede expresar (referencias entre
métricas, columnas repetidas, capacidades del widget...). Una clase por regla.

El orden en que se reportan los errores está declarado, no depende de dónde se agregue cada
regla a la lista: cada regla dice su etapa, su prioridad (bandas de abajo) y si corta la
validación. WidgetType.validate las ordena por (stage, priority); los empates conservan el
orden de rules().

Las reglas reciben el widget para consultar sus capacidades (`widget.capabilities`) y su
nombre (`widget.label`); nunca preguntan por un widget concreto.
"""
from enum import IntEnum
from typing import ClassVar

from sheets_reports.dsl.conditions import conditions_errors
from sheets_reports.dsl.context import SheetContext
from sheets_reports.dsl.groups import group_conditions_errors
from sheets_reports.dsl.metrics import metrics_errors


class Stage(IntEnum):
    PRE_SCHEMA = 0     # sobre el dict crudo, antes de jsonschema
    SEMANTIC = 1       # sobre DataSpec


# Bandas de prioridad (menor = antes).
BUSINESS = 0       # reglas de negocio con mensaje para el usuario
STRUCTURE = 100    # columnas, dimensiones, pivotes y capacidades del widget
METRICS = 200      # condiciones y métricas
REFERENCES = 300   # lo que apunta a métricas ya validadas


class Rule:
    stage: ClassVar[Stage] = Stage.SEMANTIC
    priority: ClassVar[int] = STRUCTURE
    # Si falla, se devuelven solo sus errores (y no se sigue validando).
    blocking: ClassVar[bool] = False

    def check(self, spec, widget, ctx: SheetContext) -> list[str]:
        """`spec` es el dict crudo en PRE_SCHEMA y un DataSpec en SEMANTIC."""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Negocio
# ---------------------------------------------------------------------------

PIVOT_MULTIMETRIC_MSG = "Con pivote solo se permite una métrica"


class PivotMultiMetric(Rule):
    """Pivote y varias métricas a la vez: el usuario tiene que elegir (ni la IA ni el backend
    eligen por él). Corre antes del schema para que este sea el único mensaje."""
    stage, priority, blocking = Stage.PRE_SCHEMA, BUSINESS, True

    def check(self, raw, widget, ctx):
        if widget.capabilities.multi_metric_with_pivot or not isinstance(raw, dict):
            return []
        pivots, metrics = raw.get("pivots"), raw.get("metrics")
        if pivots and isinstance(pivots, list) and isinstance(metrics, list) and len(metrics) > 1:
            return [f"{PIVOT_MULTIMETRIC_MSG}. Elige entre desagregar por «{', '.join(map(str, pivots))}» "
                    f"o mostrar varias métricas."]
        return []


# ---------------------------------------------------------------------------
# Estructura
# ---------------------------------------------------------------------------

class NoColumnRepeated(Rule):
    def check(self, spec, widget, ctx):
        errors = []
        if len(set(spec.dimensions)) < len(spec.dimensions):
            errors.append("dimensions: no se puede repetir una columna.")
        if len(set(spec.pivots)) < len(spec.pivots):
            errors.append("pivots: no se puede repetir una columna.")
        if set(spec.pivots) & set(spec.dimensions):
            errors.append("pivots: no puede ser la misma columna que la dimensión.")
        if len(set(spec.columns)) < len(spec.columns):
            errors.append("columns: no se puede repetir una columna.")
        return errors


class PivotNeedsDimension(Rule):
    def check(self, spec, widget, ctx):
        return ["pivots: con pivote hace falta una dimensión."] if spec.pivots and not spec.dimensions else []


def _grouped_hint(widget, result: str) -> str:
    if "grouped" in widget.capabilities.metric_types:
        return f"; usa una métrica «por grupo» (type 'grouped'{result})."
    return "."


class HavingAllowed(Rule):
    def check(self, spec, widget, ctx):
        if spec.having and not widget.capabilities.having:
            return [f"having: «{widget.label}» no agrupa{_grouped_hint(widget, '')}"]
        return []


class LimitAllowed(Rule):
    def check(self, spec, widget, ctx):
        if spec.limit and not widget.capabilities.limit:
            return [f"limit: «{widget.label}» no agrupa{_grouped_hint(widget, ', result top')}"]
        return []


class SortAllowed(Rule):
    def check(self, spec, widget, ctx):
        if spec.sort and not widget.capabilities.sort:
            return [f"sort: «{widget.label}» no se ordena."]
        return []


class TrendAllowed(Rule):
    def check(self, spec, widget, ctx):
        if spec.trend_by and not widget.capabilities.trend:
            return [f"trend_by: «{widget.label}» no muestra tendencia."]
        return []


# ---------------------------------------------------------------------------
# Métricas y condiciones
# ---------------------------------------------------------------------------

class ConditionsValid(Rule):
    priority = METRICS

    def check(self, spec, widget, ctx):
        return conditions_errors(spec.filters, ctx)


class MetricsValid(Rule):
    """Alias únicos, reglas de cada métrica (que delega en Metric.validate) y sin ciclos."""
    priority = METRICS

    def check(self, spec, widget, ctx):
        return metrics_errors(spec.metrics, ctx, "metrics")


class AliasNotColumn(Rule):
    priority = METRICS

    def check(self, spec, widget, ctx):
        reserved = set(spec.dimensions) | set(spec.pivots)
        return [f"metrics: el nombre '{name}' choca con el nombre de la dimensión o del pivote."
                for name in spec.aliases if name in reserved]


# ---------------------------------------------------------------------------
# Referencias
# ---------------------------------------------------------------------------

class HavingRefsWidgetMetrics(Rule):
    priority = REFERENCES

    def check(self, spec, widget, ctx):
        if not widget.capabilities.having:
            return []
        return group_conditions_errors(spec.having, set(spec.aliases), "having")


class SortTargetExists(Rule):
    priority = REFERENCES

    def check(self, spec, widget, ctx):
        if not (spec.sort and widget.capabilities.sort):
            return []
        targets = [*spec.dimensions, *spec.columns, *spec.aliases]
        if spec.sort.by not in targets:
            allowed = ", ".join(targets)
            return [f"sort.by: '{spec.sort.by}' no es válido; usa una de: {allowed}."]
        return []


class LimitNeedsMetricSort(Rule):
    priority = REFERENCES

    def check(self, spec, widget, ctx):
        if spec.limit and widget.capabilities.limit and not (spec.sort and spec.sort.by in spec.aliases):
            return ["limit: el Top N necesita ordenar por una métrica (sort.by)."]
        return []


DEFAULT_RULES: list[Rule] = [
    PivotMultiMetric(),
    NoColumnRepeated(),
    PivotNeedsDimension(),
    HavingAllowed(),
    LimitAllowed(),
    SortAllowed(),
    TrendAllowed(),
    ConditionsValid(),
    MetricsValid(),
    AliasNotColumn(),
    HavingRefsWidgetMetrics(),
    SortTargetExists(),
    LimitNeedsMetricSort(),
]
