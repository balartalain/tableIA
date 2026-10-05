"""
Gráfico de Barras: consulta con `WidgetFields`, apariencia con `WidgetStyle`.
"""
from typing import Any, ClassVar, Dict, List, Optional

from sheets_reports.widgets.base import WIDGETS, BaseWidget, WidgetResult
from sheets_reports.widgets.presentation import chart_series, percent_aliases
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
                'style': {'stacked': True}})
    ]

    capabilities: ClassVar[dict] = {
        "dimensions": [1, 1],
        "pivots": [0, 1],
        "metrics": [1, 5],
        "sort": True,
        "limit": True,
        "filters": True,
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
    ]

    def compile(
        self,
        result: WidgetResult,
        style: WidgetStyle,
        fields: Optional[WidgetFields] = None,
        metadata: Optional[dict] = None,
    ) -> dict:
        style_dict = style.to_dict()
        categories, pairs = chart_series(result, fields, metadata)

        df = result.rows
        series = [
            {"name": name, "data": df[column].fillna(0).tolist()}
            for name, column in pairs
        ]

        horizontal = bool(style_dict.get("horizontal"))
        stacked = bool(style_dict.get("stacked"))
        reference_lines = style_dict.get("reference_lines") or []

        output = {
            "categories": categories,
            "series": series,
            "stacked": stacked,
            "percent": percent_aliases(fields),
        }
        if horizontal:
            output["horizontal"] = True
        if reference_lines:
            output["referenceLines"] = self._build_annotations(reference_lines, horizontal)

        return output

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
