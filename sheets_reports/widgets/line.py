import dataclasses

from sheets_reports.dsl.ordering import chronological
from sheets_reports.engine.plans import OTHERS_LABEL, FlatResult, PivotChartResult
from sheets_reports.widgets.base import WIDGETS
from sheets_reports.widgets.chart import ChartWidget


def _ascending(values: list) -> list:
    """Categorías del eje X de lo más antiguo a lo más reciente; «Otros» (Top N) al final."""
    rest = [v for v in values if v != OTHERS_LABEL]
    return chronological(rest) + [v for v in values if v == OTHERS_LABEL]


@WIDGETS.register
class LineWidget(ChartWidget):
    key = "line"
    label = "Gráfico de Líneas"
    ai_doc = 'evolución en el tiempo ("por mes", "tendencia").'
    ai_examples = (
        ("Ventas por mes con una línea de meta en 50000 y el promedio", {
            "widget_type": "line", "title": "Ventas por mes",
            "data_spec": {"dimensions": ["mes"], "pivots": [], "columns": [], "filters": [], "having": [],
                          "sort": None, "limit": None, "trend_by": None,
                          "metrics": [{"type": "agg", "as": "total_ventas", "agg": "sum", "field": "ventas"}]},
            "view_options": {"labels": [{"name": "total_ventas", "label": "Ventas"}],
                             "reference_lines": [{"kind": "value", "value": 50000, "label": "Meta"},
                                                 {"kind": "avg", "series": "total_ventas", "label": "Promedio"}]},
        }),
    )

    def compile(self, result, options, spec):
        """Una línea muestra una evolución: sin un orden elegido en el panel, el eje X (casi
        siempre año, mes o fecha) va en orden cronológico ascendente, no en el de la hoja."""
        if spec.sort is None:
            result = self._chronological(result)
        return super().compile(result, options, spec)

    @staticmethod
    def _chronological(result):
        if isinstance(result, PivotChartResult):
            return dataclasses.replace(result, dimension_values=_ascending(result.dimension_values))
        if isinstance(result, FlatResult):
            rows = result.rows
            position = {v: i for i, v in enumerate(rows[result.dimension])}
            order = [position[v] for v in _ascending(list(position))]
            return dataclasses.replace(result, rows=rows.iloc[order].reset_index(drop=True))
        return result
