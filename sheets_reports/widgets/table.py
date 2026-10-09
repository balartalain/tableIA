"""
Tabla (filas de la hoja tal cual): las columnas a mostrar viven en `fields.columns`
(`{"field": ..., "label": ...}`), en orden, sin agrupar ni métricas.
"""
from typing import Any, ClassVar, Dict, List, Optional

from sheets_reports.utils.data import column_formats
from sheets_reports.widgets.base import WIDGETS, BaseWidget, WidgetResult
from sheets_reports.widgets.presentation import humanize
from sheets_reports.widgets.schemas import WidgetFields, WidgetStyle


MAX_ROWS = 2000


@WIDGETS.register
class TableWidget(BaseWidget):
    key = "table"
    type_key = "table"
    label = "Tabla"
    ai_doc = 'las filas de la hoja tal cual, con las columnas que el usuario elige (no agrupa).'
    ai_examples = [
       (   'muéstrame mes y ventas ordenado por ventas',
            {   'widget_type': 'table',
                'title': 'Ventas por mes',
                'fields': {   'columns': [   {'field': 'mes'},
                                            {'field': 'ventas', 'label': 'Ventas'}],
                              'sort_by': '-ventas'},
                'style': {'pageSize': 25}})
    ]

    capabilities: ClassVar[dict] = {
        "columns": [1, 50],
        "dimensions": [0, 0],
        "pivots": [0, 0],
        "metrics": [0, 0],
        "sort": True,
        "limit": False,
        "filters": True,
        "windows": [],
    }

    style_schema: ClassVar[List[Dict[str, Any]]] = [
        {"key": "title", "label": "Título", "type": "string", "default": "Tabla"},
        {"key": "pageSize", "label": "Filas por página", "type": "number", "default": 10},
        {"key": "showPagination", "label": "Mostrar paginación", "type": "boolean", "default": True},
    ]

    def process_query(self, df, fields: WidgetFields) -> WidgetResult:
        """Filtros/orden/límite del pipeline y, sin agrupar, las columnas pedidas en orden."""
        result = super().process_query(df, fields)
        frame = result.rows

        # Las columnas pedidas, en ese orden (la hoja sin agrupar).
        keep = [c["field"] for c in (fields.columns or []) if c["field"] in frame.columns]
        if keep:
            frame = frame[keep]

        return WidgetResult(
            data=frame.to_dict(orient="records"),
            metadata={**result.metadata, "dimensions": list(frame.columns),
                      # Formato de las columnas elegido en la fuente (moneda, %).
                      "column_formats": column_formats(df)},
            fields=fields,
            type="rows",
            frame=frame,
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

        # La hoja completa no entra en un JSON: se corta en MAX_ROWS y se avisa al frontend.
        total_rows = len(frame)
        truncated = total_rows > MAX_ROWS
        frame = frame.head(MAX_ROWS)

        numeric = set(frame.select_dtypes(include="number").columns)
        headers: Dict[str, str] = {
            c["field"]: (c.get("label") or humanize(c["field"])) for c in ((fields.columns if fields else None) or [])
        }
        columns = [
            {"header": headers.get(col) or humanize(col), "field": col, "numeric": col in numeric}
            for col in frame.columns
        ]
        return {
            "type": "tabulator",
            "columns": columns,
            "rows": frame.to_dict(orient="records"),
            "truncated": truncated,
            "total_rows": total_rows,
            "formats": {col: fmt for col, fmt in ((metadata or {}).get("column_formats") or {}).items()
                        if col in frame.columns},
            "style": style_dict,
        }
