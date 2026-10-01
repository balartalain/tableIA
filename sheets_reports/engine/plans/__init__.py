"""Formas de resultado. Importar este paquete registra los planes que trae el motor; una forma
nueva es un módulo más con un ResultPlan decorado con @PLANS.register y su PlanResult."""
from sheets_reports.engine.plans.base import (  # noqa: F401
    OTHERS_LABEL,
    PLANS,
    PlanInput,
    PlanResult,
    ResultPlan,
    ResultTooLargeError,
)
from sheets_reports.engine.plans.flat import FlatPlan, FlatResult  # noqa: F401
from sheets_reports.engine.plans.pivot_chart import PivotChartPlan, PivotChartResult  # noqa: F401
from sheets_reports.engine.plans.pivot_table import (  # noqa: F401
    PivotBlock,
    PivotRow,
    PivotTablePlan,
    PivotTableResult,
)
from sheets_reports.engine.plans.scalar import ScalarPlan, ScalarResult, Trend  # noqa: F401
