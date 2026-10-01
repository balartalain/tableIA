from sheets_reports.dsl.spec import DataSpec
from sheets_reports.engine.plans import FlatResult
from sheets_reports.widgets.base import WIDGETS, DataCapabilities, ViewOptions, WidgetType
from sheets_reports.widgets.presentation import column_values


@WIDGETS.register
class DonutWidget(WidgetType[FlatResult, ViewOptions]):
    """Reparte UNA métrica entre las categorías de la dimensión: sin pivote."""
    key = "donut"
    label = "Gráfico de Dona"
    capabilities = DataCapabilities(dimensions=(1, 1), pivots=(0, 0), max_metrics=1)
    plan_key = "flat"

    def data_view(self, spec: DataSpec) -> dict:
        return {"x": spec.dimensions[0] if spec.dimensions else None, "metrics": spec.aliases}

    def compile(self, result: FlatResult, options, spec):
        """{"series": [valores], "labels": [categorías]} (formato nativo de ApexCharts)."""
        return {
            "series": column_values(result.rows, spec.aliases[0]),
            "labels": [str(v) for v in column_values(result.rows, result.dimension)],
        }
