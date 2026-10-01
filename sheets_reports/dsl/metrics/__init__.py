"""Tipos de métrica. Importar este paquete registra los que trae el DSL; un tipo nuevo es un
módulo más con una subclase de Metric decorada con @METRICS.register."""
from sheets_reports.dsl.metrics.base import (  # noqa: F401
    METRICS,
    FlatContext,
    Metric,
    MetricCycleError,
    ScalarContext,
    UnsupportedEvaluation,
    evaluation_order,
    metric_union,
    metrics_errors,
    parse_metric,
)
from sheets_reports.dsl.metrics.agg import AggMetric  # noqa: F401
from sheets_reports.dsl.metrics.calc import CalcMetric  # noqa: F401
from sheets_reports.dsl.metrics.evaluation import flat_table, scalar_values  # noqa: F401
from sheets_reports.dsl.metrics.grouped import GROUP_RESULTS, GroupedMetric  # noqa: F401
