from dataclasses import dataclass

import pandas as pd

from sheets_reports.dsl.metrics import flat_table
from sheets_reports.dsl.spec import DataSpec
from sheets_reports.dsl.values import to_key
from sheets_reports.engine.plans.base import (
    OTHERS_LABEL,
    PLANS,
    PlanInput,
    PlanResult,
    ResultPlan,
    sorted_table,
)


@dataclass(frozen=True)
class FlatResult(PlanResult):
    """Una fila por valor de la dimensión (`rows`: la columna de la dimensión + una por métrica,
    en el orden de la hoja salvo que `sort` diga otra cosa; «Otros» al final) y el total general
    de cada métrica."""
    dimension: str
    rows: pd.DataFrame
    totals: dict


@PLANS.register
class FlatPlan(ResultPlan[FlatResult]):
    key = "flat"

    def run(self, spec: DataSpec, data: PlanInput) -> FlatResult:
        dimension = spec.dimensions[0]
        table = flat_table(data.df, dimension, spec.metrics)
        totals = table.attrs["totals"]
        if spec.sort:
            table = sorted_table(table, spec.sort)
        if data.has_others:
            table = pd.concat([table[table[dimension] != OTHERS_LABEL],
                               table[table[dimension] == OTHERS_LABEL]], ignore_index=True)
        table[dimension] = [to_key(v) for v in table[dimension]]
        table.attrs = {}
        return FlatResult(dimension=dimension, rows=table, totals=totals)
