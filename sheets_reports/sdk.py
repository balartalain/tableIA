"""
API simplificada y estable para los widgets de extensión y core.
"""
from sheets_reports.engine import run_steps
from sheets_reports.widgets.base import BaseWidget
from sheets_reports.widgets import WIDGETS as WIDGET_REGISTRY
from sheets_reports.widgets.schemas import WidgetFields, WidgetForm, WidgetStyle

__all__ = [
    # Contratos
    "WidgetFields",
    "WidgetStyle",
    "WidgetForm",
    # Base y Registro
    "BaseWidget",
    "WIDGET_REGISTRY",
    # Motor
    "run_steps",
]