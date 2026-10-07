"""
Paso 3 del motor: ventanas sobre las métricas, declaradas dentro del propio diccionario de la
métrica: `{"alias": "pct_total", "field": "monto", "agg": "sum",
"window": {"type": "percent_of_total"}}`.
"""
from __future__ import annotations

from typing import Any, Dict

import pandas as pd

from sheets_reports.utils.data import time_order, to_key

# Ventanas que dependen del orden de las filas: se calculan de lo más antiguo a lo más reciente.
ORDERED_WINDOWS = ("running_total", "pct_change")


def apply_window_functions(df: pd.DataFrame, metrics: list | None, metadata: dict) -> pd.DataFrame:
    if metadata.get("nested") is not None:
        # En la tabla dinámica los porcentajes ya salen por celda (niveles y totales).
        return df
    if metadata.get("scalar_result") is not None:
        # Un solo número (KPI): no hay otras filas que mirar. La participación se hace con
        # un campo calculado agregado (ej. SUM(IF(cat = "Hogar", ventas, 0)) / SUM(ventas) * 100).
        return df

    if metadata.get("pivoted"):
        return _pivot_windows(df, metrics, metadata)

    if any((m.get("window") or {}).get("type") in ORDERED_WINDOWS for m in metrics or []):
        df = _chronological_rows(df, metadata)

    planned: Dict[str, Any] = {}
    for metric in metrics or []:
        w_type = (metric.get("window") or {}).get("type")
        if not w_type:
            continue
        # La ventana trabaja sobre la columna ya agregada (el alias); si no existe, sobre el
        # campo crudo de la hoja.
        target = metric.get("alias") or metric.get("field")
        source = target if target in df.columns else metric.get("field")
        if not source or source not in df.columns:
            continue
        if w_type in ("percent_of_total", "percent_of_row"):
            total = df[source].sum()
            planned[target] = (df[source] / total * 100).round(2) if total else 0
        elif w_type == "running_total":
            planned[target] = df[source].cumsum()
        elif w_type == "pct_change":
            planned[target] = (df[source].pct_change(fill_method=None) * 100).round(2)

    if planned:
        # Se copia antes de escribir: el frame que llega de un groupby/filtro puede ser
        # una vista y la escritura no llegaría al resultado final.
        df = df.copy()
        for target, values in planned.items():
            df[target] = values
        metadata["window_applied"] = True
    return df


def _pivot_windows(df: pd.DataFrame, metrics: list | None, metadata: dict) -> pd.DataFrame:
    """Con pivote (gráficos), cada métrica es una columna `{valor}_{alias}` por valor del
    pivote: los porcentajes se calculan por celda, como en la tabla dinámica. `percent_of_row`
    divide entre el total de su fila (cada barra apilada suma 100 %); `percent_of_total`, entre
    el de su columna (cada valor del pivote suma 100 %). El resto de ventanas no aplica con
    pivote."""
    planned: Dict[str, Any] = {}
    for metric in metrics or []:
        w_type = (metric.get("window") or {}).get("type")
        if w_type not in ("percent_of_total", "percent_of_row"):
            continue
        alias = metric.get("alias") or metric.get("field")
        columns = [f"{value}_{alias}" for value in metadata.get("pivot_values") or []]
        columns = [col for col in columns if col in df.columns]
        if not columns:
            continue
        cells = df[columns].astype(float)
        axis = 1 if w_type == "percent_of_row" else 0
        totals = cells.sum(axis=axis).replace(0, float("nan"))
        shares = (cells.div(totals, axis=1 - axis) * 100).round(2).fillna(0)
        planned.update({col: shares[col] for col in columns})

    if planned:
        df = df.copy()
        for col, values in planned.items():
            df[col] = values
        metadata["window_applied"] = True
    return df


def _chronological_rows(df: pd.DataFrame, metadata: dict) -> pd.DataFrame:
    """Si la primera dimensión es de tiempo (meses, fechas, años), las filas de lo más antiguo
    a lo más reciente, como el eje del gráfico de líneas: el acumulado y la variación no
    dependen del orden de la hoja. Otra dimensión (categorías) queda en su orden."""
    dimensions = metadata.get("dimensions") or []
    column = dimensions[0] if dimensions else None
    if not column or column not in df.columns:
        return df
    keys = time_order(list({to_key(v) for v in df[column].dropna()}))
    if keys is None:
        return df
    position = {key: i for i, key in enumerate(keys)}
    order = df[column].map(lambda v: position.get(to_key(v), len(position)))
    return df.iloc[order.argsort(kind="stable")].reset_index(drop=True)
