import re
import unicodedata
from dataclasses import dataclass

import pandas as pd

from sheets_reports.dsl.metrics import scalar_values
from sheets_reports.dsl.spec import DataSpec
from sheets_reports.dsl.values import sort_key, to_key
from sheets_reports.engine.plans.base import PLANS, PlanInput, PlanResult, ResultPlan

# Grupos de la sparkline: más no se distinguen en una tarjeta.
MAX_TREND_POINTS = 60
# Número de mes de sus nombres (español e inglés, completos y abreviados) para ordenar una
# tendencia por una columna de meses en texto.
_MONTHS = [
    ("enero", "ene", "january", "jan"), ("febrero", "feb", "february"), ("marzo", "mar", "march"),
    ("abril", "abr", "april", "apr"), ("mayo", "may"), ("junio", "jun", "june"),
    ("julio", "jul", "july"), ("agosto", "ago", "august", "aug"),
    ("septiembre", "setiembre", "sep", "sept", "set", "september"), ("octubre", "oct", "october"),
    ("noviembre", "nov", "november"), ("diciembre", "dic", "december", "dec"),
]
MONTH_ORDER = {name: number for number, names in enumerate(_MONTHS, start=1) for name in names}


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


def _plain(text) -> str:
    """Texto en minúsculas, sin acentos ni punto final ("Sept." -> "sept")."""
    text = unicodedata.normalize("NFD", str(text).strip().lower())
    return "".join(c for c in text if unicodedata.category(c) != "Mn").rstrip(".")


def _natural_key(value):
    """Orden natural: los números dentro del texto se comparan como números
    ("2025-2" antes que "2025-10")."""
    return [(0, int(part), "") if part.isdigit() else (1, 0, part)
            for part in re.split(r"(\d+)", _plain(value)) if part]


def chronological_keys(series: pd.Series) -> list:
    """
    Valores distintos de la columna de la tendencia, de lo más antiguo a lo más reciente.
    Nunca en el orden de la hoja: la línea tiene que mostrar la evolución aunque las filas
    vengan desordenadas. Números de menor a mayor; nombres de mes del 1 al 12; fechas en
    texto por fecha; cualquier otro texto (periodos "2025-1", "2026-T1"...) en orden natural.
    """
    keys = list(pd.unique(series.dropna()))
    if not keys:
        return keys
    if pd.api.types.is_numeric_dtype(series):
        return sorted(keys, key=lambda k: sort_key(to_key(k)))
    if all(_plain(k) in MONTH_ORDER for k in keys):
        return sorted(keys, key=lambda k: MONTH_ORDER[_plain(k)])
    dates = pd.to_datetime(pd.Series(keys, dtype="string"), errors="coerce", dayfirst=True, format="mixed")
    if not dates.isna().any():
        return [keys[i] for i in dates.argsort(kind="stable")]
    return sorted(keys, key=_natural_key)


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
