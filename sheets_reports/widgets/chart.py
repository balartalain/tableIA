"""Base de los gráficos con eje X y series (barras, líneas)."""
from sheets_reports.dsl.spec import DataSpec
from sheets_reports.engine.plans import PLANS, FlatResult, PivotChartResult
from sheets_reports.widgets.base import DataCapabilities, ViewOptions, WidgetType, percent_metrics
from sheets_reports.widgets.presentation import column_values


class ChartWidget(WidgetType[FlatResult | PivotChartResult, ViewOptions]):
    """Una dimensión en el eje X; sin pivote, una serie por métrica; con pivote, UNA métrica y
    una serie por cada valor del pivote."""
    capabilities = DataCapabilities(dimensions=(1, 1), pivots=(0, 1))

    def plan(self, spec):
        # La forma del dato depende del spec: con o sin pivote.
        return PLANS.get("pivot_chart" if spec.pivots else "flat")

    def data_view(self, spec: DataSpec) -> dict:
        return {
            "x": spec.dimensions[0] if spec.dimensions else None,
            "seriesBy": spec.pivots[0] if spec.pivots else None,
            "metrics": spec.aliases,
        }

    def compile(self, result, options, spec):
        """{"series": [...], "categories": [...], "percent"?: [nombres de serie]}."""
        percent = percent_metrics(spec)
        if isinstance(result, PivotChartResult):
            categories = result.dimension_values
            series = [
                {"name": str(p), "data": [result.rows[d][p][result.metric] for d in categories]}
                for p in result.pivot_values
            ]
            # Con pivote, todas las series son porcentaje si la métrica lo es.
            percent_series = [s["name"] for s in series] if result.metric in percent else []
        elif isinstance(result, FlatResult):
            categories = column_values(result.rows, result.dimension)
            series = [
                {"name": options.label(m), "data": column_values(result.rows, m)}
                for m in spec.aliases
            ]
            percent_series = [options.label(m) for m in spec.aliases if m in percent]
        else:
            raise TypeError(f"{type(self).__name__} no sabe compilar {type(result).__name__}")
        compiled = {"series": series, "categories": [str(c) for c in categories]}
        if percent_series:
            compiled["percent"] = percent_series
        return compiled
