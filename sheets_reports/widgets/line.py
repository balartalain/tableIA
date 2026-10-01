from sheets_reports.widgets.base import WIDGETS
from sheets_reports.widgets.chart import ChartWidget


@WIDGETS.register
class LineWidget(ChartWidget):
    key = "line"
    label = "Gráfico de Líneas"
