"""
Planes de resultado: cada forma de dato que puede necesitar un widget (un escalar, una tabla
plana, un cruce para gráficos, una tabla dinámica, ...) es un ResultPlan registrado en PLANS,
emparejado con su propia clase de resultado tipada. Un widget con una forma de datos nueva
agrega su plan en un archivo propio; nunca edita el ejecutor ni otro plan.
"""
from dataclasses import dataclass
from typing import ClassVar, Generic, TypeVar

import pandas as pd

from sheets_reports.dsl.ordering import OTHERS_LABEL, sorted_table  # noqa: F401
from sheets_reports.dsl.registry import Registry
from sheets_reports.dsl.spec import DataSpec


class ResultTooLargeError(ValueError):
    """El cruce pedido genera más celdas de las que se pueden mostrar."""


class PlanResult:
    """Marcador de los resultados de un plan (cada plan define el suyo como dataclass)."""


R = TypeVar("R", bound=PlanResult)


@dataclass
class PlanInput:
    df: pd.DataFrame          # filas con los filtros del widget (y Top N/having aplicados)
    universe: pd.DataFrame    # filas sin los filtros del widget: denominador de los %
    has_others: bool = False  # el Top N agrupó el resto como «Otros»


class ResultPlan(Generic[R]):
    key: ClassVar[str]
    # False: trabaja con filas crudas, sin agrupar (no se aplica Top N/having).
    aggregates: ClassVar[bool] = True

    def run(self, spec: DataSpec, data: PlanInput) -> R:
        raise NotImplementedError


PLANS: Registry[ResultPlan] = Registry("Plan de resultado")


def others_last(values: list, key=lambda v: v) -> list:
    return [v for v in values if key(v) != OTHERS_LABEL] + [v for v in values if key(v) == OTHERS_LABEL]

