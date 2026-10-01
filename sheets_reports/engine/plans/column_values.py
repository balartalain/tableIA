from dataclasses import dataclass, field

from sheets_reports.dsl.conditions import distinct_values
from sheets_reports.dsl.schema import MAX_BOARD_IN_VALUES
from sheets_reports.dsl.spec import DataSpec
from sheets_reports.engine.plans.base import PLANS, PlanInput, PlanResult, ResultPlan

# Opciones por columna: tantas como puede llevar después el filtro `in` del tablero.
MAX_OPTIONS = MAX_BOARD_IN_VALUES


@dataclass(frozen=True)
class ColumnValuesResult(PlanResult):
    """Valores distintos de cada columna de `columns` (opciones de un filtro), normalizados igual
    que los compara una condición. `truncated`: columnas con más de MAX_OPTIONS valores."""
    values: dict
    truncated: frozenset = field(default_factory=frozenset)


@PLANS.register
class ColumnValuesPlan(ResultPlan[ColumnValuesResult]):
    """Sin agrupar ni resumir: los valores posibles de cada columna, de toda la hoja que recibe."""
    key = "column_values"
    aggregates = False

    def run(self, spec: DataSpec, data: PlanInput) -> ColumnValuesResult:
        values, truncated = {}, set()
        for column in spec.columns:
            options = distinct_values(data.df[column])
            if len(options) > MAX_OPTIONS:
                truncated.add(column)
            values[column] = options[:MAX_OPTIONS]
        return ColumnValuesResult(values=values, truncated=frozenset(truncated))
