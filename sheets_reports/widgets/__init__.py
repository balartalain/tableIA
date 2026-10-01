"""Tipos de widget. Importar este paquete registra los que trae la app; un widget nuevo es un
módulo más con una subclase de WidgetType decorada con @WIDGETS.register."""
from sheets_reports.widgets.base import WIDGETS, DataCapabilities, ViewOptions, WidgetType  # noqa: F401
from sheets_reports.widgets import kpi, bar, line, donut, table  # noqa: F401,E402
