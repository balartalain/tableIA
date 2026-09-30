"""
Motor de ejecución determinista del DSL: el ÚNICO lugar donde se hacen sumas, promedios y
conteos reales. La IA no participa aquí bajo ninguna circunstancia; `spec` ya viene validado
(services/spec_validation.py). Solo usa operaciones vectorizadas de pandas: nada de
DataFrame.query()/eval() ni ninguna otra forma de evaluar texto.
"""
import datetime
import math
import operator
import re
import unicodedata

import numpy as np
import pandas as pd

from sheets_reports.services.spec_validation import RANKING_RESULTS

# Tope de celdas de una tabla dinámica (filas × columnas × métricas): un cruce mayor no se
# puede leer y congelaría el navegador.
MAX_TABLE_CELLS = 20000
# Grupos de la sparkline de un KPI: más no se distinguen en una tarjeta.
MAX_TREND_POINTS = 60
# Etiqueta del grupo que junta lo que queda fuera del Top N.
OTHERS_LABEL = "Otros"
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
    if value is pd.NaT or value is pd.NA:
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


# ---------------------------------------------------------------------------
# Condiciones
# ---------------------------------------------------------------------------

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


def resolve_relative(series: pd.Series, relative: str, today: datetime.date | None = None):
    """Valor concreto de un filtro relativo: del reloj (año/mes actual) o de los datos de la
    columna (último, penúltimo o primer valor). None si la columna no tiene valores."""
    today = today or datetime.date.today()
    if relative == "current_year":
        return today.year
    if relative == "previous_year":
        return today.year - 1
    if relative == "current_month":
        return today.month
    values = sorted({to_key(v) for v in series.dropna()}, key=_sort_key)
    if not values:
        return None
    if relative == "max":
        return values[-1]
    if relative == "min":
        return values[0]
    # second_max: el valor anterior al último (ej. el año o periodo anterior en los datos).
    return values[-2] if len(values) > 1 else None


def _resolved(filters: list[dict] | None, df: pd.DataFrame) -> list[dict]:
    return [
        {"field": f["field"], "op": f["op"], "value": resolve_relative(df[f["field"]], f["relative"])}
        if "relative" in f else f
        for f in filters or []
    ]


def resolve_relative_filters(spec: dict, df: pd.DataFrame) -> dict:
    """Copia de `spec` con cada valor relativo convertido en su valor concreto, calculado UNA
    vez sobre `df` (la hoja con los filtros del tablero): "el último año" es el de la hoja,
    no el de cada grupo o punto de la tendencia."""
    def metric(m):
        m = dict(m)
        if m.get("filters"):
            m["filters"] = _resolved(m["filters"], df)
        if m["type"] == "grouped":
            m["inner"] = [metric(x) for x in m["inner"]]
        return m

    return {**spec, "filters": _resolved(spec.get("filters"), df), "metrics": [metric(m) for m in spec["metrics"]]}


def _condition_mask(df: pd.DataFrame, f: dict) -> pd.Series:
    raw = df[f["field"]]
    column = _column_for_comparison(raw)
    op = f["op"]
    if op == "is_empty":
        return raw.isna() | (column.astype("string").str.strip() == "")
    if op == "not_empty":
        return ~(raw.isna() | (column.astype("string").str.strip() == ""))
    if op == "contains":
        return column.astype("string").str.contains(str(f["value"]), case=False, regex=False)
    if op in ("in", "not_in"):
        cond = column.isin([_coerce_value(raw, v) for v in f["value"]])
        return ~cond if op == "not_in" else cond
    if op == "between":
        low, high = f["value"]
        return column.between(min(low, high), max(low, high))
    value = resolve_relative(raw, f["relative"]) if "relative" in f else f["value"]
    if value is None:  # valor relativo sin datos (ej. no hay "penúltimo año")
        return pd.Series(False, index=df.index)
    return _COMPARATORS[op](column, _coerce_value(raw, value))


def apply_filters(df: pd.DataFrame, filters: list[dict] | None) -> pd.DataFrame:
    mask = pd.Series(True, index=df.index)
    for f in filters or []:
        mask &= _condition_mask(df, f).fillna(False).astype(bool)
    return df[mask]


# ---------------------------------------------------------------------------
# Agregaciones
# ---------------------------------------------------------------------------

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


def _empty_value(metric: dict):
    return 0 if metric["agg"] in _ZERO_WHEN_EMPTY else None


def _aggregate(grouped, metric: dict) -> pd.Series:
    """Valor crudo por grupo, antes de aplicar `show_as`."""
    if metric["agg"] == "count":
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


def _frame(df: pd.DataFrame, metric: dict) -> pd.DataFrame:
    """Filas que resume una métrica: las del widget recortadas por sus propias condiciones
    (como CALCULATE: "ventas de 2026" y "ventas de 2025" en el mismo widget)."""
    return apply_filters(df, metric.get("filters")) if metric.get("filters") else df


def _show_as(metric: dict) -> str:
    return metric.get("show_as") or "value"


# Métricas calculadas: left <op> right. Nunca se evalúa texto: cada op es una función fija.
def _ratio_pct(left, right):
    return left / right * 100


def _diff_pct(left, right):
    return (left - right) / right * 100


_CALC_FUNCS = {
    "add": operator.add,
    "sub": operator.sub,
    "mul": operator.mul,
    "div": operator.truediv,
    "ratio_pct": _ratio_pct,
    "diff_pct": _diff_pct,
}
_DIVIDING_OPS = {"div", "ratio_pct", "diff_pct"}


def _calc(op: str, left, right):
    """Cálculo escalar; None si falta un lado o se divide entre cero."""
    left, right = to_python(left), to_python(right)
    if left is None or right is None or isinstance(left, dict) or isinstance(right, dict):
        return None
    if op in _DIVIDING_OPS and not right:
        return None
    value = _CALC_FUNCS[op](left, right)
    return round(value, 2) if op in ("ratio_pct", "diff_pct") else value


def _calc_series(op: str, left: pd.Series, right) -> pd.Series:
    """Cálculo por grupo (vectorizado); los grupos sin valor o con divisor 0 quedan en NaN."""
    left = pd.to_numeric(left, errors="coerce")
    right = pd.to_numeric(right, errors="coerce") if isinstance(right, pd.Series) else right
    if op in _DIVIDING_OPS:
        right = right.where(right != 0) if isinstance(right, pd.Series) else (right or np.nan)
    value = _CALC_FUNCS[op](left, right)
    return value.round(2) if op in ("ratio_pct", "diff_pct") else value


def _operand(values: dict, ref):
    return values.get(ref) if isinstance(ref, str) else ref


def _apply_calcs(values: dict, metrics: list[dict]) -> dict:
    """Completa `values` ({as: valor}) con las métricas calculadas, en orden."""
    for m in metrics:
        if m["type"] == "calc":
            values[m["as"]] = _calc(m["op"], _operand(values, m["left"]), _operand(values, m["right"]))
    return values


# ---------------------------------------------------------------------------
# Tabla plana por una columna (base de gráficos, having/limit, métricas por grupo)
# ---------------------------------------------------------------------------

def _flat_table(df: pd.DataFrame, dimension: str, metrics: list[dict]) -> pd.DataFrame:
    """
    Una fila por cada valor de `dimension` (en orden de aparición en la hoja) y una columna
    por métrica, con `show_as` aplicado. Las claves quedan crudas (sin to_key) para poder
    compararlas con la columna original. Los totales de cada métrica van en attrs["totals"].
    """
    df = df[df[dimension].notna()]
    keys = pd.Index(pd.unique(df[dimension]), name=dimension)
    columns: dict[str, pd.Series] = {}
    totals: dict = {}
    for m in metrics:
        name = m["as"]
        if m["type"] == "calc":
            right = columns[m["right"]] if isinstance(m["right"], str) else m["right"]
            columns[name] = _calc_series(m["op"], columns[m["left"]], right)
            totals[name] = _calc(m["op"], _operand(totals, m["left"]), _operand(totals, m["right"]))
            continue
        frame = _frame(df, m)
        values = _aggregate(frame.groupby(dimension, sort=False), m).reindex(keys)
        empty = _empty_value(m)
        if empty is not None:
            values = values.fillna(empty)
        grand = _total(frame, m)
        show_as = _show_as(m)
        if show_as == "value":
            totals[name] = to_python(grand)
        elif show_as == "pct_row":
            # Sin columnas de pivote, cada fila es el 100% de sí misma (como en Sheets).
            values = values.where(values.isna(), 100.0)
            totals[name] = 100.0 if to_python(grand) is not None else None
        else:
            values = pd.Series([_percent(v, grand) for v in values], index=keys, dtype="float64")
            totals[name] = _percent(grand, grand)
        columns[name] = values
    result = pd.DataFrame(columns, index=keys).reset_index()
    if result.empty:
        result = pd.DataFrame(columns=[dimension, *[m["as"] for m in metrics]])
    result.attrs["totals"] = totals
    return result


def _having_mask(table: pd.DataFrame, having: list[dict]) -> pd.Series:
    mask = pd.Series(True, index=table.index)
    for h in having or []:
        left = pd.to_numeric(table[h["left"]], errors="coerce")
        right = pd.to_numeric(table[h["right"]], errors="coerce") if isinstance(h["right"], str) else h["right"]
        cond = _COMPARATORS[h["op"]](left, right) & left.notna()
        if isinstance(right, pd.Series):
            cond &= right.notna()
        mask &= cond.fillna(False).astype(bool)
    return mask


def _sorted_table(table: pd.DataFrame, sort: dict) -> pd.DataFrame:
    return table.sort_values(
        sort["by"], ascending=sort["dir"] == "asc", na_position="last", kind="stable",
    ).reset_index(drop=True)


def _restrict_groups(df: pd.DataFrame, spec: dict) -> tuple[pd.DataFrame, bool]:
    """
    `having` y `limit` sobre los grupos de la primera dimensión: se calculan las métricas por
    grupo, se eligen los grupos y se recortan las FILAS del df a esos grupos. Así todo lo que
    sigue (pivotes, subtotales, porcentajes) se calcula sobre los grupos que se muestran.
    Con limit.others, los grupos fuera del Top N se renombran a «Otros» (sus valores se
    recalculan desde los datos, así funciona con cualquier agregación).
    Retorna (df, hay_grupo_otros).
    """
    having, limit = spec.get("having") or [], spec.get("limit")
    if not having and not limit:
        return df, False
    dimension = spec["dimensions"][0]
    table = _flat_table(df, dimension, spec["metrics"])
    table = table[_having_mask(table, having)]
    kept = list(table[dimension])
    if not limit:
        return df[df[dimension].isin(kept)], False
    top = list(_sorted_table(table, spec["sort"])[dimension][:limit["n"]])
    rest = [k for k in kept if k not in set(top)]
    if not (limit.get("others") and rest):
        return df[df[dimension].isin(top)], False
    df = df[df[dimension].isin(kept)].copy()
    df[dimension] = df[dimension].astype(object).where(df[dimension].isin(top), OTHERS_LABEL)
    return df, True


def _others_last(values: list, key=lambda v: v) -> list:
    return [v for v in values if key(v) != OTHERS_LABEL] + [v for v in values if key(v) == OTHERS_LABEL]


# ---------------------------------------------------------------------------
# KPI
# ---------------------------------------------------------------------------

def _grouped_value(df: pd.DataFrame, metric: dict):
    """
    Métrica «por grupo» de un KPI: agrupa por `group_by`, calcula las métricas internas de
    cada grupo, se queda con los que cumplen `having` y los resume:
      count / pct_groups      -> cuántos grupos cumplen (o qué % de los grupos)
      sum / avg / min / max   -> de la métrica interna `value` entre los que cumplen
      top / bottom            -> el grupo con el mayor/menor `value`: {"label", "value"}
    """
    table = _flat_table(_frame(df, metric), metric["group_by"], metric["inner"])
    matching = table[_having_mask(table, metric.get("having"))]
    result = metric["result"]
    if result == "count":
        return len(matching)
    if result == "pct_groups":
        return _percent(len(matching), len(table))
    values = pd.to_numeric(matching[metric["value"]], errors="coerce")
    if result in RANKING_RESULTS:
        values = values.dropna()
        if values.empty:
            return None
        # idxmax/idxmin: el primero en la hoja si hay empate.
        index = values.idxmax() if result == "top" else values.idxmin()
        return {"label": to_key(matching.loc[index, metric["group_by"]]), "value": to_python(values[index])}
    if values.dropna().empty:
        return None
    return to_python(getattr(values, {"avg": "mean"}.get(result, result))())


def _scalar_metrics(df: pd.DataFrame, universe: pd.DataFrame, metrics: list[dict]) -> dict:
    """{as: valor} de un KPI. Los porcentajes (show_as) son: valor con las condiciones del
    widget y de la métrica sobre el valor sin ellas (el universo)."""
    values: dict = {}
    for m in metrics:
        name = m["as"]
        if m["type"] == "agg":
            part = _total(_frame(df, m), m)
            values[name] = to_python(part) if _show_as(m) == "value" else _percent(part, _total(universe, m))
        elif m["type"] == "calc":
            values[name] = _calc(m["op"], _operand(values, m["left"]), _operand(values, m["right"]))
        else:
            values[name] = _grouped_value(df, m)
    return values


def _trend_metrics(metrics: list[dict], column: str) -> list[dict]:
    """Métricas tal como se evalúan dentro de cada punto de la tendencia. El bucket ya fija el
    valor de `column` (df[column] == key), así que un filtro eq sobre esa misma columna no
    distingue nada dentro del punto y solo sirve para vaciarlo. Sin quitarlo, "el último año"
    (resuelto UNA vez sobre toda la hoja) deja la serie con un único punto con datos y ceros en
    todos los demás: el KPI de "este año vs el anterior" dibujaría una línea plana en cero.
    Los operadores ne/rango y los filtros de otras columnas se conservan."""
    out = []
    for m in metrics:
        m = dict(m)
        if m.get("filters"):
            m["filters"] = [f for f in m["filters"] if not (f["field"] == column and f.get("op") == "eq")]
        out.append(m)
    return out


def _plain(text) -> str:
    """Texto en minúsculas, sin acentos ni punto final ("Sept." -> "sept")."""
    text = unicodedata.normalize("NFD", str(text).strip().lower())
    return "".join(c for c in text if unicodedata.category(c) != "Mn").rstrip(".")


def _natural_key(value):
    """Orden natural: los números dentro del texto se comparan como números
    ("2025-2" antes que "2025-10")."""
    return [(0, int(part), "") if part.isdigit() else (1, 0, part)
            for part in re.split(r"(\d+)", _plain(value)) if part]


def _chronological_keys(series: pd.Series) -> list:
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
        return sorted(keys, key=lambda k: _sort_key(to_key(k)))
    if all(_plain(k) in MONTH_ORDER for k in keys):
        return sorted(keys, key=lambda k: MONTH_ORDER[_plain(k)])
    dates = pd.to_datetime(pd.Series(keys, dtype="string"), errors="coerce", dayfirst=True, format="mixed")
    if not dates.isna().any():
        return [keys[i] for i in dates.argsort(kind="stable")]
    return sorted(keys, key=_natural_key)


def _trend(df: pd.DataFrame, universe: pd.DataFrame, spec: dict) -> dict:
    """Serie de la sparkline: el KPI calculado para cada valor de `trend_by`, de lo más
    antiguo a lo más reciente (con muchos valores, quedan los más recientes)."""
    column = spec["trend_by"]
    keys = _chronological_keys(df[column])[-MAX_TREND_POINTS:]
    metrics = [m for m in spec["metrics"] if not (m["type"] == "grouped" and m["result"] in RANKING_RESULTS)]
    metrics = _trend_metrics(metrics, column)
    series = {m["as"]: [] for m in metrics}
    for key in keys:
        point = _scalar_metrics(df[df[column] == key], universe[universe[column] == key], metrics)
        for name, value in point.items():
            series[name].append(value)
    return {"categories": [to_key(k) for k in keys], "series": series}


# ---------------------------------------------------------------------------
# Punto de entrada
# ---------------------------------------------------------------------------

def run_data_spec(df: pd.DataFrame, spec: dict, layout: str = "auto") -> pd.DataFrame | dict:
    """
    Ejecuta la agregación descrita en `spec` sobre `df`. Con layout="table" (widgets tabla)
    devuelve la tabla dinámica jerárquica de _run_pivot_table; si no, los formatos de abajo.

    `show_as` de cada métrica agg (como "Mostrar como" en las tablas dinámicas de Sheets):
    - value: el valor resumido.
    - pct_row: % del total de su fila de la dimensión (solo cambia algo con pivote).
    - pct_column: % del total de su columna (sin pivote: del total de todos los grupos).
    - pct_total: % del total general.
    En un kpi cualquier porcentaje es: valor con las condiciones del widget (y de la métrica)
    sobre el valor sin ellas. Los totales se calculan desde los datos (no sumando celdas), así
    el total de un promedio es el promedio real.

    - Sin dimensión (kpi): dict {as: valor}; una métrica top/bottom vale {"label", "value"}.
      Con trend_by, además "__trend": {"categories", "series": {as: [valores]}}.
    - Sin pivote: DataFrame plano con la columna de la dimensión + una columna por cada
      `metrics[].as`, en el orden de aparición en la hoja salvo que `sort` diga otra cosa.
      El total general de cada métrica va en `result.attrs["totals"]`.
    - Con pivote: dict anidado
        {"dimension", "pivot", "metric" (la primera), "metrics",
         "dimension_values": [...], "pivot_values": [...],
         "rows": {valor_dim: {valor_pivote: {as: valor}}},
         "row_totals": {valor_dim: {as: valor}}, "column_totals": {valor_pivote: {as: valor}},
         "grand_totals": {as: valor}}
      que apex_compiler consume para las series de gráfico.
    """
    # Universo de los porcentajes del KPI: la hoja antes de las condiciones del widget.
    universe = df
    spec = resolve_relative_filters(spec, universe)
    df = apply_filters(df, spec.get("filters"))
    metrics = spec["metrics"]
    dimensions = spec.get("dimensions") or []
    pivots = spec.get("pivots") or []
    sort = spec.get("sort")

    if not dimensions:
        result = _scalar_metrics(df, universe, metrics)
        if spec.get("trend_by"):
            result["__trend"] = _trend(df, universe, spec)
        return result

    df, has_others = _restrict_groups(df, spec)

    if layout == "table":
        return _run_pivot_table(df, dimensions, pivots, metrics, sort)

    dimension = dimensions[0]
    if not pivots:
        result = _flat_table(df, dimension, metrics)
        totals = result.attrs["totals"]
        if sort:
            result = _sorted_table(result, sort)
        if has_others:
            result = pd.concat([result[result[dimension] != OTHERS_LABEL],
                                result[result[dimension] == OTHERS_LABEL]], ignore_index=True)
        result[dimension] = [to_key(v) for v in result[dimension]]
        result.attrs["totals"] = totals
        return result

    return _run_pivot_chart(df, dimension, pivots[0], metrics[0], sort)


def _series_dict(series: pd.Series) -> dict:
    """{clave de grupo: valor}; claves compuestas (varias columnas) como tuplas."""
    return {
        (tuple(to_key(p) for p in k) if isinstance(k, tuple) else to_key(k)): to_python(v)
        for k, v in series.items()
    }


def _run_pivot_chart(df, dimension, pivot, metric, sort) -> dict:
    """Gráfico con pivote (una sola métrica agg): una serie por cada valor del pivote."""
    df = df[df[dimension].notna() & df[pivot].notna()]
    dimension_values = [to_key(v) for v in df[dimension].unique()]
    pivot_values = [to_key(v) for v in df[pivot].unique()]
    name, show_as = metric["as"], _show_as(metric)
    frame = _frame(df, metric)
    cells = _series_dict(_aggregate(frame.groupby([dimension, pivot], sort=False), metric))
    by_row = _series_dict(_aggregate(frame.groupby(dimension, sort=False), metric))
    by_column = _series_dict(_aggregate(frame.groupby(pivot, sort=False), metric))
    grand = to_python(_total(frame, metric))
    empty = _empty_value(metric)

    def shown(value, d=None, p=None):
        if show_as == "value":
            return value
        if show_as == "pct_row":
            whole = by_row.get(d, empty) if d is not None else grand
        elif show_as == "pct_column":
            whole = by_column.get(p, empty) if p is not None else grand
        else:
            whole = grand
        return _percent(value, whole)

    rows = {d: {p: {name: shown(cells.get((d, p), empty), d, p)} for p in pivot_values} for d in dimension_values}
    row_totals = {d: {name: shown(by_row.get(d, empty), d=d)} for d in dimension_values}
    column_totals = {p: {name: shown(by_column.get(p, empty), p=p)} for p in pivot_values}

    if sort:
        reverse = sort["dir"] == "desc"
        if sort["by"] == dimension:
            dimension_values.sort(key=_sort_key, reverse=reverse)
        else:
            # Ordena por el total de la fila en esa métrica; los vacíos siempre al final.
            present = [d for d in dimension_values if row_totals[d][name] is not None]
            missing = [d for d in dimension_values if row_totals[d][name] is None]
            present.sort(key=lambda d: row_totals[d][name], reverse=reverse)
            dimension_values = present + missing
    dimension_values = _others_last(dimension_values)

    return {
        "dimension": dimension,
        "pivot": pivot,
        "metric": name,
        "metrics": [name],
        "dimension_values": dimension_values,
        "pivot_values": pivot_values,
        "rows": rows,
        "row_totals": row_totals,
        "column_totals": column_totals,
        "grand_totals": {name: shown(grand)},
    }


# ---------------------------------------------------------------------------
# Tabla dinámica
# ---------------------------------------------------------------------------

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
    de ese mismo nivel. Las métricas calculadas se evalúan en cada celda con los valores ya
    mostrados de las métricas a las que se refieren.

    Retorna {"layout": "table", "dimensions", "pivots", "metrics",
             "column_keys": [tupla, ...],
             "rows": [{"key", "subtotal", "cells": {clave_col: {as: v}}, "totals": {as: v}}],
             "grand": {"cells": {...}, "totals": {...}}}
    """
    df = df.dropna(subset=[*dimensions, *pivots])
    metric_names = [m["as"] for m in metrics]
    by_name = {m["as"]: m for m in metrics}
    aggs = [m for m in metrics if m["type"] == "agg"]
    calcs = [m for m in metrics if m["type"] == "calc"]

    cache: dict = {}

    def raw(columns, metric):
        cache_key = (tuple(columns), metric["as"])
        if cache_key not in cache:
            cache[cache_key] = _group_values(_frame(df, metric), list(columns), metric)
        return cache[cache_key]

    def level_values(columns, metric) -> dict:
        """Valor de `metric` en cada grupo de `columns` (para ordenar): crudo en las agg,
        calculado en los cálculos."""
        if metric["type"] == "agg":
            return raw(columns, metric)
        left = level_values(columns, by_name[metric["left"]])
        right = level_values(columns, by_name[metric["right"]]) if isinstance(metric["right"], str) else None
        return {
            k: _calc(metric["op"], left.get(k), right.get(k) if right is not None else metric["right"])
            for k in left
        }

    def sort_rows(level, siblings):
        if sort:
            reverse = sort["dir"] == "desc"
            if sort["by"] in dimensions:
                if dimensions.index(sort["by"]) == level:
                    siblings = sorted(siblings, key=lambda k: _sort_key(k[-1]), reverse=reverse)
            else:
                # Por una métrica: cada nivel se ordena por el valor de su grupo; vacíos al final.
                values = level_values(dimensions[:level + 1], by_name[sort["by"]])
                present = [k for k in siblings if values.get(k) is not None]
                missing = [k for k in siblings if values.get(k) is None]
                siblings = sorted(present, key=lambda k: values[k], reverse=reverse) + missing
        return _others_last(siblings, key=lambda k: k[0]) if level == 0 else siblings

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
    for metric in aggs:
        name, show_as = metric["as"], _show_as(metric)
        empty = _empty_value(metric)
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

    if calcs:
        for block in [*rows, grand]:
            for values in [*block["cells"].values(), block["totals"]]:
                _apply_calcs(values, calcs)

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
