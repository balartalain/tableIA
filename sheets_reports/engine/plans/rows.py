from dataclasses import dataclass

import pandas as pd

from sheets_reports.dsl.spec import DataSpec
from sheets_reports.engine.plans.base import PLANS, PlanInput, PlanResult, ResultPlan, sorted_table

# Filas que se envían al navegador: más no se pueden recorrer en una tarjeta y harían pesado
# el tablero. El resultado indica cuántas había en total.
MAX_ROWS = 5000


@dataclass(frozen=True)
class RowsResult(PlanResult):
    """Las filas de la hoja tal cual (con los filtros aplicados), solo con `columns`."""
    columns: list[str]
    rows: pd.DataFrame
    total_rows: int

    @property
    def truncated(self) -> bool:
        return self.total_rows > len(self.rows)


@PLANS.register
class RowsPlan(ResultPlan[RowsResult]):
    """Sin agrupar ni resumir: las filas que pasan los filtros, en el orden de la hoja salvo que
    `sort` diga otra cosa, hasta MAX_ROWS."""
    key = "rows"
    aggregates = False

    def run(self, spec: DataSpec, data: PlanInput) -> RowsResult:
        columns = list(spec.columns)
        table = data.df[columns]
        if spec.sort:
            table = sorted_table(data.df, spec.sort)[columns]
        return RowsResult(columns=columns, rows=table.head(MAX_ROWS).reset_index(drop=True),
                          total_rows=len(table))
