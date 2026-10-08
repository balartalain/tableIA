"""
Gráfico de Barras: consulta con `WidgetFields`, apariencia con `WidgetStyle`.
"""
from typing import Any, ClassVar, Dict, List, Optional

import pandas as pd

from sheets_reports.widgets.base import WIDGETS, BaseWidget, WidgetResult
from sheets_reports.widgets.presentation import (chart_series, metric_alias, metric_label, percent_aliases,
                                                 percent_series)
from sheets_reports.widgets.schemas import WidgetFields, WidgetStyle


@WIDGETS.register
class BarChartWidget(BaseWidget):
    key = "bar"
    type_key = "bar"
    label = "Gráfico de Barras"
    ai_doc = 'comparar un número entre pocos grupos (ventas por categoría, por mes).'
    ai_examples = [
       (   'ventas por categoría',
            {   'widget_type': 'bar',
                'title': 'Ventas por categoría',
                'fields': {   'dimensions': ['categoria'],
                              'metrics': [   {   'agg': 'sum',
                                                 'field': 'ventas',
                                                 'alias': 'total_ventas'}],
                              'sort_by': '-total_ventas',
                              'limit': 5},
                'style': {'stacked': False}}),
        (   'ventas por mes separadas por categoría',
            {   'widget_type': 'bar',
                'title': 'Ventas por categoría y mes',
                'fields': {   'dimensions': ['categoria'],
                              'pivots': ['mes'],
                              'metrics': [   {   'agg': 'sum',
                                                 'field': 'ventas',
                                                 'alias': 'total_ventas'}]},
                'style': {'stacked': True}}),
        (   'ventas por categoría del último mes frente al anterior',
            {   'widget_type': 'bar',
                'title': 'Ventas por categoría: último mes vs. anterior',
                'fields': {   'dimensions': ['categoria'],
                              'metrics': [   {   'agg': 'sum',
                                                 'field': 'ventas',
                                                 'alias': 'ventas_ultimo_mes',
                                                 'label': 'Último mes',
                                                 'filters': [   {   'field': 'mes',
                                                                    'op': 'eq',
                                                                    'relative': 'latest'}]},
                                             {   'agg': 'sum',
                                                 'field': 'ventas',
                                                 'alias': 'ventas_mes_anterior',
                                                 'label': 'Mes anterior',
                                                 'filters': [   {   'field': 'mes',
                                                                    'op': 'eq',
                                                                    'relative': 'previous'}]}]},
                'style': {'stacked': False}})
    ]

    capabilities: ClassVar[dict] = {
        # Sin dimensión compara totales: una barra por métrica (al menos 2).
        "dimensions": [0, 1],
        "ungrouped_min_metrics": 2,
        "pivots": [0, 1],
        "metrics": [1, 5],
        "sort": True,
        "limit": True,
        "filters": True,
        "windows": ["percent_of_total", "percent_of_row", "running_total", "pct_change"],
    }

    # Backend-driven style schema (solo ui: text | select | checkbox | number)
    style_schema: ClassVar[List[Dict[str, Any]]] = [
        {"key": "title", "label": "Título", "type": "string", "default": "Gráfico de Barras"},
        {"key": "horizontal", "label": "Horizontal", "type": "boolean", "default": False},
        {"key": "stacked", "label": "Apilado", "type": "boolean", "default": False},
        {"key": "color_scheme", "label": "Paleta", "type": "choice", "default": "default", "options": [
            {"value": "default", "label": "Por defecto"},
            {"value": "ocean", "label": "Océano"},
            {"value": "forest", "label": "Bosque"},
            {"value": "sunset", "label": "Atardecer"},
        ]},
        {"key": "yAxisWidth", "label": "Ancho del Eje Y (px)", "type": "number"},
        {"key": "barWidth", "label": "Ancho de barra (%)", "type": "number", "default": 70},
        {"key": "dataLabelFormatter", "label": "Formato de etiquetas. Ej. {value} %", "type": "string"},
        {"key": "chartWidth", "label": "Forzar ancho de gráfico (px)", "type": "number"},
        {"key": "showGrid", "label": "Mostrar cuadrícula", "type": "boolean", "default": True},
        # Lo edita la leyenda arrastrable, no el panel.
        {"key": "seriesOrder", "label": "Orden de las series", "type": "list"},
    ]

    def compile(
        self,
        result: WidgetResult,
        style: WidgetStyle,
        fields: Optional[WidgetFields] = None,
        metadata: Optional[dict] = None,
    ) -> dict:
        style_dict = style.to_dict()
        if fields is not None and not fields.dimensions:
            categories, series, percent = self._ungrouped(result, fields, metadata)
        else:
            categories, pairs = chart_series(result, fields, metadata)
            df = result.rows
            series = [
                {"name": name, "data": df[column].fillna(0).tolist()}
                for name, column in pairs
            ]
            percent = percent_series(pairs, fields, metadata)

        horizontal = bool(style_dict.get("horizontal"))
        stacked = bool(style_dict.get("stacked"))
        reference_lines = style_dict.get("reference_lines") or []

        output = {
            "categories": categories,
            "series": series,
            "stacked": stacked,
            "percent": percent,
        }
        if fields is not None and not fields.dimensions:
            # Cada barra es una métrica: color por barra y sin leyenda (el nombre va en el eje).
            output["ungrouped"] = True
        if horizontal:
            output["horizontal"] = True
        if reference_lines:
            output["referenceLines"] = self._build_annotations(reference_lines, horizontal)

        return output

    @staticmethod
    def _ungrouped(result: WidgetResult, fields: WidgetFields, metadata: Optional[dict]):
        """Sin dimensión: el total de cada métrica, una barra por métrica en una sola serie."""
        metrics = [m for m in fields.metrics or [] if metric_alias(m)]
        row = result.rows.iloc[0] if not result.rows.empty else {}
        values = []
        for metric in metrics:
            value = row.get(metric_alias(metric)) if hasattr(row, "get") else None
            values.append(0 if value is None or pd.isna(value) else float(value))
        percent_all = set(percent_aliases(fields, metadata))
        percent = ["Total"] if metrics and all(metric_alias(m) in percent_all for m in metrics) else []
        return [metric_label(m) for m in metrics], [{"name": "Total", "data": values}], percent

    def _build_annotations(self, lines, horizontal):
        annotations = {"xaxis": [], "yaxis": []}
        key = "x" if horizontal else "y"
        for line in lines:
            if line.get("kind") == "value" and line.get("value") is not None:
                color = line.get("color", "#d97706")
                annotations["xaxis" if horizontal else "yaxis"].append({
                    key: line["value"],
                    "borderColor": color,
                    "strokeDashArray": 4,
                    "label": {
                        "text": line.get("label", f"Meta: {line['value']}"),
                        "borderColor": color,
                        "orientation": "horizontal",
                        "position": "top" if horizontal else "right",
                        "style": {"color": "#fff", "background": color, "fontSize": "10px"},
                    },
                })
        return annotations
