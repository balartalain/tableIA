from dataclasses import dataclass

import pandas as pd

from sheets_reports.dsl.metrics import scalar_values
from sheets_reports.dsl.ordering import chronological
from sheets_reports.dsl.spec import DataSpec
from sheets_reports.dsl.values import to_key
from sheets_reports.engine.plans.base import PLANS, PlanInput, PlanResult, ResultPlan

# Grupos de la sparkline: más no se distinguen en una tarjeta.
MAX_TREND_POINTS = 60


@dataclass(frozen=True)
class Trend:
    categories: list
    series: dict  # {as: [valor por categoría]}


@dataclass(frozen=True)
class ScalarResult(PlanResult):
    """{as: valor} de cada métrica (una top/bottom vale {"label", "value"}) y, con trend_by,
    la serie de la sparkline."""
    values: dict
    trend: Trend | None = None


def chronological_keys(series: pd.Series) -> list:
    """Valores distintos de la columna de la tendencia, de lo más antiguo a lo más reciente
    (ver dsl/ordering.chronological). Nunca en el orden de la hoja: la línea tiene que mostrar
    la evolución aunque las filas vengan desordenadas."""
    keys = list(pd.unique(series.dropna()))
    if pd.api.types.is_numeric_dtype(series):
        # Comparar como números nativos (2026, no numpy.float64(2026.0)).
        by_key = {to_key(k): k for k in keys}
        return [by_key[k] for k in chronological(list(by_key))]
    return chronological(keys)


@PLANS.register
class ScalarPlan(ResultPlan[ScalarResult]):
    """Todas las filas resumidas en un valor por métrica (KPI)."""
    key = "scalar"

    def run(self, spec: DataSpec, data: PlanInput) -> ScalarResult:
        values = scalar_values(data.df, data.universe, spec.metrics)
        trend = self.trend(spec, data) if spec.trend_by else None
        return ScalarResult(values=values, trend=trend)

    def trend(self, spec: DataSpec, data: PlanInput) -> Trend:
        """El KPI calculado para cada valor de `trend_by`, de lo más antiguo a lo más reciente
        (con muchos valores, quedan los más recientes).

        Dentro de cada punto, una condición eq de una métrica sobre la misma columna no
        distingue nada (el punto ya fija ese valor) y solo sirve para vaciarlo: sin quitarla,
        "el último año" (resuelto UNA vez sobre toda la hoja) dejaría la serie con un único
        punto con datos, y el KPI de "este año vs el anterior" dibujaría una línea plana en
        cero. Los operadores ne/rango y las condiciones de otras columnas se conservan."""
        column = spec.trend_by
        keys = chronological_keys(data.df[column])[-MAX_TREND_POINTS:]
        metrics = [m.without_eq_filter_on(column) for m in spec.metrics if m.supports_trend()]
        series = {m.alias: [] for m in metrics}
        for key in keys:
            point = scalar_values(data.df[data.df[column] == key],
                                  data.universe[data.universe[column] == key], metrics)
            for name, value in point.items():
                series[name].append(value)
        return Trend(categories=[to_key(k) for k in keys], series=series)
