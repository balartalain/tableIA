"""
Compilador de resultados (query_engine.run_data_spec) + view_spec al formato exacto que
consume el frontend (ApexCharts para kpi/bar/line, Tabulator para table). Es el ÚNICO lugar
que conoce esos formatos: si cambia la librería de gráficos, solo se reescribe este módulo.
"""
import pandas as pd

from sheets_reports.services.query_engine import to_python
from sheets_reports.services.spec_validation import humanize

PIVOT_FIELD_PREFIX = "__pivots"
TOTAL_FIELD_PREFIX = "__total"
TOTAL_LABEL = "Total general"


def pivot_field(pivot_value, metric: str) -> str:
    """Clave plana de la celda (fila, valor de pivote) en las filas de una tabla con pivote.
    Es una clave literal (no una ruta anidada): el frontend desactiva el separador de campos
    anidados de Tabulator, así los puntos dentro de un valor de pivote no rompen nada."""
    return f"{PIVOT_FIELD_PREFIX}.{pivot_value}.{metric}"


# Separa los valores de una clave de columna con varios pivotes ("2026" + "Ene"). Es un
# carácter de control: no aparece en los valores de una hoja.
KEY_SEPARATOR = "\x1f"


def cell_field(col_key: tuple, metric: str) -> str:
    """Clave de la celda (fila, columna) de una tabla dinámica; con un pivote coincide con
    pivot_field, así las preferencias guardadas por columna (formatos) se mantienen."""
    return pivot_field(KEY_SEPARATOR.join(str(v) for v in col_key), metric)


def total_field(metric: str) -> str:
    """Clave de la columna "Total general" de una métrica en una tabla con pivote."""
    return f"{TOTAL_FIELD_PREFIX}.{metric}"


def _label(view_spec: dict, name) -> str:
    return (view_spec.get("labels") or {}).get(name) or humanize(name)


def _metric_names(view_spec: dict) -> list[str]:
    return view_spec.get("metrics") or [view_spec["metric"]]


def _records(df: pd.DataFrame) -> list[dict]:
    return [{k: to_python(v) for k, v in row.items()} for row in df.to_dict(orient="records")]


def _percent_metrics(view_spec: dict) -> list[str]:
    # Widgets guardados antes de existir las métricas pct_* no traen la clave.
    return view_spec.get("percent") or []


def _compile_kpi(result, view_spec):
    compiled = {"value": to_python(result.get(view_spec["metric"])), "label": view_spec.get("label", "")}
    if view_spec["metric"] in _percent_metrics(view_spec):
        compiled["percent"] = True
    return compiled


def _compile_chart(result, view_spec):
    if isinstance(result, dict):
        categories = result["dimension_values"]
        series = [
            {"name": str(p), "data": [result["rows"][d][p][result["metric"]] for d in categories]}
            for p in result["pivot_values"]
        ]
    else:
        categories = [to_python(v) for v in result[view_spec["x"]]]
        series = [
            {"name": _label(view_spec, m), "data": [to_python(v) for v in result[m]]}
            for m in _metric_names(view_spec)
        ]
    compiled = {"series": series, "categories": [str(c) for c in categories]}
    # Nombres de las series que son porcentajes (con pivote, todas si la métrica lo es).
    percent = _percent_metrics(view_spec)
    if isinstance(result, dict):
        percent_series = [s["name"] for s in series] if result["metric"] in percent else []
    else:
        percent_series = [_label(view_spec, m) for m in _metric_names(view_spec) if m in percent]
    if percent_series:
        compiled["percent"] = percent_series
    if view_spec["widget"] == "bar":
        compiled["stacked"] = bool(view_spec.get("stacked"))
    return compiled


def _compile_donut(result, view_spec):
    metric = view_spec["metric"]
    return {
        "series": [to_python(v) for v in result[metric]],
        "labels": [str(to_python(v)) for v in result[view_spec["x"]]],
    }


def _pivot_columns(result: dict, view_spec: dict) -> list[dict]:
    """
    Columnas de los pivotes, anidadas como en Sheets: cada valor del primer pivote agrupa
    los del segundo, seguido de su columna "Total <valor>" (subtotal). Con una métrica, todo
    va bajo un grupo con el nombre de la métrica; con varias, cada columna hoja se abre en
    una subcolumna por métrica. Al final, la columna "Total general".
    """
    metrics, depth = result["metrics"], len(result["pivots"])
    single = len(metrics) == 1

    def node(header, col_key, **flags):
        if single:
            return {"header": header, "field": cell_field(col_key, metrics[0]), **flags}
        return {"header": header, **flags, "children": [
            {"header": _label(view_spec, m), "field": cell_field(col_key, m)} for m in metrics
        ]}

    top: list[dict] = []
    containers = {(): top}
    for key in result["column_keys"]:
        parent = key[:-1]
        if parent not in containers:
            group = {"header": str(parent[-1]), "children": []}
            containers[parent[:-1]].append(group)
            containers[parent] = group["children"]
        if len(key) == depth:
            containers[parent].append(node(str(key[-1]), key))
        else:
            containers[parent].append(node(f"Total {key[-1]}", key, subtotal=True))

    if single:
        metric = metrics[0]
        return [
            {"header": _label(view_spec, metric), "children": top},
            {"header": TOTAL_LABEL, "field": total_field(metric), "total": True},
        ]
    return [*top, {"header": TOTAL_LABEL, "total": True, "children": [
        {"header": _label(view_spec, m), "field": total_field(m)} for m in metrics
    ]}]


def _compile_table(result, view_spec):
    """
    Tabla dinámica (resultado de run_data_spec con layout="table"):
    - una columna por cada fila de la dimensión (`rowFields`), luego las métricas (sin
      pivote) o las columnas anidadas de los pivotes (_pivot_columns);
    - las filas de subtotal llevan "__subtotal": True y la etiqueta "Total <valor>";
    - la fila de totales va aparte en `totals` (el frontend la muestra como fila de pie);
    - `percent`: campos que son porcentajes, para que la tabla les ponga el formato %.
    """
    dimensions, pivots, metrics = result["dimensions"], result["pivots"], result["metrics"]
    columns = [{"header": _label(view_spec, d), "field": d} for d in dimensions]
    if pivots:
        columns += _pivot_columns(result, view_spec)
    else:
        columns += [{"header": _label(view_spec, m), "field": m} for m in metrics]

    def value_fields(cells, totals):
        record = {}
        for col_key, values in cells.items():
            for m, v in values.items():
                record[cell_field(col_key, m)] = v
        for m, v in totals.items():
            record[total_field(m) if pivots else m] = v
        return record

    rows = []
    for row in result["rows"]:
        key = row["key"]
        record = {}
        for i, d in enumerate(dimensions):
            if i >= len(key):
                record[d] = None
            elif i == len(key) - 1 and row["subtotal"]:
                record[d] = f"Total {key[i]}"
            else:
                record[d] = key[i]
        if row["subtotal"]:
            record["__subtotal"] = True
        record.update(value_fields(row["cells"], row["totals"]))
        rows.append(record)

    compiled = {
        "columns": columns,
        "rows": rows,
        "rowFields": dimensions,
        "totals": {dimensions[0]: TOTAL_LABEL, **value_fields(result["grand"]["cells"], result["grand"]["totals"])},
    }
    percent = [m for m in metrics if m in _percent_metrics(view_spec)]
    if pivots:
        percent_fields = [
            field for m in percent
            for field in [*(cell_field(k, m) for k in result["column_keys"]), total_field(m)]
        ]
    else:
        percent_fields = percent
    if percent_fields:
        compiled["percent"] = percent_fields
    return compiled


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
    - table:    {"columns", "rows", "rowFields", "totals", "percent"?} de una tabla dinámica
                (ver _compile_table); `result` debe venir de run_data_spec(layout="table").
    """
    try:
        compiler = _COMPILERS[widget_type]
    except KeyError:
        raise ValueError(f"Tipo de widget desconocido: {widget_type}")
    return compiler(result, view_spec)
