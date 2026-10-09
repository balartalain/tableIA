"""Tipos de widget. Importar este paquete registra los que trae la app; un widget nuevo es un
módulo más con una subclase de BaseWidget decorada con @WIDGETS.register."""
from sheets_reports.widgets.base import WIDGETS, BaseWidget  # noqa: F401
from sheets_reports.widgets import kpi, bar, line, donut, dynamic_table, table, filter, ranking, correlation, scatter  # noqa: F401,E402