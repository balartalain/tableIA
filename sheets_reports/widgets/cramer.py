"""
V de Cramér: grado de asociación entre varias columnas categóricas (variables nominales u ordinales),
sobre las filas que cumplen los filtros. Las variables viven en `fields.columns`.
"""
from typing import Any, ClassVar, Dict, List, Optional

import numpy as np
import pandas as pd

from sheets_reports.widgets.base import WIDGETS, BaseWidget, WidgetResult
from sheets_reports.widgets.presentation import humanize
from sheets_reports.widgets.schemas import WidgetFields, WidgetStyle

MIN_ROWS = 3
TOP_PAIRS = 3


def _value(value) -> Optional[float]:
    return None if pd.isna(value) else round(float(value), 4)


def _cramers_v(s1: pd.Series, s2: pd.Series) -> tuple[Optional[float], int]:
    """Calcula la V de Cramér entre dos series categóricas y devuelve (v, n_filas)."""
    valid = s1.notna() & s2.notna()
    n = int(valid.sum())
    if n < MIN_ROWS:
        return None, n

    v1 = s1[valid].astype(str)
    v2 = s2[valid].astype(str)

    ct = pd.crosstab(v1, v2)
    r, c = ct.shape
    if r < 2 or c < 2:
        return None, n

    k = min(r - 1, c - 1)
    if k <= 0:
        return None, n

    obs = ct.values.astype(float)
    r_sum = obs.sum(axis=1, keepdims=True)
    c_sum = obs.sum(axis=0, keepdims=True)
    exp = (r_sum @ c_sum) / n

    with np.errstate(divide="ignore", invalid="ignore"):
        chi2 = np.where(exp > 0, ((obs - exp) ** 2) / exp, 0.0).sum()

    v = np.sqrt((chi2 / n) / k)
    v = float(np.clip(v, 0.0, 1.0))
    return round(v, 4), n


@WIDGETS.register
class CramerWidget(BaseWidget):
    key = "cramer"
    type_key = "cramer"
    label = "V de Cramér"
    ai_doc = ('mide el grado de asociación entre varias columnas categóricas (variables nominales '
              'u ordinales, de 0 a 1), como mapa de calor. Las variables van en columns.')
    ai_examples = [
        (   'qué relación hay entre categoría, vendedor y mes',
            {   'widget_type': 'cramer',
                'title': 'Asociación entre categoría, vendedor y mes',
                'fields': {'columns': [{'field': 'categoria'}, {'field': 'vendedor'}, {'field': 'mes'}]},
                'style': {}}),
    ]

    capabilities: ClassVar[dict] = {
        "columns": [2, 12],
        "columns_numeric": False,
        "columns_label": "Variables a analizar",
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
        {"key": "title", "label": "Título", "type": "string", "default": "V de Cramér"},
        {"key": "showValues", "label": "Mostrar valores", "type": "boolean", "default": True},
        {"key": "lowerOnly", "label": "Solo la mitad inferior", "type": "boolean", "default": True},
        {"key": "showTop", "label": "Mostrar lo más asociado", "type": "boolean", "default": True},
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
        labels = {c["field"]: c.get("label") or humanize(c["field"]) for c in ((fields.columns if fields else None) or [])}

        frame = result.rows
        columns = list(frame.columns)
        size = len(columns)

        matrix = [[None] * size for _ in range(size)]
        n = [[0] * size for _ in range(size)]

        for i in range(size):
            for j in range(size):
                if i == j:
                    col_data = frame[columns[i]].dropna()
                    if len(col_data.astype(str).unique()) >= 2 and len(col_data) >= MIN_ROWS:
                        matrix[i][j] = 1.0
                    else:
                        matrix[i][j] = None
                    n[i][j] = len(col_data)
                elif j > i:
                    v, count = _cramers_v(frame[columns[i]], frame[columns[j]])
                    matrix[i][j] = v
                    matrix[j][i] = v
                    n[i][j] = count
                    n[j][i] = count

        pairs = [(i, j) for i in range(size) for j in range(i + 1, size)
                 if matrix[i][j] is not None]
        pairs.sort(key=lambda p: (-matrix[p[0]][p[1]], p))
        top = [{"a": labels.get(columns[i], columns[i]), "b": labels.get(columns[j], columns[j]),
                "r": matrix[i][j], "n": n[i][j]} for i, j in pairs[:TOP_PAIRS]]

        return {
            "type": "cramer",
            "variables": [{"field": c, "label": labels.get(c, humanize(c))} for c in columns],
            "matrix": matrix,
            "n": n,
            "rows": len(frame),
            "top": top,
            "style": style_dict,
        }
