"""
API estable para los widgets de extensión (`sheets_reports/widgets/ext/<nombre>.py`).

Un widget de extensión es un único archivo que importa SOLO desde aquí (lo verifica
tests/test_architecture.py) y nunca de otra extensión: lo que dos extensiones repitan queda a
la vista y, si conviene, se sube al core y se publica en este módulo.

Lo que no está aquí es interno del core y puede cambiar sin aviso.
"""
from sheets_reports.dsl.context import SheetContext
from sheets_reports.dsl.errors import SpecValidationError
from sheets_reports.dsl.metrics import METRICS, Metric, flat_table, scalar_values
from sheets_reports.dsl.ordering import chronological
from sheets_reports.dsl.parts import (
    SPEC_PARTS,
    ColumnListPart,
    Columns,
    Dimensions,
    Filters,
    Having,
    Limit,
    Metrics,
    OrderBy,
    PanelColumns,
    Pivots,
    Rows,
    Sort,
    SpecPart,
    TopN,
    TrendBy,
    choices,
    column_message,
)
from sheets_reports.dsl.registry import Registry
from sheets_reports.dsl.rules import BUSINESS as PRIORITY_BUSINESS
from sheets_reports.dsl.rules import METRICS as PRIORITY_METRICS
from sheets_reports.dsl.rules import REFERENCES as PRIORITY_REFERENCES
from sheets_reports.dsl.rules import STRUCTURE as PRIORITY_STRUCTURE
from sheets_reports.dsl.rules import Rule, Stage
from sheets_reports.dsl.schema import (
    MAX_COLUMNS,
    MAX_DIMENSIONS,
    MAX_METRICS,
    MAX_PIVOTS,
    field_enum,
    nullable,
)
from sheets_reports.dsl.spec import DataSpec, GroupedSpec, RowsSpec, ScalarSpec, with_parts
from sheets_reports.dsl.values import is_number, percent, to_key, to_python
from sheets_reports.engine.plans import (
    OTHERS_LABEL,
    PLANS,
    ColumnValuesResult,
    FlatResult,
    PivotChartResult,
    PivotTableResult,
    PlanInput,
    PlanResult,
    ResultPlan,
    ResultTooLargeError,
    RowsResult,
    ScalarResult,
    Trend,
)
from sheets_reports.engine.plans.base import others_last, sorted_table
from sheets_reports.widgets.base import WIDGETS, ViewOptions, WidgetType, percent_metrics
from sheets_reports.widgets.chart import ChartOptions, ChartWidget
from sheets_reports.widgets.presentation import column_values, humanize, number

__all__ = [
    # Widgets
    "WIDGETS", "WidgetType", "ViewOptions", "ChartWidget", "ChartOptions", "percent_metrics",
    # data_spec: la base, las formas del core y sus piezas
    "DataSpec", "GroupedSpec", "ScalarSpec", "RowsSpec", "with_parts",
    "SpecPart", "SPEC_PARTS", "ColumnListPart", "Rows", "column_message", "PanelColumns", "choices",
    "Dimensions", "Pivots", "Columns", "Metrics", "Filters", "Having", "OrderBy", "TopN", "TrendBy",
    "Sort", "Limit", "SheetContext", "SpecValidationError",
    "MAX_COLUMNS", "MAX_DIMENSIONS", "MAX_METRICS", "MAX_PIVOTS", "field_enum", "nullable",
    # Reglas
    "Rule", "Stage", "PRIORITY_BUSINESS", "PRIORITY_STRUCTURE", "PRIORITY_METRICS", "PRIORITY_REFERENCES",
    # Métricas
    "METRICS", "Metric", "flat_table", "scalar_values",
    # Planes y resultados
    "PLANS", "ResultPlan", "PlanResult", "PlanInput", "ResultTooLargeError", "OTHERS_LABEL",
    "FlatResult", "PivotChartResult", "PivotTableResult", "RowsResult", "ScalarResult", "Trend",
    "ColumnValuesResult", "others_last", "sorted_table",
    # Valores y presentación
    "Registry", "chronological", "is_number", "percent", "to_key", "to_python",
    "column_values", "humanize", "number",
]
