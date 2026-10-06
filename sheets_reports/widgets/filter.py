"""
Widget de filtros del tablero: no consulta, publica las columnas y sus valores posibles.
"""
from typing import Any, ClassVar, Dict, List, Optional

import pandas as pd

from sheets_reports.widgets.base import WIDGETS, BaseWidget, WidgetResult
from sheets_reports.engine.steps.filter import distinct_values
from sheets_reports.utils.data import to_python
from sheets_reports.widgets.presentation import humanize
from sheets_reports.widgets.schemas import WidgetFields, WidgetStyle

MAX_FILTER_VALUES = 100


@WIDGETS.register
class FilterWidget(BaseWidget):
    key = "filter"
    type_key = "filter"
    label = "Filtros"
    ai_doc = 'una caja de filtros fija arriba del tablero (elegir categoría, año, vendedor).'
    ai_examples = [
       (   'que se pueda filtrar por categoría y año',
            {   'widget_type': 'filter',
                'title': 'Filtros',
                'fields': {'dimensions': ['categoria', 'anio'], 'metrics': []},
                'style': {'layout': 'horizontal'}})
    ]
    # La caja lista los valores de la hoja completa: la selección filtra a los demás widgets,
    # no a sí misma (así siempre se pueden ampliar y quitar filtros).
    board_filtered = False
    max_per_dashboard = 1

    capabilities: ClassVar[dict] = {
        # Aquí `dimensions` son las columnas que la caja expone al usuario.
        "dimensions": [0, 50],
        "dimensions_label": "Columnas del filtro",
        "dimensions_hint": "Columnas que se muestran como controles de filtro.",
        "pivots": [0, 0],
        "metrics": [0, 0],
        "sort": False,
        "limit": False,
        "filters": False,
        "windows": [],
    }

    style_schema: ClassVar[List[Dict[str, Any]]] = [
        {"key": "title", "label": "Título", "type": "string", "default": "Filtros"},
        {"key": "layout", "label": "Layout", "type": "choice", "default": "horizontal", "options": [
            {"value": "horizontal", "label": "Horizontal"},
            {"value": "vertical", "label": "Vertical"},
        ]},
    ]

    def process_query(self, df: pd.DataFrame, fields: WidgetFields) -> WidgetResult:
        # Las columnas elegidas en `fields.dimensions` (vacío = todas) definen los controles.
        chosen = [c for c in (fields.dimensions or []) if c in df.columns]
        columns = chosen or list(df.columns)
        return WidgetResult(
            data={"columns": columns},
            metadata={"fields": fields, "dimensions": columns},
            fields=fields,
            type="columns",
            frame=df,
        )

    def compile(
        self,
        result: WidgetResult,
        style: WidgetStyle,
        fields: Optional[WidgetFields] = None,
        metadata: Optional[dict] = None,
    ) -> dict:
        style_dict = style.to_dict()
        frame = result.rows
        columns = metadata.get("dimensions") or list(frame.columns)

        filters: List[Dict[str, Any]] = []
        for col in columns:
            if col not in frame.columns:
                continue
            values = distinct_values(frame[col])
            truncated = len(values) > MAX_FILTER_VALUES
            filters.append({
                "field": col,
                "label": humanize(col),
                "type": "multi_select",
                "options": [to_python(v) for v in values[:MAX_FILTER_VALUES]],
                "truncated": truncated,
            })

        return {
            "type": "filter",
            "columns": columns,
            "filters": filters,
            "style": style_dict,
        }
