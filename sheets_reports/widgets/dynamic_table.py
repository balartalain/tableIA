"""
Tabla Dinámica: `WidgetFields.dimensions` son las filas, `WidgetFields.pivots` las columnas.
El motor devuelve el resultado jerárquico (`metadata["nested"]`) con subtotales por nivel y
total general; los niveles que se dibujan se controlan con los flags de totales del estilo.
"""
from typing import Any, ClassVar, Dict, List, Optional

import pandas as pd

from sheets_reports.engine.steps.aggregation import cell_field, total_field
from sheets_reports.widgets.base import WIDGETS, BaseWidget, WidgetResult
from sheets_reports.widgets.presentation import (
    TOTAL_LABEL,
    humanize,
    metric_alias,
    metric_formats,
    metric_label,
    percent_aliases,
)
from sheets_reports.widgets.schemas import WidgetFields, WidgetStyle


def _pivot_columns(nested: dict, labels: dict) -> List[Dict[str, Any]]:
    """
    Columnas de los pivotes, anidadas como en una hoja de cálculo: cada valor del primer
    pivote agrupa los del segundo, seguido de su columna «Total <valor>» (subtotal). Al
    final, la columna «Total general». Con una métrica todo va bajo un grupo con su nombre;
    con varias, cada columna hoja se abre en una subcolumna por métrica.
    """
    col_keys, depth = nested["column_keys"], len(nested["pivots"])
    metrics = nested["metrics"]
    single = len(metrics) == 1

    def header(alias: str) -> str:
        return labels.get(alias) or humanize(alias)

    def node(header_text: str, col_key: tuple, **flags) -> Dict[str, Any]:
        if single:
            return {"header": header_text, "field": cell_field(col_key, metrics[0]), **flags}
        return {"header": header_text, **flags, "children": [
            {"header": header(alias), "field": cell_field(col_key, alias)} for alias in metrics
        ]}

    children: Dict[tuple, list] = {}
    for key in col_keys:
        children.setdefault(key[:-1], []).append(key)

    def build(prefix: tuple) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for key in children.get(prefix, []):
            if len(key) == depth:
                out.append(node(str(key[-1]), key))
            else:
                out.append({"header": str(key[-1]), "children": build(key)})
                out.append(node(f"Total {key[-1]}", key, subtotal=True))
        return out

    top = build(())
    if single:
        return [
            {"header": header(metrics[0]), "children": top},
            {"header": TOTAL_LABEL, "field": total_field(metrics[0]), "total": True},
        ]
    return [*top, {"header": TOTAL_LABEL, "total": True, "children": [
        {"header": header(alias), "field": total_field(alias)} for alias in metrics
    ]}]


@WIDGETS.register
class DynamicTableWidget(BaseWidget):
    key = "dynamic_table"
    type_key = "dynamic_table"
    label = "Tabla Dinámica"
    ai_doc = 'cruzar filas y columnas con totales (una tabla dinámica de la hoja).'
    ai_examples = [
       (   'ventas por categoría cruzadas con el mes',
            {   'widget_type': 'dynamic_table',
                'title': 'Ventas por categoría y mes',
                'fields': {   'dimensions': ['categoria'],
                              'pivots': ['mes'],
                              'metrics': [   {   'agg': 'sum',
                                                 'field': 'ventas',
                                                 'alias': 'total_ventas',
                                                 'label': 'Total de ventas'}]},
                'style': {'showTotals': True}}),
        (   'cuántas órdenes por vendedor y categoría',
            {   'widget_type': 'dynamic_table',
                'title': 'Órdenes por vendedor',
                'fields': {   'dimensions': ['vendedor'],
                              'pivots': ['categoria'],
                              'metrics': [{'agg': 'count', 'alias': 'ordenes'}]},
                'style': {}}),
        (   'ventas por categoría y su porcentaje del total',
            {   'widget_type': 'dynamic_table',
                'title': 'Ventas por categoría',
                'fields': {   'dimensions': ['categoria'],
                              'metrics': [   {   'agg': 'sum',
                                                 'field': 'ventas',
                                                 'alias': 'total_ventas'},
                                             {   'agg': 'sum',
                                                 'field': 'ventas',
                                                 'alias': 'pct_ventas',
                                                 'label': '% del total',
                                                 'window': {'type': 'percent_of_total'}}]},
                'style': {'showTotals': True}})
    ]

    capabilities: ClassVar[dict] = {
        # Al menos una: sin dimensiones sería un KPI dentro de una tabla o una tabla girada.
        "dimensions": [1, 3],
        "pivots": [0, 2],
        "metrics": [1, 5],
        "sort": True,
        "limit": False,
        "filters": True,
        "windows": ["percent_of_total", "percent_of_row"],
    }

    style_schema: ClassVar[List[Dict[str, Any]]] = [
        {"key": "title", "label": "Título", "type": "string", "default": "Tabla Dinámica"},
        {"key": "pageSize", "label": "Filas por página", "type": "number", "default": 10},
        {"key": "showPagination", "label": "Mostrar paginación", "type": "boolean", "default": True},
        {"key": "showTotals", "label": "Fila «Total general» al pie", "type": "boolean", "default": True},
        {"key": "rowSubtotal1", "label": "Subtotales «Total …» de cada dimensión 1", "type": "boolean", "default": False},
        {"key": "rowSubtotal2", "label": "Subtotales «Total …» de cada dimensión 2", "type": "boolean", "default": False},
        {"key": "showColumnTotals", "label": "Columna «Total general» a la derecha", "type": "boolean", "default": False},
        {"key": "columnSubtotal1", "label": "Subtotales «Total …» de cada pivote", "type": "boolean", "default": False},
        {"key": "repeatRowLabels", "label": "Repetir etiquetas de fila", "type": "boolean", "default": False},
    ]

    def compile(
        self,
        result: WidgetResult,
        style: WidgetStyle,
        fields: Optional[WidgetFields] = None,
        metadata: Optional[dict] = None,
    ) -> dict:
        style_dict = style.to_dict()
        meta = metadata or {}
        nested = meta.get("nested")
        if nested:
            return self._compile_nested(nested, style_dict, fields, meta)

        frame = result.rows
        dimensions = [d for d in (meta.get("dimensions") or []) if d in frame.columns]
        pivots = meta.get("pivots") or []
        pivot_values = meta.get("pivot_values") or []
        metrics = (fields.metrics if fields else None) or meta.get("metrics") or []

        return {
            "type": "tabulator",
            "columns": self._build_columns(frame, dimensions, pivots, pivot_values, metrics),
            "rows": frame.to_dict(orient="records"),
            "rowFields": dimensions,
            "totals": self._totals(frame, dimensions),
            "percent": percent_aliases(fields, meta),
            "formats": metric_formats(fields, meta),
            "style": style_dict,
        }

    def _compile_nested(self, nested: dict, style: dict, fields, metadata=None) -> dict:
        """
        - una columna por cada fila de la dimensión (`rowFields`), luego las métricas (sin
          pivote) o las columnas anidadas de los pivotes (`_pivot_columns`);
        - las filas de subtotal llevan `__subtotal: True` y la etiqueta «Total <valor>»;
        - la fila de totales va en `totals` (el frontend la dibuja como pie de la tabla);
        - `percent`: los campos que hay que formatear como porcentaje;
        - `formats`: el formato de cada campo de valor (moneda, %, barra) según su métrica.
        """
        dimensions = nested["dimensions"]
        pivots = nested["pivots"]
        metrics = nested["metrics"]
        labels = {metric_alias(m): metric_label(m) for m in ((fields.metrics if fields else None) or [])}

        columns: List[Dict[str, Any]] = [
            {"header": humanize(dim), "field": dim} for dim in dimensions
        ]
        if pivots:
            columns += _pivot_columns(nested, labels)
        else:
            columns += [{"header": labels.get(m) or humanize(m), "field": m} for m in metrics]

        def value_fields(cells, totals) -> Dict[str, Any]:
            record = {}
            for col_key, values in (cells or {}).items():
                for alias, value in values.items():
                    record[cell_field(col_key, alias)] = value
            for alias, value in (totals or {}).items():
                record[total_field(alias) if pivots else alias] = value
            return record

        rows = []
        for row in nested["rows"]:
            key = row["key"]
            record: Dict[str, Any] = {}
            for i, dim in enumerate(dimensions):
                if i >= len(key):
                    record[dim] = None
                elif i == len(key) - 1 and row["subtotal"]:
                    record[dim] = f"Total {key[i]}"
                else:
                    record[dim] = key[i]
            if row["subtotal"]:
                record["__subtotal"] = True
            record.update(value_fields(row["cells"], row["totals"]))
            rows.append(record)

        grand = value_fields(nested["grand"]["cells"], nested["grand"]["totals"])
        if dimensions:
            grand = {dimensions[0]: TOTAL_LABEL, **grand}

        percent = percent_aliases(fields, metadata)
        formats = metric_formats(fields, metadata)
        if pivots:
            # Con pivote cada métrica es una columna por valor del pivote más su «Total general».
            def pivot_fields(alias):
                return [*(cell_field(k, alias) for k in nested["column_keys"]), total_field(alias)]

            percent = [field for alias in percent for field in pivot_fields(alias)]
            formats = {field: fmt for alias, fmt in formats.items() for field in pivot_fields(alias)}

        return {
            "type": "tabulator",
            "columns": columns,
            "rows": rows,
            "rowFields": dimensions,
            "totals": grand,
            "percent": percent,
            "formats": formats,
            "style": style,
        }

    @staticmethod
    def _totals(frame, dimensions) -> Dict[str, Any]:
        """Total de cada columna numérica (incluidas las del pivote) para la fila inferior."""
        skip = set(dimensions)
        out: Dict[str, Any] = {}
        for col in frame.columns:
            if col in skip:
                continue
            series = frame[col]
            if pd.api.types.is_numeric_dtype(series):
                out[col] = float(series.sum())
        return out

    @staticmethod
    def _build_columns(frame, dimensions, pivots, pivot_values, metrics) -> List[Dict[str, Any]]:
        """Columnas Tabulator: `header` (etiqueta) + `field` (columna del resultado); con
        pivotes, cada métrica lleva una agrupación de hijos, uno por valor del pivote."""
        columns: List[Dict[str, Any]] = [
            {"header": humanize(dim), "field": dim}
            for dim in dimensions
            if dim in frame.columns
        ]

        if pivots and metrics:
            for metric in metrics:
                alias = metric_alias(metric)
                children = [
                    {"header": str(value), "field": f"{value}_{alias}"}
                    for value in pivot_values
                    if f"{value}_{alias}" in frame.columns
                ]
                if children:
                    columns.append({"header": metric_label(metric), "field": alias, "children": children})
                elif alias in frame.columns:
                    columns.append({"header": metric_label(metric), "field": alias})
        else:
            for metric in metrics:
                alias = metric_alias(metric)
                if alias in frame.columns:
                    columns.append({"header": metric_label(metric), "field": alias})

        # Columnas de la hoja que no participaron en la consulta (tabla simple sin pivotes).
        used = {c["field"] for c in columns}
        used |= {ch["field"] for c in columns for ch in c.get("children", [])}
        for col in frame.columns:
            if col in used:
                continue
            columns.append({"header": humanize(col), "field": col})

        return columns
