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

from sheets_reports.services.spec_validation import normalize_metric, pivots_of

# Tope de celdas de una tabla dinámica (filas × columnas × métricas): un cruce mayor no se
# puede leer y congelaría el navegador.
MAX_TABLE_CELLS = 20000


class ResultTooLargeError(ValueError):
    """El cruce pedido genera más celdas de las que se pueden mostrar."""

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


def to_key(value):
    """Valor de una columna de agrupación (dimensión o pivote): como to_python, pero un entero
    que pandas leyó como float (columna con celdas vacías) vuelve a ser entero: 2026, no
    "2026.0" en etiquetas y cabeceras."""
    value = to_python(value)
    if isinstance(value, float) and value.is_integer():
        return int(value)
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


# Función de pandas para cada `agg` que resume una columna (count cuenta filas aparte).
_AGG_FUNCS = {
    "sum": "sum",
    "avg": "mean",
    "min": "min",
    "max": "max",
    "median": "median",
    "count_distinct": "nunique",
}
# Una combinación sin filas suma/cuenta 0; su promedio, mínimo, etc. no existe.
_ZERO_WHEN_EMPTY = {"count", "sum", "count_distinct"}


def _aggregate(grouped, metric: dict) -> pd.Series:
    """Valor crudo por grupo, antes de aplicar `show_as`."""
    if metric["agg"] == "count":
        # count cuenta filas del grupo; no depende semánticamente de `field`.
        return grouped.size()
    return getattr(grouped[metric["field"]], _AGG_FUNCS[metric["agg"]])()


def _total(df: pd.DataFrame, metric: dict):
    if metric["agg"] == "count":
        return len(df)
    return getattr(df[metric["field"]], _AGG_FUNCS[metric["agg"]])()


def _percent(part, whole):
    """part / whole * 100 redondeado a 2 decimales; None si no hay total contra el que dividir."""
    part, whole = to_python(part), to_python(whole)
    if part is None or not whole:
        return None
    return round(part / whole * 100, 2)


def _series_dict(series: pd.Series) -> dict:
    """{clave de grupo: valor}; claves compuestas (varias columnas) como tuplas."""
    return {
        (tuple(to_key(p) for p in k) if isinstance(k, tuple) else to_key(k)): to_python(v)
        for k, v in series.items()
    }


def run_data_spec(df: pd.DataFrame, spec: dict, layout: str = "auto") -> pd.DataFrame | dict:
    """
    Ejecuta la agregación descrita en `spec` sobre `df`. Con layout="table" (widgets tabla)
    devuelve la tabla dinámica jerárquica de _run_pivot_table; si no, los formatos de abajo.

    `show_as` de cada métrica (como "Mostrar como" en las tablas dinámicas de Sheets):
    - value: el valor resumido.
    - pct_row: % del total de su fila de la dimensión (solo cambia algo con pivote).
    - pct_column: % del total de su columna (sin pivote: del total de todos los grupos).
    - pct_total: % del total general.
    En un kpi cualquier porcentaje es: valor con los filtros del widget sobre el valor sin ellos.
    Los totales se calculan desde los datos (no sumando celdas), así el total de un promedio
    es el promedio real.

    - Sin dimensión (kpi): dict {as: valor}.
    - Sin pivote: DataFrame plano con la columna de la dimensión + una columna por cada
      `metrics[].as`, en el orden de aparición en la hoja salvo que `sort` diga otra cosa.
      El total general de cada métrica va en `result.attrs["totals"]`.
    - Con pivote: dict anidado
        {"dimension", "pivot", "metric" (la primera), "metrics",
         "dimension_values": [...], "pivot_values": [...],
         "rows": {valor_dim: {valor_pivote: {as: valor}}},
         "row_totals": {valor_dim: {as: valor}}, "column_totals": {valor_pivote: {as: valor}},
         "grand_totals": {as: valor}}
      que apex_compiler consume tanto para tabla anidada como para series de gráfico.
    """
    # Universo de los porcentajes del KPI: la hoja antes de los filtros propios del widget.
    universe = df
    df = apply_filters(df, spec.get("filters") or [])
    metrics = [normalize_metric(m, spec) for m in spec["metrics"]]
    dimensions = spec.get("dimensions") or []
    pivots = pivots_of(spec)
    pivot = pivots[0] if pivots else None
    sort = spec.get("sort")

    if not dimensions:
        return {
            m["as"]: _percent(_total(df, m), _total(universe, m)) if m["show_as"] != "value"
            else to_python(_total(df, m))
            for m in metrics
        }

    if layout == "table":
        return _run_pivot_table(df, dimensions, pivots, metrics, sort)

    dimension = dimensions[0]
    df = df[df[dimension].notna()]

    if not pivot:
        grouped = df.groupby(dimension, sort=False)
        result = pd.DataFrame({m["as"]: _aggregate(grouped, m) for m in metrics})
        totals = {}
        for m in metrics:
            grand = _total(df, m)
            if m["show_as"] == "value":
                totals[m["as"]] = to_python(grand)
            elif m["show_as"] == "pct_row":
                # Sin columnas de pivote, cada fila es el 100% de sí misma (como en Sheets).
                result[m["as"]] = [None if pd.isna(v) else 100.0 for v in result[m["as"]]]
                totals[m["as"]] = 100.0 if to_python(grand) is not None else None
            else:
                result[m["as"]] = [_percent(v, grand) for v in result[m["as"]]]
                totals[m["as"]] = _percent(grand, grand)
        result = result.reset_index()
        if result.empty:
            result = pd.DataFrame(columns=[dimension, *[m["as"] for m in metrics]])
        if sort:
            result = result.sort_values(
                sort["by"], ascending=sort["dir"] == "asc", na_position="last", kind="stable",
            ).reset_index(drop=True)
        result[dimension] = [to_key(v) for v in result[dimension]]
        result.attrs["totals"] = totals
        return result

    df = df[df[pivot].notna()]
    dimension_values = [to_key(v) for v in df[dimension].unique()]
    pivot_values = [to_key(v) for v in df[pivot].unique()]

    rows = {d: {p: {} for p in pivot_values} for d in dimension_values}
    row_totals = {d: {} for d in dimension_values}
    column_totals = {p: {} for p in pivot_values}
    grand_totals = {}
    for m in metrics:
        name, show_as = m["as"], m["show_as"]
        cells = _series_dict(_aggregate(df.groupby([dimension, pivot], sort=False), m))
        by_row = _series_dict(_aggregate(df.groupby(dimension, sort=False), m))
        by_column = _series_dict(_aggregate(df.groupby(pivot, sort=False), m))
        grand = to_python(_total(df, m))
        empty = 0 if m["agg"] in _ZERO_WHEN_EMPTY else None

        def shown(value, d=None, p=None):
            if show_as == "value":
                return value
            if show_as == "pct_row":
                whole = by_row[d] if d is not None else grand
            elif show_as == "pct_column":
                whole = by_column[p] if p is not None else grand
            else:
                whole = grand
            return _percent(value, whole)

        for d in dimension_values:
            for p in pivot_values:
                rows[d][p][name] = shown(cells.get((d, p), empty), d, p)
            row_totals[d][name] = shown(by_row[d], d=d)
        for p in pivot_values:
            column_totals[p][name] = shown(by_column[p], p=p)
        grand_totals[name] = shown(grand)

    if sort:
        reverse = sort["dir"] == "desc"
        if sort["by"] == dimension:
            dimension_values.sort(key=_sort_key, reverse=reverse)
        else:
            # Ordena por el total de la fila en esa métrica; los vacíos siempre al final.
            present = [d for d in dimension_values if row_totals[d][sort["by"]] is not None]
            missing = [d for d in dimension_values if row_totals[d][sort["by"]] is None]
            present.sort(key=lambda d: row_totals[d][sort["by"]], reverse=reverse)
            dimension_values = present + missing

    return {
        "dimension": dimension,
        "pivot": pivot,
        "metric": metrics[0]["as"],
        "metrics": [m["as"] for m in metrics],
        "dimension_values": dimension_values,
        "pivot_values": pivot_values,
        "rows": rows,
        "row_totals": row_totals,
        "column_totals": column_totals,
        "grand_totals": grand_totals,
    }


def _group_values(df: pd.DataFrame, columns: list[str], metric: dict) -> dict:
    """{tupla de valores de `columns`: valor crudo de la métrica}; () es el total general."""
    if not columns:
        return {(): to_python(_total(df, metric))}
    values = _aggregate(df.groupby(columns, sort=False), metric)
    return {
        tuple(to_key(k) for k in (key if isinstance(key, tuple) else (key,))): to_python(v)
        for key, v in values.items()
    }


def _ordered_keys(df: pd.DataFrame, columns: list[str]) -> list[tuple]:
    """Combinaciones de `columns` presentes en `df`, en orden de aparición en la hoja."""
    if not columns:
        return []
    rows = df[columns].itertuples(index=False, name=None)
    return list(dict.fromkeys(tuple(to_key(v) for v in row) for row in rows))


def _hierarchy(leaf_keys: list[tuple], sort_level=None) -> list[tuple]:
    """
    Recorre las claves hoja como un árbol y devuelve todas las claves en orden de despliegue:
    los hijos de cada grupo y, al terminar el grupo, su subtotal (la clave del grupo, más
    corta), como las tablas dinámicas de Sheets. `sort_level(level, siblings)` ordena los
    hermanos de cada nivel.
    """
    depth = len(leaf_keys[0]) if leaf_keys else 0
    children: dict[tuple, dict] = {}
    for key in leaf_keys:
        for level in range(depth):
            children.setdefault(key[:level], {})[key[:level + 1]] = None

    ordered = []

    def walk(prefix):
        siblings = list(children.get(prefix, {}))
        if sort_level:
            siblings = sort_level(len(prefix), siblings)
        for child in siblings:
            if len(child) == depth:
                ordered.append(child)
            else:
                walk(child)
                ordered.append(child)

    walk(())
    return ordered


def _run_pivot_table(df, dimensions, pivots, metrics, sort) -> dict:
    """
    Tabla dinámica con varias filas (`dimensions`) y columnas (`pivots`) anidadas.

    Cada fila y cada columna tiene una clave (tupla de valores); las claves más cortas que la
    profundidad son subtotales. Los valores se calculan desde los datos en cada nivel (nunca
    sumando celdas) y `show_as` divide entre el total de la fila, de la columna o el general
    de ese mismo nivel.

    Retorna {"layout": "table", "dimensions", "pivots", "metrics",
             "column_keys": [tupla, ...],
             "rows": [{"key", "subtotal", "cells": {clave_col: {as: v}}, "totals": {as: v}}],
             "grand": {"cells": {...}, "totals": {...}}}
    """
    df = df.dropna(subset=[*dimensions, *pivots])
    metric_names = [m["as"] for m in metrics]

    cache: dict = {}

    def raw(columns, metric):
        cache_key = (tuple(columns), metric["as"])
        if cache_key not in cache:
            cache[cache_key] = _group_values(df, list(columns), metric)
        return cache[cache_key]

    def sort_rows(level, siblings):
        if not sort:
            return siblings
        reverse = sort["dir"] == "desc"
        if sort["by"] in dimensions:
            if dimensions.index(sort["by"]) != level:
                return siblings
            return sorted(siblings, key=lambda k: _sort_key(k[-1]), reverse=reverse)
        # Por una métrica: cada nivel se ordena por el valor de su grupo; vacíos al final.
        metric = next(m for m in metrics if m["as"] == sort["by"])
        values = raw(dimensions[:level + 1], metric)
        present = [k for k in siblings if values.get(k) is not None]
        missing = [k for k in siblings if values.get(k) is None]
        return sorted(present, key=lambda k: values[k], reverse=reverse) + missing

    row_keys = _hierarchy(_ordered_keys(df, dimensions), sort_rows)
    column_keys = _hierarchy(_ordered_keys(df, pivots))

    size = len(row_keys) * (len(column_keys) + 1) * len(metrics)
    if size > MAX_TABLE_CELLS:
        raise ResultTooLargeError(
            f"La tabla tendría {size:,} celdas (máximo {MAX_TABLE_CELLS:,}). Quita un nivel de "
            f"filas o columnas, o agrega filtros."
        )

    rows = [{"key": key, "subtotal": len(key) < len(dimensions), "cells": {}, "totals": {}} for key in row_keys]
    grand = {"cells": {}, "totals": {}}
    for metric in metrics:
        name, show_as = metric["as"], metric["show_as"]
        empty = 0 if metric["agg"] in _ZERO_WHEN_EMPTY else None
        grand_value = raw([], metric)[()]

        def column_total(col_key):
            return raw(pivots[:len(col_key)], metric).get(col_key, empty)

        def shown(value, row_total, col_total):
            if show_as == "value":
                return value
            whole = {"pct_row": row_total, "pct_column": col_total}.get(show_as, grand_value)
            return _percent(value, whole)

        for row in rows:
            key = row["key"]
            row_total = raw(dimensions[:len(key)], metric).get(key, empty)
            for col_key in column_keys:
                columns = dimensions[:len(key)] + pivots[:len(col_key)]
                value = raw(columns, metric).get(key + col_key, empty)
                row["cells"].setdefault(col_key, {})[name] = shown(value, row_total, column_total(col_key))
            row["totals"][name] = shown(row_total, row_total, grand_value)
        for col_key in column_keys:
            col_total = column_total(col_key)
            grand["cells"].setdefault(col_key, {})[name] = shown(col_total, grand_value, col_total)
        grand["totals"][name] = shown(grand_value, grand_value, grand_value)

    return {
        "layout": "table",
        "dimensions": list(dimensions),
        "pivots": list(pivots),
        "metrics": metric_names,
        "column_keys": column_keys,
        "rows": rows,
        "grand": grand,
    }


def _sort_key(value):
    # Números antes que textos, para no comparar tipos distintos.
    return (0, value, "") if isinstance(value, (int, float)) else (1, 0, str(value))
