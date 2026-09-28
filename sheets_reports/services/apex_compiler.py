"""
Compilador de resultados (query_engine.run_data_spec) + view_spec al formato exacto que
consume el frontend (ApexCharts para kpi/bar/line, Tabulator para table). Es el ÚNICO lugar
que conoce esos formatos: si cambia la librería de gráficos, solo se reescribe este módulo.
"""
import pandas as pd

from sheets_reports.services.query_engine import to_python
from sheets_reports.services.spec_validation import humanize

PIVOT_FIELD_PREFIX = "__pivots"


def pivot_field(pivot_value, metric: str) -> str:
    """Clave plana de la celda (fila, valor de pivote) en las filas de una tabla con pivote.
    Es una clave literal (no una ruta anidada): el frontend desactiva el separador de campos
    anidados de Tabulator, así los puntos dentro de un valor de pivote no rompen nada."""
    return f"{PIVOT_FIELD_PREFIX}.{pivot_value}.{metric}"


def _label(view_spec: dict, name) -> str:
    return (view_spec.get("labels") or {}).get(name) or humanize(name)


def _metric_names(view_spec: dict) -> list[str]:
    return view_spec.get("metrics") or [view_spec["metric"]]


def _records(df: pd.DataFrame) -> list[dict]:
    return [{k: to_python(v) for k, v in row.items()} for row in df.to_dict(orient="records")]


def _compile_kpi(result, view_spec):
    return {"value": to_python(result.get(view_spec["metric"])), "label": view_spec.get("label", "")}


def _compile_chart(result, view_spec):
    if isinstance(result, dict):
        categories = result["dimension_values"]
        series = [
            {"name": str(p), "data": [result["rows"][d][p] for d in categories]}
            for p in result["pivot_values"]
        ]
    else:
        categories = [to_python(v) for v in result[view_spec["x"]]]
        series = [
            {"name": _label(view_spec, m), "data": [to_python(v) for v in result[m]]}
            for m in _metric_names(view_spec)
        ]
    compiled = {"series": series, "categories": [str(c) for c in categories]}
    if view_spec["widget"] == "bar":
        compiled["stacked"] = bool(view_spec.get("stacked"))
    return compiled


def _compile_donut(result, view_spec):
    metric = view_spec["metric"]
    return {
        "series": [to_python(v) for v in result[metric]],
        "labels": [str(to_python(v)) for v in result[view_spec["x"]]],
    }


def _compile_table(result, view_spec):
    columns = []
    for col in view_spec["columns"]:
        if "pivotOf" in col:
            metric = col["pivotOf"]
            pivot_values = result["pivot_values"] if isinstance(result, dict) else []
            columns.append({
                "header": col["header"],
                "children": [{"header": str(p), "field": pivot_field(p, metric)} for p in pivot_values],
            })
        else:
            columns.append({"header": col["header"], "field": col["field"]})

    if isinstance(result, dict):
        dimension, metric = result["dimension"], result["metric"]
        rows = []
        for d in result["dimension_values"]:
            row = {dimension: d}
            for p, v in result["rows"][d].items():
                row[pivot_field(p, metric)] = v
            rows.append(row)
    else:
        rows = _records(result)
    return {"columns": columns, "rows": rows}


_COMPILERS = {
    "kpi": _compile_kpi,
    "bar": _compile_chart,
    "line": _compile_chart,
    "donut": _compile_donut,
    "table": _compile_table,
}


def compile_view(widget_type: str, result, view_spec: dict) -> dict:
    """
    Traduce el resultado de run_data_spec + view_spec al formato de cada widget:
    - kpi:      {"value": ..., "label": ...}
    - bar/line: {"series": [...], "categories": [...]} (+ "stacked" en bar); una serie por
                métrica sin seriesBy, o una por cada valor del pivote con seriesBy.
    - donut:    {"series": [valores], "labels": [categorías]} (formato nativo de ApexCharts).
    - table:    {"columns": [...], "rows": [...]}; con pivote, la columna de la métrica
                lleva "children" con un hijo por cada valor del pivote.
    """
    try:
        compiler = _COMPILERS[widget_type]
    except KeyError:
        raise ValueError(f"Tipo de widget desconocido: {widget_type}")
    return compiler(result, view_spec)
