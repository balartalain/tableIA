"""Helpers de presentación compartidos por los compiladores de los widgets (formatos que
consume el frontend: ApexCharts para kpi/bar/line/donut, Tabulator para table)."""
from typing import List, Optional, Tuple

import pandas as pd

from sheets_reports.dsl.values import is_number, to_python

TOTAL_LABEL = "Total general"

# Etiqueta en español de cada agregación: el nombre visible cuando no hay «Nombre a mostrar».
AGG_LABELS = {
    "sum": "Suma",
    "avg": "Promedio",
    "median": "Mediana",
    "min": "Mínimo",
    "max": "Máximo",
    "std": "Desviación estándar",
    "count": "Conteo de filas",
    "count_distinct": "Valores distintos",
}


def agg_label(agg) -> str:
    return AGG_LABELS.get(str(agg or "").strip(), str(agg or "").strip())


def humanize(name) -> str:
    text = str(name).replace("_", " ").strip()
    return text[:1].upper() + text[1:]


def number(value):
    value = to_python(value)
    return value if is_number(value) else None


def column_values(df: pd.DataFrame, column: str) -> list:
    return [to_python(v) for v in df[column]]


def metric_alias(metric: dict) -> str:
    return metric.get("alias") or metric.get("as") or metric.get("field", "")


def metric_label(metric: dict) -> str:
    """Nombre a mostrar de una métrica: el `label` escrito por el usuario si lo hay (admite
    espacios y mayúsculas); sin él, la etiqueta en español del `agg` con el campo humanizado
    (p. ej. «Promedio Ventas»). El alias es la clave técnica y no se muestra en la UI."""
    label = str(metric.get("label") or "").strip()
    if label:
        return label
    agg = str(metric.get("agg") or "").strip()
    if agg:
        return f"{agg_label(agg)} {humanize(metric.get('field') or '')}".strip()
    alias = metric_alias(metric)
    return humanize(alias) if alias else ""


def chart_series(result, fields=None, metadata=None) -> Tuple[List[str], List[Tuple[str, str]]]:
    """
    Del resultado del pipeline, las categorías del eje y las series `[(nombre, columna)]`.

    - Con `pivots`: una serie por valor del pivote (`{valor}_{alias}`).
    - Sin pivotes: una serie por métrica, con su nombre humanizado.
    """
    meta = metadata or {}
    df = result.rows

    dimensions = meta.get("dimensions") or (fields.dimensions if fields else []) or []
    pivots = meta.get("pivots") or (fields.pivots if fields else []) or []
    pivot_values = meta.get("pivot_values") or []
    metrics = (fields.metrics if fields else None) or meta.get("metrics") or []

    lead = dimensions[0] if dimensions else None
    cat_col = lead if lead and lead in df.columns else None
    categories = [str(v) for v in df[cat_col].tolist()] if cat_col else [str(i) for i in range(len(df))]

    alias = metric_alias(metrics[0]) if metrics else ""
    if pivots and pivot_values and alias:
        pairs = [(str(value), f"{value}_{alias}") for value in pivot_values]
        pairs = [(name, col) for name, col in pairs if col in df.columns]
    else:
        labels = {metric_alias(m): metric_label(m) for m in metrics}
        pairs = [(labels.get(col) or humanize(col), col) for col in df.columns if col != cat_col]

    return categories, pairs


def percent_aliases(fields=None) -> list:
    """Alias de las métricas cuyo valor ya es un porcentaje (`window` percent_*): el frontend
    las formatea con «%» y con el estilo de porcentaje en las tablas."""
    if fields is None:
        return []
    out = []
    for metric in fields.metrics or []:
        window = (metric or {}).get("window") or {}
        if str(window.get("type", "")).startswith("percent_"):
            alias = metric_alias(metric)
            if alias:
                out.append(alias)
    return out


def value_column(result, fields=None, metadata=None) -> Optional[str]:
    """Columna numérica de una sola serie (dona, tabla simple)."""
    _categories, pairs = chart_series(result, fields, metadata)
    return pairs[0][1] if pairs else None
