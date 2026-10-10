"""
Paso 2 del motor: agregación y/o pivote.

  - con `pivots`     → pivot_table (series por valor del pivote)
  - con `dimensions` → groupby + agg (resultado plano)
  - sin ninguno      → escalar (KPI)
  - tabla dinámica   → resultado jerárquico con subtotales y total general
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Tuple

import pandas as pd

from sheets_reports.engine.formulas import aggregated_fields
from sheets_reports.engine.steps.filter import parse_conditions
from sheets_reports.utils.data import (
    column_formats,
    is_number,
    require_id_column,
    to_python,
)

MAX_PIVOT_CELLS = 50_000

# Claves de celda de una tabla dinámica: separan los valores de varios pivotes ("2026" y
# "Ene"). Es un carácter de control: no aparece en los valores de una hoja.
KEY_SEPARATOR = "\x1f"

# Agregaciones aceptadas en `fields.metrics[].agg` → nombre de Pandas.
AGGREGATIONS: Dict[str, str] = {
    "sum": "sum",
    "avg": "mean",
    "mean": "mean",
    "median": "median",
    "min": "min",
    "max": "max",
    "std": "std",
    "count": "count",
    "count_distinct": "nunique",
    # Campo calculado agregado de la fuente: la agregación viene en su fórmula.
    "auto": "auto",
}


class ResultTooLargeError(ValueError):
    """El resultado de la consulta supera el máximo de celdas permitido."""

    def __init__(self, message: str = "El resultado es demasiado grande para mostrarlo."):
        super().__init__(message)


def cell_field(col_key: tuple, metric: str) -> str:
    """Clave plana de la celda (fila, columna) de una tabla dinámica. El frontend desactiva
    el separador de campos anidados de Tabulator, así que los puntos no rompen nada."""
    return "__pivots." + KEY_SEPARATOR.join(str(v) for v in col_key) + "." + metric


def total_field(metric: str) -> str:
    """Clave de la columna «Total general» de una métrica en una tabla con pivotes."""
    return f"__total.{metric}"


def agg_name(agg: str) -> str:
    """Traduce una agregación del form al nombre que entiende Pandas."""
    return AGGREGATIONS.get(agg, agg)


def _calculated_aggregator(df: pd.DataFrame, field) -> Callable[[pd.Series], Any]:
    """Agregador de un campo calculado agregado: recibe la columna intermedia de un grupo (sus
    filas, NaN las que excluyen las condiciones de la métrica) y evalúa la fórmula sobre esas
    filas de `df`. Sirve igual para groupby, pivot_table y el total general."""
    def aggregate_group(series: pd.Series):
        return field.formula.aggregate(df.loc[series.dropna().index])
    aggregate_group.__name__ = "calculated"
    return aggregate_group


def aggregate(series: pd.Series, agg: str):
    if agg == "count":
        return series.count()
    return series.agg(agg_name(agg))


def metric_alias(metric: dict) -> str:
    """Alias de una métrica (la columna del resultado con su valor)."""
    return metric.get("alias") or ""


# Formatos de una métrica; `progress` (barra 0-100) solo se elige en la métrica.
METRIC_FORMATS = ("number", "currency", "percent", "progress")
# Agregaciones que conservan la unidad de la columna (una suma de montos es un monto); el
# conteo no: cuenta filas.
_UNIT_AGGS = {"sum", "avg", "mean", "median", "min", "max", "std"}


def metric_formats(df: pd.DataFrame, metrics: list) -> Dict[str, str]:
    """{alias: formato} de las métricas que tienen uno: el elegido en la métrica; si no, el del
    campo calculado agregado o el de la columna en la fuente (si la agregación conserva su
    unidad). Las que no figuran se muestran como número."""
    calculated = aggregated_fields(df)
    columns = column_formats(df)
    out: Dict[str, str] = {}
    for metric in metrics or []:
        alias, field = metric_alias(metric), metric_field(metric)
        chosen = (metric or {}).get("format")
        if chosen in METRIC_FORMATS:
            fmt = chosen
        elif field in calculated:
            fmt = calculated[field].format
        elif (metric or {}).get("agg") in _UNIT_AGGS:
            fmt = columns.get(field)
        else:
            fmt = None
        if alias and fmt and fmt != "number":
            out[alias] = fmt
    return out


def metric_field(metric: dict) -> str:
    """Columna de la hoja que resume la métrica."""
    return metric.get("field", "")


def metric_mask(df: pd.DataFrame, metric: dict) -> pd.Series:
    """Máscara booleana de las filas que cumplen las condiciones propias de la métrica."""
    mask = pd.Series(True, index=df.index)
    for condition in parse_conditions(metric.get("filters") or []):
        mask &= condition.mask(df)
    return mask.fillna(False)


def _metric_rows(df: pd.DataFrame, metric: dict) -> pd.DataFrame:
    """Las filas de `df` que entran a la métrica (sus condiciones propias, si las trae)."""
    if not metric.get("filters"):
        return df
    return df[metric_mask(df, metric)]


def _metric_columns(df: pd.DataFrame, metrics: list) -> Tuple[pd.DataFrame, List[Tuple[str, str, str]]]:
    """
    Una columna intermedia por métrica: dos métricas sobre el mismo campo (suma y promedio)
    no colisionan, y el «Conteo» (sin campo o sobre uno) se resuelve como
    filas distintas (ID): la intermedia es la columna ID con `nunique`,
    dejando NaN las filas que quedan fuera (su columna vacía o sus
    condiciones propias). Devuelve
    (frame, [(columna, agregación pandas, alias), ...]).

    Las condiciones propias de la métrica enmascaran sus filas como NaN: todas
    las agregaciones aceptadas (sum, avg, count, nunique...) ignoran los NaN, así que cada
    métrica resume solo sus filas dentro del mismo groupby/pivote.
    """
    df = df.copy()
    calculated = aggregated_fields(df)
    specs = []
    for i, metric in enumerate(metrics):
        field_name, agg, alias = metric_field(metric), metric.get("agg"), metric_alias(metric)
        column = f"__metric_{i}"
        func = agg_name(agg)
        if field_name in calculated:
            # Campo agregado: la columna solo marca las filas; su agregador evalúa la fórmula.
            df[column] = 1.0
            func = _calculated_aggregator(df, calculated[field_name])
        elif field_name and agg == "count":
            # Conteo sobre una columna: filas distintas (ID) con la
            # columna no vacía (las vacías quedan como NaN).
            id_column = require_id_column(df)
            df[column] = df[id_column]
            df.loc[df[field_name].isna(), column] = float("nan")
            func = "nunique"
        elif field_name:
            df[column] = df[field_name]
        elif agg == "count":
            # Conteo sin columna: filas distintas (ID).
            id_column = require_id_column(df)
            df[column] = df[id_column]
            func = "nunique"
        else:
            continue
        if metric.get("filters"):
            df.loc[~metric_mask(df, metric), column] = float("nan")
        specs.append((column, func, alias))
    return df, specs


# ------------------------------------------------------- claves jerárquicas
def _ordered_keys(df: pd.DataFrame, columns: list) -> List[tuple]:
    """Combinaciones de `columns` presentes en el frame, en orden de aparición y sin nulos."""
    if not columns:
        return []
    rows = df[list(columns)].dropna().itertuples(index=False, name=None)
    return list(dict.fromkeys(tuple(to_python(v) for v in row) for row in rows))


def hierarchy(leaf_keys: List[tuple], sort_key=None) -> List[tuple]:
    """
    Recorre las claves hoja como un árbol y devuelve todas las claves en orden de despliegue:
    los hijos de cada grupo y, al terminar el grupo, su clave (el subtotal), como las tablas
    dinámicas de Sheets. `sort_key(level, siblings)` reordena los hermanos de ese nivel.
    """
    depth = len(leaf_keys[0]) if leaf_keys else 0
    if not depth:
        return []
    children: Dict[tuple, List[tuple]] = {}
    for key in leaf_keys:
        for level in range(depth):
            children.setdefault(key[:level], []).append(key[:level + 1])

    ordered: List[tuple] = []

    def walk(prefix: tuple) -> None:
        siblings = list(dict.fromkeys(children.get(prefix, [])))
        if sort_key:
            siblings = sort_key(len(prefix), siblings)
        for child in siblings:
            if len(child) == depth:
                ordered.append(child)
            else:
                walk(child)
                ordered.append(child)

    walk(())
    return ordered


def _grouped_values(df: pd.DataFrame, specs: list, by: list) -> Dict[tuple, dict]:
    """{clave (tupla) → {alias: valor}} agregando `df` por `by`; sin `by`, el total general."""
    if not by:
        return {(): {alias: to_python(df[col].agg(func)) for col, func, alias in specs}}
    cols = [col for col, _, _ in specs]
    funcs = {col: func for col, func, _ in specs}
    grouped = df.groupby(list(by), sort=False, dropna=True)[cols].agg(funcs)
    out: Dict[tuple, dict] = {}
    for key, row in grouped.iterrows():
        out[tuple(key) if isinstance(key, tuple) else (key,)] = {
            alias: to_python(row[col]) for col, _, alias in specs
        }
    return out


def _wide_frame(dimensions, pivots, row_keys, col_keys, cells) -> pd.DataFrame:
    """Marco ancho de las celdas hoja: una fila por clave de fila completa y una columna por
    celda (o la métrica directa, si no hay pivotes). Es el `data` de respaldo del resultado."""
    records = []
    for key in row_keys:
        if dimensions and len(key) != len(dimensions):
            continue  # solo las filas hoja: los subtotales viven en `nested`
        record = {dim: value for dim, value in zip(dimensions, key)}
        if pivots:
            for col in col_keys:
                if len(col) != len(pivots):
                    continue
                for alias, value in cells[(key, col)].items():
                    record[cell_field(col, alias)] = value
        else:
            record.update(cells[(key, ())])
        records.append(record)
    return pd.DataFrame(records)


# ------------------------------------------------------------------ paso
def apply_aggregation(
    df: pd.DataFrame,
    fields,
    metadata: dict,
    *,
    widget_type: str | None = None,
) -> pd.DataFrame:
    """Paso 2: agrega o pivotea `df` según `fields` y deja en `metadata` la forma del
    resultado (`scalar_result`, `flat_result`, `pivot_chart_result` o `nested`)."""
    metrics = fields.metrics or []
    if not metrics:
        return df
    # Cómo mostrar cada métrica (moneda, %…): el frontend formatea sus valores con él.
    metadata["metric_formats"] = metric_formats(df, metrics)
    metadata["percent_metrics"] = [alias for alias, fmt in metadata["metric_formats"].items()
                                   if fmt == "percent"]

    dimensions = fields.dimensions or []
    pivots = fields.pivots or []

    if not dimensions and not pivots:
        return _scalar(df, metrics, metadata)
    if widget_type == "dynamic_table":
        # La tabla dinámica necesita subtotales por nivel y total general: el frame
        # plano no los trae, así que arma el resultado jerárquico.
        return _nested(df, dimensions, pivots, metrics, metadata)
    if pivots:
        return _pivot(df, dimensions, pivots, metrics, metadata)
    return _grouped(df, dimensions, pivots, metrics, metadata)


# -- escalar (KPI) ---------------------------------------------------------
def _scalar(df, metrics, metadata) -> pd.DataFrame:
    values = {}
    calculated = aggregated_fields(df)
    for m in metrics:
        rows = _metric_rows(df, m)
        field_name, agg, alias = metric_field(m), m.get("agg"), metric_alias(m)
        if field_name in calculated:
            values[alias] = calculated[field_name].formula.aggregate(rows)
        elif field_name and agg == "count":
            # Conteo sobre una columna: las filas distintas (ID) con
            # esa columna no vacía.
            id_column = require_id_column(rows)
            values[alias] = rows.loc[rows[field_name].notna(), id_column].nunique()
        elif field_name:
            values[alias] = aggregate(rows[field_name], agg)
        elif agg == "count":
            # Conteo sin columna: filas distintas (ID).
            values[alias] = rows[require_id_column(rows)].nunique()
        else:
            values[alias] = None
    metadata["scalar_result"] = {"values": values}
    metadata["aggregated"] = True
    return df


# -- groupby ---------------------------------------------------------------
def _grouped(df, dimensions, pivots, metrics, metadata) -> pd.DataFrame:
    df, specs = _metric_columns(df, metrics)
    if not specs:
        return df

    original = {dim: df[dim].dropna().unique().tolist() for dim in dimensions}
    df_agg = df.groupby(dimensions, sort=False).agg({col: agg for col, agg, _ in specs}).reset_index()

    lead = dimensions[0]
    order = original.get(lead, [])
    if order and lead in df_agg.columns:
        values = df_agg[lead]
        df_agg[lead] = pd.Categorical(values, categories=order, ordered=True)
        df_agg = df_agg.sort_values(lead).reset_index(drop=True)
        df_agg[lead] = df_agg[lead].astype(values.dtype)

    df_agg = df_agg.rename(columns={col: alias for col, _, alias in specs if alias and alias != col})

    metadata["aggregated"] = True
    metadata["dimensions"] = dimensions
    metadata["pivots"] = pivots
    metadata["dimension_values"] = df_agg[lead].tolist()
    metadata["flat_result"] = {
        "dimension": lead,
        "totals": {},
        "dimension_values": metadata["dimension_values"],
    }
    return df_agg


# -- tabla dinámica (filas y columnas con subtotales) ----------------------
def _nested(df, dimensions, pivots, metrics, metadata) -> pd.DataFrame:
    """
    Resultado jerárquico de la tabla dinámica: una celda por (prefijo de filas,
    prefijo de columnas), con el subtotal de cada nivel y el total general, como en
    las tablas dinámicas de una hoja de cálculo.

    - `metadata["nested"]` lleva claves, celdas y filas para `dynamic_table.compile`;
    - el frame devuelto es el marco ancho de las celdas hoja (el `data` de respaldo).
    """
    df, specs = _metric_columns(df, metrics)
    if not specs:
        return df

    row_leaf = _ordered_keys(df, dimensions)
    col_leaf = _ordered_keys(df, pivots)
    row_keys = hierarchy(row_leaf)
    col_keys = hierarchy(col_leaf)
    all_rows, all_cols = [(), *row_keys], [(), *col_keys]

    # Celdas (con subtotales y totales) × métricas: un cruce mayor no se puede leer.
    cells = max(len(all_rows), 1) * max(len(all_cols), 1) * len(specs)
    if cells > MAX_PIVOT_CELLS:
        raise ResultTooLargeError(
            f"La tabla {len(all_rows)} × {len(all_cols)} × {len(specs)} métricas genera "
            f"{cells:,} celdas; acota las dimensiones o filtra la hoja."
        )

    # Un groupby por pareja (nivel de fila, nivel de columna): todos los valores de esa
    # pareja salen de una sola agregación, clave a clave.
    tables = {
        (k, j): _grouped_values(df, specs, list(dimensions[:k]) + list(pivots[:j]))
        for k in range(len(dimensions) + 1)
        for j in range(len(pivots) + 1)
    }
    agg_cells: Dict[tuple, dict] = {}
    for row_key in all_rows:
        for col_key in all_cols:
            values = tables[(len(row_key), len(col_key))].get(row_key + col_key)
            agg_cells[(row_key, col_key)] = (
                values if values is not None
                else {alias: None for _, _, alias in specs}
            )

    aliases = [a for a in (metric_alias(m) for m in metrics) if a]
    windows = [m for m in metrics if (m.get("window") or {}).get("type")]
    if windows:
        _cell_windows(agg_cells, windows)

    rows = [
        {
            "key": list(key),
            "cells": {col: agg_cells[(key, col)] for col in col_keys},
            "totals": agg_cells[(key, ())],
            "subtotal": len(key) < len(dimensions),
        }
        for key in row_keys
    ]
    metadata["nested"] = {
        "dimensions": list(dimensions),
        "pivots": list(pivots),
        "metrics": aliases,
        "row_leaf": row_leaf,
        "column_keys": col_keys,
        "cells": agg_cells,
        "rows": rows,
        "grand": {"cells": {col: agg_cells[((), col)] for col in col_keys},
                  "totals": agg_cells[((), ())]},
    }
    metadata["aggregated"] = True
    metadata["dimensions"] = dimensions
    metadata["pivots"] = pivots
    metadata["metrics"] = metrics
    metadata["dimension_values"] = [list(k) for k in row_leaf]
    return _wide_frame(dimensions, pivots, row_keys, col_keys, agg_cells)


def _cell_windows(cells: Dict[tuple, dict], windows: list) -> None:
    """Ventanas sobre TODAS las celdas (hoja, subtotales y total general): un porcentaje se
    recalcula en cada nivel, no se suma."""
    keys = list(cells)
    frame = pd.DataFrame([cells[key] for key in keys])
    columns = list(frame.columns)
    for metric in windows:
        _cell_window(frame, metric, keys, columns)
    for position, key in enumerate(keys):
        cells[key] = {alias: to_python(frame.at[position, alias]) for alias in columns}


def _cell_window(frame: pd.DataFrame, metric: dict, keys: list, columns: list) -> None:
    """Porcentajes sobre las celdas: el denominador es el total de su columna
    (`percent_of_total`) o el de su fila (`percent_of_row`). El resto de ventanas no
    tiene sentido fila a fila en una tabla y se ignoran."""
    alias = metric.get("alias") or metric.get("field")
    w_type = (metric.get("window") or {}).get("type")
    if alias not in columns or w_type not in ("percent_of_total", "percent_of_row"):
        return
    values = {key: frame.at[position, alias] for position, key in enumerate(keys)}
    # Una suma de enteros deja la columna en int64: el porcentaje (66.23) no cabe en ella.
    frame[alias] = frame[alias].astype(float)

    def _percent(value, total) -> float:
        if not is_number(value) or value != value:
            return 0
        if not is_number(total) or not total:
            return 0
        return round(float(value) / float(total) * 100, 2)

    # Cada celda sobre el total de su columna, o el de su fila.
    for position, key in enumerate(keys):
        row_key, col_key = key
        total = (values[((), col_key)] if w_type == "percent_of_total"
                 else values[(row_key, ())])
        frame.at[position, alias] = _percent(frame.at[position, alias], total)


# -- pivote ----------------------------------------------------------------
def _pivot(df, dimensions, pivots, metrics, metadata) -> pd.DataFrame:
    pivot_column = pivots[0]
    df, specs = _metric_columns(df, metrics)
    if not specs:
        return df

    order = {dim: df[dim].dropna().unique().tolist() for dim in dimensions}
    pivot_values = df[pivot_column].dropna().unique().tolist()

    # pivot_table multiplica filas × valores del pivote × métricas: un fan-out inesperado
    # se corta aquí en vez de mandar un JSON gigante al navegador.
    cells = max(1, len(dimensions or ["_"])) * max(1, len(pivot_values)) * len(specs)
    if cells > MAX_PIVOT_CELLS:
        raise ResultTooLargeError(
            f"El cruce {pivot_column} × {'/'.join(dimensions or ['filas'])} genera "
            f"{cells:,} celdas; acota las dimensiones o filtra la hoja."
        )

    table = df.pivot_table(
        index=dimensions or None,
        columns=pivot_column,
        values=[col for col, _, _ in specs],
        aggfunc={col: agg for col, agg, _ in specs},
        fill_value=0,
        sort=False,
    )
    if isinstance(table.columns, pd.MultiIndex):
        table.columns = [
            f"{value}_{col}" if value else str(col)
            for col, value in table.columns
        ]
    table = table.reset_index()

    # {valor}_{columna intermedia} → {valor}_{alias}
    for col, _, alias in specs:
        if not alias or alias == col:
            continue
        table.columns = [
            c[: -len(col)] + alias if c.endswith(f"_{col}") else c
            for c in table.columns
        ]

    metadata["pivoted"] = True
    metadata["pivot_column"] = pivot_column
    metadata["pivot_values"] = pivot_values
    metadata["dimensions"] = dimensions
    metadata["pivots"] = pivots
    metadata["metrics"] = metrics
    metadata["dimension_values"] = order.get(dimensions[0], []) if dimensions else []

    if dimensions:
        dim = dimensions[0]
        metadata["pivot_chart_result"] = {
            "dimension": dim,
            "pivot_values": pivot_values,
            "metric": metric_alias(metrics[0]),
            "dimension_values": order.get(dim, []),
        }
    return table
