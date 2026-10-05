"""
Paso 4 del motor: ventanas sobre las métricas, declaradas dentro del propio diccionario de la
métrica: `{"alias": "pct_total", "field": "monto", "agg": "sum",
"window": {"type": "percent_of_total"}}`.
"""
from __future__ import annotations

from typing import Any, Dict

import pandas as pd

from sheets_reports.engine.steps.aggregation import aggregate, metric_field


def apply_window_functions(df: pd.DataFrame, metrics: list | None, metadata: dict) -> pd.DataFrame:
    if metadata.get("nested") is not None:
        # En la tabla dinámica los porcentajes ya salen por celda (niveles y totales).
        return df
    scalar = metadata.get("scalar_result")
    if scalar is not None:
        _scalar_windows(scalar, metrics or [], metadata, df)
        return df

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
            planned[target] = (df[source].pct_change() * 100).round(2)

    if planned:
        # Se copia antes de escribir: el frame que llega de un groupby/filtro puede ser
        # una vista y la escritura no llegaría al resultado final.
        df = df.copy()
        for target, values in planned.items():
            df[target] = values
        metadata["window_applied"] = True
    return df


def _scalar_windows(scalar: dict, metrics: list, metadata: dict, df: pd.DataFrame) -> None:
    """Ventanas sobre un KPI (sin dimensiones): el denominador es el universo del widget,
    es decir, sus filas sin el recorte de los filtros propios de la métrica."""
    values = scalar.get("values") or {}
    universe = metadata.get("universe")
    if universe is None:
        universe = df
    for metric in metrics:
        if (metric.get("window") or {}).get("type") != "percent_of_total":
            continue
        alias, field_name = metric.get("alias") or metric.get("field"), metric_field(metric)
        if not field_name or alias not in values or field_name not in universe.columns:
            continue
        total = aggregate(universe[field_name], metric.get("agg"))
        values[alias] = round(values[alias] / total * 100, 2) if total else 0
        metadata["window_applied"] = True
