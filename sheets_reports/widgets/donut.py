from sheets_reports.dsl.parts import Metrics
from sheets_reports.dsl.spec import DataSpec, GroupedSpec, with_parts
from sheets_reports.engine.plans import FlatResult
from sheets_reports.widgets.base import WIDGETS, ViewOptions, WidgetType
from sheets_reports.widgets.presentation import column_values


class DonutSpec(GroupedSpec):
    """Reparte UNA métrica entre las categorías: sin pivote y sin «mostrar como» (el
    porcentaje de cada porción lo calcula ApexCharts)."""
    parts = with_parts(GroupedSpec, Metrics(1, 1, show_as=False), without=("pivots",))


@WIDGETS.register
class DonutWidget(WidgetType[FlatResult, ViewOptions]):
    """Reparte UNA métrica entre las categorías de la dimensión: sin pivote."""
    key = "donut"
    label = "Gráfico de Dona"
    spec_cls = DonutSpec
    plan_key = "flat"
    ai_doc = "cómo se reparte un total entre pocas categorías."

    def data_view(self, spec: DataSpec) -> dict:
        return {"x": spec.dimensions[0] if spec.dimensions else None, "metrics": spec.aliases}

    def compile(self, result: FlatResult, options, spec):
        """{"series": [valores], "labels": [categorías]} (formato nativo de ApexCharts)."""
        return {
            "series": column_values(result.rows, spec.aliases[0]),
            "labels": [str(v) for v in column_values(result.rows, result.dimension)],
        }
