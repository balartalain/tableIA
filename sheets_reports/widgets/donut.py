"""
Gráfico de Dona: consulta con `WidgetFields`, apariencia con `WidgetStyle`.
"""
from typing import Any, ClassVar, Dict, List, Optional

from sheets_reports.widgets.base import WIDGETS, BaseWidget, WidgetResult
from sheets_reports.widgets.presentation import chart_series, percent_aliases
from sheets_reports.widgets.schemas import WidgetFields, WidgetStyle


@WIDGETS.register
class DonutWidget(BaseWidget):
    key = "donut"
    type_key = "donut"
    label = "Gráfico de Dona"
    ai_doc = 'la composición de un total en pocas partes (qué % de ventas viene de cada categoría).'
    ai_examples = [
       (   'qué parte de las ventas viene de cada categoría',
            {   'widget_type': 'donut',
                'title': 'Participación por categoría',
                'fields': {   'dimensions': ['categoria'],
                              'metrics': [   {   'agg': 'sum',
                                                 'field': 'ventas',
                                                 'alias': 'total_ventas'}],
                              'sort_by': '-total_ventas'},
                'style': {'labelMode': 'percent'}})
    ]

    capabilities: ClassVar[dict] = {
        "dimensions": [1, 1],
        "pivots": [0, 0],
        "metrics": [1, 1],
        "sort": True,
        "limit": True,
        "filters": True,
    }

    style_schema: ClassVar[List[Dict[str, Any]]] = [
        {"key": "title", "label": "Título", "ui": "text", "default": "Gráfico de Dona"},
        {"key": "labelMode", "label": "Mostrar en porciones", "ui": "select", "options": [
            {"value": "percent", "label": "Porcentaje"},
            {"value": "value", "label": "Valor"},
        ], "default": "percent"},
        {"key": "donutSize", "label": "Tamaño del hueco (%)", "ui": "number", "min": 30, "max": 80, "step": 5, "default": 50},
        {"key": "showLegend", "label": "Mostrar leyenda", "ui": "checkbox", "default": True},
    ]

    def compile(
        self,
        result: WidgetResult,
        style: WidgetStyle,
        fields: Optional[WidgetFields] = None,
        metadata: Optional[dict] = None,
    ) -> dict:
        categories, pairs = chart_series(result, fields, metadata)

        df = result.rows
        if pairs:
            column = pairs[0][1]
            labels = categories
            values = df[column].fillna(0).tolist()
        else:
            labels = []
            values = []

        output = {"series": values, "labels": labels,
                  "percent": percent_aliases(fields)}

        return output
