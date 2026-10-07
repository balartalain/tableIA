"""
Gráfico de Líneas: consulta con `WidgetFields`, apariencia con `WidgetStyle`.
"""
from typing import Any, ClassVar, Dict, List, Optional

from sheets_reports.widgets.base import WIDGETS, BaseWidget, WidgetResult
from sheets_reports.widgets.presentation import chart_series, percent_series
from sheets_reports.widgets.schemas import WidgetFields, WidgetStyle


@WIDGETS.register
class LineWidget(BaseWidget):
    key = "line"
    type_key = "line"
    label = "Gráfico de Líneas"
    ai_doc = 'la evolución de un número en el tiempo (serie por mes o por año).'
    ai_examples = [
       (   'cómo evolucionaron las ventas mes a mes',
            {   'widget_type': 'line',
                'title': 'Ventas por mes',
                'fields': {   'dimensions': ['mes'],
                              'metrics': [   {   'agg': 'sum',
                                                 'field': 'ventas',
                                                 'alias': 'total_ventas'}],
                              'sort_by': 'mes'},
                'style': {'showMarkers': True}})
    ]

    capabilities: ClassVar[dict] = {
        "dimensions": [1, 1],
        "pivots": [0, 1],
        "metrics": [1, 5],
        "sort": True,
        "limit": True,
        "filters": True,
        "windows": ["percent_of_total", "running_total", "pct_change"],
    }

    style_schema: ClassVar[List[Dict[str, Any]]] = [
        {"key": "title", "label": "Título", "type": "string", "default": "Gráfico de Líneas"},
        {"key": "color_scheme", "label": "Paleta", "type": "choice", "default": "default", "options": [
            {"value": "default", "label": "Por defecto"},
            {"value": "ocean", "label": "Océano"},
            {"value": "forest", "label": "Bosque"},
            {"value": "sunset", "label": "Atardecer"},
        ]},
        {"key": "curve", "label": "Curva", "type": "choice", "default": "monotoneCubic", "options": [
            {"value": "monotoneCubic", "label": "Suave (monotoneCubic)"},
            {"value": "straight", "label": "Recta"},
            {"value": "smooth", "label": "Suave (smooth)"},
        ]},
        {"key": "showGrid", "label": "Mostrar cuadrícula", "type": "boolean", "default": True},
        {"key": "showMarkers", "label": "Mostrar puntos", "type": "boolean", "default": True},
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
        series = [
            {"name": name, "data": df[column].fillna(0).tolist()}
            for name, column in pairs
        ]

        # Sin orden explícito, un eje de tiempo se ordena cronológicamente (como el acumulado y
        # la variación, engine/steps/window.py); otras categorías quedan en el orden del cálculo.
        sort_by = fields.sort_by if fields else None
        if not sort_by and categories:
            try:
                from sheets_reports.utils.data import time_order

                ordered = time_order(categories)
                if ordered is not None and ordered != categories:
                    positions = {value: index for index, value in enumerate(categories)}
                    order = [positions[value] for value in ordered if value in positions]
                    if order:
                        categories = ordered
                        for item in series:
                            item["data"] = [item["data"][index] for index in order]
            except Exception:  # noqa: BLE001 - el orden cronológico es best-effort
                pass

        output = {"categories": categories, "series": series,
                  "percent": percent_series(pairs, fields, metadata)}

        return output
