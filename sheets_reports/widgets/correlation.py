"""
Matriz de correlación: qué tan relacionadas están entre sí varias columnas numéricas, sobre las
filas que cumplen los filtros. Las variables viven en `fields.columns` (como en la Tabla, pero
solo numéricas: `columns_numeric`); el método (Pearson o Spearman) en `style.method`.

Cada par usa sus filas completas (una fila sin valor se descarta solo para los pares de esa
columna) y guarda cuántas usó (`n`). Con menos de MIN_ROWS filas, o con una columna constante,
no hay correlación: la celda va vacía (None), no un 0 engañoso.
"""
from typing import Any, ClassVar, Dict, List, Optional

import pandas as pd

from sheets_reports.widgets.base import WIDGETS, BaseWidget, WidgetResult
from sheets_reports.widgets.presentation import humanize
from sheets_reports.widgets.schemas import WidgetFields, WidgetStyle

# Menos filas que esto en un par no dicen nada de la relación.
MIN_ROWS = 3
# Pares en «lo más relacionado».
TOP_PAIRS = 3
METHODS = ("pearson", "spearman")


def _value(value) -> Optional[float]:
    return None if pd.isna(value) else round(float(value), 4)


@WIDGETS.register
class CorrelationWidget(BaseWidget):
    key = "correlation"
    type_key = "correlation"
    label = "Matriz de correlación"
    ai_doc = ('qué tan relacionadas están entre sí varias columnas numéricas (si cuando sube una '
              'sube o baja la otra), como mapa de calor. Las variables van en columns.')
    ai_examples = [
        (   'qué relación hay entre ventas, costo y plan',
            {   'widget_type': 'correlation',
                'title': 'Relación entre ventas, costo y plan',
                'fields': {'columns': [{'field': 'ventas'}, {'field': 'costo'}, {'field': 'plan'}]},
                'style': {'method': 'pearson'}}),
    ]

    capabilities: ClassVar[dict] = {
        "columns": [2, 12],
        "columns_numeric": True,
        "columns_label": "Variables a correlacionar",
        # Sin texto de ayuda bajo las variables («Cómo se lee» ya explica la escala).
        "columns_hint": "",
        "dimensions": [0, 0],
        "pivots": [0, 0],
        "metrics": [0, 0],
        "sort": False,
        "limit": False,
        "filters": True,
        "windows": [],
    }

    style_schema: ClassVar[List[Dict[str, Any]]] = [
        {"key": "title", "label": "Título", "type": "string", "default": "Matriz de correlación"},
        {"key": "method", "label": "Método", "type": "choice", "default": "pearson", "options": [
            {"value": "pearson", "label": "Pearson (relación lineal)"},
            {"value": "spearman", "label": "Spearman (por rangos)"},
        ]},
        {"key": "showValues", "label": "Mostrar valores", "type": "boolean", "default": True},
        {"key": "lowerOnly", "label": "Solo la mitad inferior", "type": "boolean", "default": True},
        {"key": "showTop", "label": "Mostrar lo más relacionado", "type": "boolean", "default": True},
    ]

    def process_query(self, df: pd.DataFrame, fields: WidgetFields) -> WidgetResult:
        """Las filas que pasan los filtros (sin agrupar), solo con las variables pedidas."""
        result = super().process_query(df, fields)
        frame = result.rows
        keep = [c["field"] for c in (fields.columns or []) if c["field"] in frame.columns]
        frame = frame[keep]
        return WidgetResult(data=None, metadata=result.metadata, fields=fields, type="rows", frame=frame)

    def compile(
        self,
        result: WidgetResult,
        style: WidgetStyle,
        fields: Optional[WidgetFields] = None,
        metadata: Optional[dict] = None,
    ) -> dict:
        style_dict = style.to_dict()
        method = style_dict.get("method") if style_dict.get("method") in METHODS else "pearson"
        labels = {c["field"]: c.get("label") or humanize(c["field"]) for c in ((fields.columns if fields else None) or [])}

        frame = result.rows.apply(pd.to_numeric, errors="coerce")
        columns = list(frame.columns)
        corr = frame.corr(method=method, min_periods=MIN_ROWS)
        present = frame.notna().astype(int)
        counts = present.T @ present

        matrix = [[_value(corr.at[a, b]) for b in columns] for a in columns]
        n = [[int(counts.at[a, b]) for b in columns] for a in columns]

        pairs = [(i, j) for i in range(len(columns)) for j in range(i + 1, len(columns))
                 if matrix[i][j] is not None]
        pairs.sort(key=lambda p: (-abs(matrix[p[0]][p[1]]), p))
        top = [{"a": labels.get(columns[i], columns[i]), "b": labels.get(columns[j], columns[j]),
                "r": matrix[i][j], "n": n[i][j]} for i, j in pairs[:TOP_PAIRS]]

        return {
            "type": "correlation",
            "method": method,
            "variables": [{"field": c, "label": labels.get(c, humanize(c))} for c in columns],
            "matrix": matrix,
            "n": n,
            "rows": len(frame),
            "top": top,
            "style": style_dict,
        }
