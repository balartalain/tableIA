"""Helpers de presentación compartidos por los compiladores de los widgets (formatos que
consume el frontend: ApexCharts para kpi/bar/line/donut, Tabulator para table)."""
from typing import List, Optional, Tuple

import pandas as pd

from sheets_reports.utils.data import is_number, to_python

TOTAL_LABEL = "Total general"

# Etiqueta en español de cada agregación: el nombre visible cuando no hay «Nombre a mostrar».
AGG_LABELS = {
    "sum": "Suma",
    "avg": "Promedio",
    "median": "Mediana",
    "min": "Mínimo",
    "max": "Máximo",
    "std": "Desviación estándar",
    "count": "Conteo",
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
    return metric.get("alias") or metric.get("field", "")


def metric_label(metric: dict) -> str:
    """Nombre a mostrar de una métrica: el `label` escrito por el usuario si lo hay (admite
    espacios y mayúsculas); sin él, la etiqueta en español del `agg` con el campo humanizado
    (p. ej. «Promedio Ventas»). El alias es la clave técnica y no se muestra en la UI."""
    label = str(metric.get("label") or "").strip()
    if label:
        return label
    agg = str(metric.get("agg") or "").strip()
    if agg == "auto":
        # Campo calculado agregado: su nombre ya dice qué es (ej. «% Ejecución»).
        return str(metric.get("field") or metric_alias(metric))
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


def metric_formats(fields=None, metadata=None) -> dict:
    """{alias: formato} de las métricas que no se muestran como número: el que calculó el motor
    (elegido en la métrica, en el campo calculado o en la columna de la fuente) y, con «Mostrar
    como» un porcentaje, «percent» salvo que la métrica pida barra."""
    if fields is None:
        return {}
    out = dict((metadata or {}).get("metric_formats") or {})
    for alias in (metadata or {}).get("percent_metrics") or []:
        out.setdefault(alias, "percent")
    for metric in fields.metrics or []:
        window = (metric or {}).get("window") or {}
        alias = metric_alias(metric)
        if alias and str(window.get("type", "")).startswith("percent_") and out.get(alias) != "progress":
            out[alias] = "percent"
    return out


def percent_aliases(fields=None, metadata=None) -> list:
    """Alias de las métricas cuyo valor ya es un porcentaje (`window` percent_*, un campo
    calculado o una columna con formato porcentaje): el frontend las formatea con «%» y con el
    estilo de porcentaje en las tablas."""
    return [alias for alias, fmt in metric_formats(fields, metadata).items()
            if fmt in ("percent", "progress")]


def percent_series(pairs, fields=None, metadata=None) -> list:
    """Nombres de las series (de `chart_series`) que son porcentajes: el frontend de los
    gráficos las reconoce por nombre. Con pivote cada serie es `{valor}_{alias}`."""
    aliases = percent_aliases(fields, metadata)
    pivoted = bool((metadata or {}).get("pivots"))

    def is_percent(column) -> bool:
        return any(column == alias or (pivoted and column.endswith(f"_{alias}")) for alias in aliases)

    return [name for name, column in pairs if is_percent(str(column))]


def value_column(result, fields=None, metadata=None) -> Optional[str]:
    """Columna numérica de una sola serie (dona, tabla simple)."""
    _categories, pairs = chart_series(result, fields, metadata)
    return pairs[0][1] if pairs else None
