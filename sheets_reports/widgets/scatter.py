"""
Gráfico de dispersión: una fila de la hoja es un punto, con una columna numérica en el eje X y
otra en el Y, sobre las filas que cumplen los filtros. Las dos columnas viven en
`fields.columns` (como en la correlación, solo numéricas: `columns_numeric`), en orden: X, Y.

Opcionalmente, una columna de categorías en `fields.dimensions` reparte los puntos en grupos
(un color y una línea de tendencia por grupo). Aquí la dimensión no agrupa ni agrega: cada fila
sigue siendo un punto. Con más de MAX_GROUPS categorías, las menos frecuentes van a «Otros».

Solo cuentan las filas con los dos valores. Con más de MAX_POINTS, se dibuja una muestra fija
(siempre la misma para los mismos datos, proporcional en cada grupo) y las tendencias se
calculan con todas.
"""
import dataclasses
from typing import Any, ClassVar, Dict, List, Optional

import numpy as np
import pandas as pd

from sheets_reports.widgets.base import WIDGETS, BaseWidget, WidgetResult
from sheets_reports.widgets.presentation import humanize
from sheets_reports.widgets.schemas import WidgetFields, WidgetStyle

# Más puntos que esto no se distinguen y vuelven lento el navegador.
MAX_POINTS = 2000
# Un color por grupo (los de la paleta de gráficos): el resto se junta en «Otros».
MAX_GROUPS = 8
OTHERS = "Otros"
NO_VALUE = "(Sin valor)"


def _trend(x: pd.Series, y: pd.Series) -> Optional[dict]:
    """Recta de mínimos cuadrados y su r de Pearson; None si X no varía o hay menos de 2 puntos."""
    if len(x) < 2 or x.nunique() < 2:
        return None
    slope, intercept = np.polyfit(x, y, 1)
    r = x.corr(y) if y.nunique() > 1 else None
    return {
        "slope": float(slope),
        "intercept": float(intercept),
        "r": None if r is None or pd.isna(r) else round(float(r), 4),
        "from": float(x.min()),
        "to": float(x.max()),
    }


def _group_names(values: pd.Series) -> pd.Series:
    """La categoría de cada fila: su valor, «(Sin valor)» o «Otros» si no está entre las
    MAX_GROUPS más frecuentes (con «Otros», las primeras MAX_GROUPS - 1)."""
    names = values.map(lambda v: NO_VALUE if pd.isna(v) or str(v).strip() == "" else str(v))
    counts = names.value_counts(sort=True)
    if len(counts) <= MAX_GROUPS:
        return names
    kept = set(counts.index[:MAX_GROUPS - 1])
    return names.where(names.isin(kept), OTHERS)


@WIDGETS.register
class ScatterWidget(BaseWidget):
    key = "scatter"
    type_key = "scatter"
    label = "Gráfico de dispersión"
    ai_doc = ('cómo se relacionan dos columnas numéricas fila a fila (¿a más experiencia, más '
              'salario?): un punto por fila. columns lleva exactamente dos: primero la del eje X, '
              'luego la del eje Y. dimensions (opcional, una columna de categorías) colorea los '
              'puntos por grupo para comparar el patrón de cada uno.')
    ai_examples = [
        (   'relación entre ventas y costo',
            {   'widget_type': 'scatter',
                'title': 'Ventas frente a costo',
                'fields': {'columns': [{'field': 'ventas'}, {'field': 'costo'}]},
                'style': {'showTrend': True}}),
        (   'ventas frente a costo por categoría',
            {   'widget_type': 'scatter',
                'title': 'Ventas frente a costo por categoría',
                'fields': {'columns': [{'field': 'ventas'}, {'field': 'costo'}],
                           'dimensions': ['categoria']},
                'style': {'showTrend': True}}),
    ]

    capabilities: ClassVar[dict] = {
        "columns": [2, 2],
        "columns_numeric": True,
        "columns_label": "Ejes (X, Y)",
        "columns_hint": "La primera columna va en el eje X y la segunda en el eje Y. Cada fila es un punto.",
        # Aquí `dimensions` no agrupa: es la columna que da color a los puntos.
        "dimensions": [0, 1],
        "dimensions_label": "Agrupar por",
        "dimensions_hint": "Una columna de categorías: cada valor se dibuja con su color y su línea de tendencia.",
        "pivots": [0, 0],
        "metrics": [0, 0],
        "sort": False,
        "limit": False,
        "filters": True,
        "windows": [],
    }

    style_schema: ClassVar[List[Dict[str, Any]]] = [
        {"key": "title", "label": "Título", "type": "string", "default": "Gráfico de dispersión"},
        {"key": "showTrend", "label": "Línea de tendencia", "type": "boolean", "default": True},
        {"key": "markerSize", "label": "Tamaño de los puntos (px)", "type": "number", "default": 5},
        {"key": "showGrid", "label": "Mostrar cuadrícula", "type": "boolean", "default": True},
    ]

    def process_query(self, df: pd.DataFrame, fields: WidgetFields) -> WidgetResult:
        """Las filas que pasan los filtros (sin agrupar), solo con los ejes y el color."""
        result = super().process_query(df, dataclasses.replace(fields, dimensions=[]))
        frame = result.rows
        wanted = [c["field"] for c in (fields.columns or [])] + list(fields.dimensions or [])[:1]
        keep = list(dict.fromkeys(c for c in wanted if c in frame.columns))
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
        rows = result.rows
        columns = [c for c in ((fields.columns if fields else None) or []) if c["field"] in rows.columns]
        axes = [{"field": c["field"], "label": c.get("label") or humanize(c["field"])} for c in columns[:2]]
        if len(axes) < 2:
            return {"type": "scatter", "x": None, "y": None, "color": None, "groups": [],
                    "rows": 0, "shown": 0, "trend": None, "style": style_dict}

        x_field, y_field = axes[0]["field"], axes[1]["field"]
        color_field = next((d for d in ((fields.dimensions if fields else None) or []) if d in rows.columns), None)

        frame = rows[[x_field, y_field]].apply(pd.to_numeric, errors="coerce")
        frame["__group"] = _group_names(rows[color_field]) if color_field else ""
        frame = frame.dropna(subset=[x_field, y_field])
        frame = frame[np.isfinite(frame[[x_field, y_field]]).all(axis=1)]
        shown = frame.sample(n=MAX_POINTS, random_state=0).sort_index() if len(frame) > MAX_POINTS else frame

        # Por frecuencia, con «Otros» y «(Sin valor)» al final.
        order = frame["__group"].value_counts(sort=True).index.tolist()
        order = [g for g in order if g not in (OTHERS, NO_VALUE)] + [g for g in (NO_VALUE, OTHERS) if g in order]
        groups = []
        for name in order:
            every = frame[frame["__group"] == name]
            drawn = shown[shown["__group"] == name]
            groups.append({
                "name": name,
                "points": [[float(a), float(b)] for a, b in drawn[[x_field, y_field]].itertuples(index=False)],
                "rows": len(every),
                "trend": _trend(every[x_field], every[y_field]),
            })

        return {
            "type": "scatter",
            "x": axes[0],
            "y": axes[1],
            "color": {"field": color_field, "label": humanize(color_field)} if color_field else None,
            "groups": groups,
            "rows": len(frame),
            "shown": len(shown),
            "trend": _trend(frame[x_field], frame[y_field]),
            "style": style_dict,
        }
