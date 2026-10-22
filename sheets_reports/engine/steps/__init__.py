"""
El motor: cuatro pasos atómicos que un widget encadena en `BaseWidget.process_query`.

    1. filter_rows               (filter.py)       condiciones sobre las filas
       expand_grid               (grid.py)         solo si usa una cuadrícula: la desdobla
    2. apply_aggregation         (aggregation.py)  groupby, pivote, escalar o tabla dinámica
                                                   (incluidos los campos calculados agregados)
    3. apply_window_functions    (window.py)       percent_of_total, running_total, ...
    4. apply_sort_limit          (sort.py)         sort_by + limit

Cada paso recibe el frame y un dict `metadata` compartido, donde la agregación deja la forma
del resultado. `run_steps` es la secuencia por defecto; un widget con necesidades atípicas
reusa solo los pasos que le sirven. No conoce widgets: devuelve `{data, type, metadata}`.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd

from sheets_reports.engine.steps.aggregation import apply_aggregation
from sheets_reports.engine.steps.filter import filter_rows
from sheets_reports.engine.steps.grid import expand_grid, grid_in_use, split_filters
from sheets_reports.engine.steps.sort import apply_sort_limit
from sheets_reports.engine.steps.window import apply_window_functions


def run_steps(df: pd.DataFrame, fields, widget_type: Optional[str] = None) -> Dict[str, Any]:
    """La secuencia por defecto de los cuatro pasos sobre `df`. Si la consulta usa las columnas
    virtuales de una cuadrícula (grid.py), el filtro se parte: las condiciones sobre columnas
    reales en ancho, la cuadrícula se desdobla y después las condiciones sobre las virtuales."""
    metadata: Dict[str, Any] = {}
    grid = grid_in_use(df, fields)
    if grid is None:
        df = filter_rows(df, fields.filters, metadata)
    else:
        real, virtual = split_filters(fields.filters, grid.names)
        df = filter_rows(df, real, metadata)
        applied = metadata.get("filters_applied", 0)
        df = filter_rows(expand_grid(df, grid, fields), virtual, metadata)
        if real and virtual:
            metadata["filters_applied"] += applied
        metadata["grid"] = grid.question
    df = apply_aggregation(df, fields, metadata, widget_type=widget_type)
    df = apply_window_functions(df, fields.metrics, metadata)
    df = apply_sort_limit(df, fields.sort_by, fields.limit, metadata)
    return build_query_result(df, fields, metadata)


def build_query_result(df: pd.DataFrame, fields, metadata: Dict[str, Any]) -> Dict[str, Any]:
    """Convierte el estado final de los pasos en `{data, type, metadata}`."""
    base_meta = {
        "fields": fields,
        "dimensions": metadata.get("dimensions", []),
        "pivots": metadata.get("pivots", []),
        "metrics": fields.metrics or [],
        "pivot_column": metadata.get("pivot_column"),
        "pivot_values": metadata.get("pivot_values", []),
        "dimension_values": metadata.get("dimension_values", []),
        "totals": metadata.get("totals", {}),
        "row_totals": metadata.get("row_totals", []),
        "column_totals": metadata.get("column_totals", []),
        # Tabla dinámica: resultado jerárquico con subtotales y total general.
        "nested": metadata.get("nested"),
        # Métricas de campos calculados con formato porcentaje.
        "percent_metrics": metadata.get("percent_metrics", []),
        # Formato de cada métrica ({alias: "currency" | "percent" | "progress"}).
        "metric_formats": metadata.get("metric_formats", {}),
    }
    records = df.to_dict(orient="records")

    if "scalar_result" in metadata:
        return {"data": metadata["scalar_result"], "type": "scalar", "metadata": base_meta}

    if "flat_result" in metadata:
        flat = metadata["flat_result"]
        # El frame final (no el de la agregación) es el que ya pasó por orden y límite.
        dimension = flat["dimension"]
        values = df[dimension].tolist() if dimension in df.columns else flat["dimension_values"]
        return {"data": records, "type": "flat", "dimension": dimension,
                "dimension_values": values, "totals": flat["totals"], "metadata": base_meta}

    if "pivot_chart_result" in metadata:
        chart = metadata["pivot_chart_result"]
        return {"data": records, "type": "pivot_chart", "dimension": chart["dimension"],
                "pivot_values": chart["pivot_values"], "metrics": chart["metric"],
                "metadata": base_meta}

    if metadata.get("pivoted"):
        return {"data": records, "type": "pivot_table", "dimensions": base_meta["dimensions"],
                "pivots": base_meta["pivots"], "metadata": base_meta}

    return {"data": records, "type": "flat" if metadata.get("aggregated") else "rows",
            "columns": list(df.columns), "metadata": base_meta}
