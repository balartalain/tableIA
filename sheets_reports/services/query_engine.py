"""
Motor de ejecución determinista del DSL: el ÚNICO lugar donde se hacen sumas, promedios y
conteos reales. La IA no participa aquí bajo ninguna circunstancia; `spec` ya viene validado
(services/spec_validation.py). Solo usa operaciones vectorizadas de pandas: nada de
DataFrame.query()/eval() ni ninguna otra forma de evaluar texto.
"""
import math
import operator

import numpy as np
import pandas as pd

_COMPARATORS = {
    "eq": operator.eq,
    "ne": operator.ne,
    "lt": operator.lt,
    "lte": operator.le,
    "gt": operator.gt,
    "gte": operator.ge,
}


def to_python(value):
    """Convierte escalares de numpy/pandas a tipos nativos serializables a JSON (NaN -> None)."""
    if value is None:
        return None
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if value is pd.NaT:
        return None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value


def _coerce_value(series: pd.Series, value):
    """Adapta el valor del filtro al tipo de la columna (ej. "2026" que llega por URL contra
    una columna numérica, o 2026 contra una columna de texto)."""
    if pd.api.types.is_numeric_dtype(series):
        if isinstance(value, str):
            try:
                return float(value.replace(",", ""))
            except ValueError:
                return value
        return value
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (int, float)):
        return str(value)
    return value


def _column_for_comparison(series: pd.Series) -> pd.Series:
    if pd.api.types.is_numeric_dtype(series):
        return series
    return series.astype("string").str.strip()


def apply_filters(df: pd.DataFrame, filters: list[dict]) -> pd.DataFrame:
    mask = pd.Series(True, index=df.index)
    for f in filters or []:
        column = _column_for_comparison(df[f["field"]])
        if f["op"] == "in":
            values = [_coerce_value(df[f["field"]], v) for v in f["value"]]
            cond = column.isin(values)
        else:
            cond = _COMPARATORS[f["op"]](column, _coerce_value(df[f["field"]], f["value"]))
        mask &= cond.fillna(False).astype(bool)
    return df[mask]


def _aggregate(grouped, metric: dict) -> pd.Series:
    if metric["agg"] == "count":
        # count cuenta filas del grupo; no depende semánticamente de `field`.
        return grouped.size()
    column = grouped[metric["field"]]
    return column.sum() if metric["agg"] == "sum" else column.mean()


def _total(df: pd.DataFrame, metric: dict):
    if metric["agg"] == "count":
        return len(df)
    column = df[metric["field"]]
    return column.sum() if metric["agg"] == "sum" else column.mean()


def run_data_spec(df: pd.DataFrame, spec: dict) -> pd.DataFrame | dict:
    """
    Ejecuta la agregación descrita en `spec` sobre `df`.

    - Sin dimensión (kpi): dict {as: valor}.
    - Sin pivote: DataFrame plano con la columna de la dimensión + una columna por cada
      `metrics[].as`, en el orden de aparición en la hoja salvo que `sort` diga otra cosa.
    - Con pivote: dict anidado
        {"dimension", "pivot", "metric",
         "dimension_values": [...], "pivot_values": [...],
         "rows": {valor_dim: {valor_pivote: valor_métrica}}}
      que apex_compiler consume tanto para tabla anidada como para series de gráfico.
    """
    df = apply_filters(df, spec.get("filters") or [])
    metrics = spec["metrics"]
    dimensions = spec.get("dimensions") or []
    pivot = spec.get("pivot")
    sort = spec.get("sort")

    if not dimensions:
        return {m["as"]: to_python(_total(df, m)) for m in metrics}

    dimension = dimensions[0]
    df = df[df[dimension].notna()]

    if not pivot:
        grouped = df.groupby(dimension, sort=False)
        result = pd.DataFrame({m["as"]: _aggregate(grouped, m) for m in metrics})
        result = result.reset_index()
        if result.empty:
            result = pd.DataFrame(columns=[dimension, *[m["as"] for m in metrics]])
        if sort:
            result = result.sort_values(
                sort["by"], ascending=sort["dir"] == "asc", na_position="last", kind="stable",
            ).reset_index(drop=True)
        return result

    metric = metrics[0]
    df = df[df[pivot].notna()]
    dimension_values = [to_python(v) for v in df[dimension].unique()]
    pivot_values = [to_python(v) for v in df[pivot].unique()]
    values = _aggregate(df.groupby([dimension, pivot], sort=False), metric)
    # Una combinación sin filas suma/cuenta 0; su promedio no existe.
    empty = None if metric["agg"] == "avg" else 0

    rows = {d: {p: empty for p in pivot_values} for d in dimension_values}
    for (d, p), v in values.items():
        rows[to_python(d)][to_python(p)] = to_python(v)

    if sort:
        reverse = sort["dir"] == "desc"
        if sort["by"] == dimension:
            dimension_values.sort(key=_sort_key, reverse=reverse)
        else:
            totals = {d: sum(v for v in rows[d].values() if v is not None) for d in dimension_values}
            dimension_values.sort(key=lambda d: totals[d], reverse=reverse)

    return {
        "dimension": dimension,
        "pivot": pivot,
        "metric": metric["as"],
        "dimension_values": dimension_values,
        "pivot_values": pivot_values,
        "rows": rows,
    }


def _sort_key(value):
    # Números antes que textos, para no comparar tipos distintos.
    return (0, value, "") if isinstance(value, (int, float)) else (1, 0, str(value))
