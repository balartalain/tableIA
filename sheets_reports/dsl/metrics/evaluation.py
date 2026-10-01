"""Evaluación de una lista de métricas: a escalares (KPI) o por grupo de una columna (base de
los gráficos, de `having`/`limit` y de las métricas «por grupo»)."""
import pandas as pd

from sheets_reports.dsl.metrics.base import FlatContext, Metric, ScalarContext, evaluation_order


def scalar_values(df: pd.DataFrame, universe: pd.DataFrame, metrics: list[Metric]) -> dict:
    """{as: valor}, en el orden de la lista. Los porcentajes (show_as) son: valor con las
    condiciones del widget y de la métrica sobre el valor sin ellas (el universo)."""
    ev = ScalarContext(df=df, universe=universe)
    for m in evaluation_order(metrics):
        ev.values[m.alias] = m.scalar(ev)
    return {m.alias: ev.values[m.alias] for m in metrics}


def flat_table(df: pd.DataFrame, dimension: str, metrics: list[Metric]) -> pd.DataFrame:
    """
    Una fila por cada valor de `dimension` (en orden de aparición en la hoja) y una columna
    por métrica (en el orden de la lista), con `show_as` aplicado. Las claves quedan crudas
    (sin to_key) para poder compararlas con la columna original. Los totales de cada métrica
    van en attrs["totals"].
    """
    df = df[df[dimension].notna()]
    fc = FlatContext(df=df, dimension=dimension, keys=pd.Index(pd.unique(df[dimension]), name=dimension))
    for m in evaluation_order(metrics):
        fc.columns[m.alias], fc.totals[m.alias] = m.flat(fc)
    names = [m.alias for m in metrics]
    result = pd.DataFrame({name: fc.columns[name] for name in names}, index=fc.keys).reset_index()
    if result.empty:
        result = pd.DataFrame(columns=[dimension, *names])
    result.attrs["totals"] = {name: fc.totals[name] for name in names}
    return result
