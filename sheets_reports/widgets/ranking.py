"""
Ranking: el top N de los grupos de una columna (vendedores, productos) según una métrica, con
la condición de los filtros. «Los mejores» o «los peores» salen de la dirección de `sort_by`
y de si un valor alto es mejor (`style.higher_is_better`): en ventas lo mejor es lo alto; en
reclamos o días de entrega, lo bajo.

Además del corte: posiciones con empates (1, 2, 2, 4), los grupos sin valor no compiten, el
% del total (solo con métricas que se suman) y «el resto» al pie. Una sola métrica: la que
clasifica.
"""
import dataclasses
import math
from typing import Any, ClassVar, Dict, List, Optional

import pandas as pd

from sheets_reports.utils.data import to_python
from sheets_reports.widgets.base import WIDGETS, BaseWidget, WidgetResult
from sheets_reports.widgets.presentation import metric_alias, metric_formats, metric_label
from sheets_reports.widgets.schemas import WidgetFields, WidgetStyle

# Sin «Cuántos» elegido.
DEFAULT_TOP = 10
# Agregaciones cuyos grupos suman el total: solo con ellas tiene sentido el % del total.
SHARE_AGGS = ("sum", "count")


def _number(value) -> Optional[float]:
    """El valor como número; vacío, NaN o infinito → None (el JSON no admite NaN)."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


@WIDGETS.register
class RankingWidget(BaseWidget):
    key = "ranking"
    type_key = "ranking"
    label = "Ranking"
    ai_doc = ('el top N de los grupos de una columna según una métrica, los mejores o los peores '
              '(los 5 vendedores que más venden, los 3 productos con menos ventas). sort_by es '
              'el alias de la métrica con «-» para los más altos; limit es N.')
    ai_examples = [
        (   'los 5 vendedores que más vendieron en 2026',
            {   'widget_type': 'ranking',
                'title': 'Top vendedores 2026',
                'fields': {   'dimensions': ['vendedor'],
                              'metrics': [   {   'agg': 'sum',
                                                 'field': 'ventas',
                                                 'alias': 'total_ventas'}],
                              'filters': [{'field': 'anio', 'op': 'eq', 'value': 2026}],
                              'sort_by': '-total_ventas',
                              'limit': 5},
                'style': {'higher_is_better': True}}),
        (   'los 3 productos que menos se venden',
            {   'widget_type': 'ranking',
                'title': 'Productos con menos ventas',
                'fields': {   'dimensions': ['producto'],
                              'metrics': [   {   'agg': 'sum',
                                                 'field': 'ventas',
                                                 'alias': 'total_ventas'}],
                              'sort_by': 'total_ventas',
                              'limit': 3},
                'style': {'higher_is_better': True}}),
    ]

    capabilities: ClassVar[dict] = {
        "dimensions": [1, 1],
        "pivots": [0, 0],
        # Una sola: la que clasifica.
        "metrics": [1, 1],
        "sort": True,
        "limit": True,
        "filters": True,
        "windows": [],
        "dimensions_label": "Qué se clasifica",
    }

    style_schema: ClassVar[List[Dict[str, Any]]] = [
        {"key": "title", "label": "Título", "type": "string", "default": "Ranking"},
        {"key": "higher_is_better", "label": "Un valor más alto es mejor", "type": "boolean", "default": True},
        {"key": "showBars", "label": "Mostrar barras", "type": "boolean", "default": True},
        {"key": "showShare", "label": "Mostrar % del total", "type": "boolean", "default": True},
        {"key": "showRest", "label": "Mostrar el resto", "type": "boolean", "default": True},
        {"key": "abbreviate", "label": "Abreviar (1.2M)", "type": "boolean", "default": False},
    ]

    # ------------------------------------------------------------------ datos
    @staticmethod
    def ranked_metric(fields: WidgetFields) -> Optional[dict]:
        """La métrica que clasifica (la única); `sort_by` solo da la dirección."""
        return next((m for m in (fields.metrics or []) if metric_alias(m)), None)

    @staticmethod
    def descending(fields: WidgetFields) -> bool:
        """Sin orden elegido, de mayor a menor (el «top» de siempre)."""
        return not fields.sort_by or str(fields.sort_by).startswith("-")

    def process_query(self, df: pd.DataFrame, fields: WidgetFields) -> WidgetResult:
        """Todos los grupos (sin orden ni límite del motor), clasificados y cortados en N; el
        total y el resto se calculan antes del corte."""
        result = super().process_query(df, dataclasses.replace(fields, sort_by=None, limit=None))
        frame = result.rows
        metric = self.ranked_metric(fields)
        alias = metric_alias(metric) if metric else None
        dimension = (fields.dimensions or [None])[0]
        metadata = {**result.metadata, "ranked": alias, "total_groups": 0}
        if not alias or alias not in frame.columns:
            return WidgetResult(data=[], metadata=metadata, fields=fields, type="ranking",
                                frame=frame.iloc[0:0])

        # Sin valor no compite: con «los peores» un vacío saldría primero.
        values = pd.to_numeric(frame[alias], errors="coerce").replace([math.inf, -math.inf], math.nan)
        frame = frame.assign(**{alias: values}).dropna(subset=[alias])
        descending = self.descending(fields)
        # Empatados, por nombre: el orden no cambia de una carga a otra.
        keys = [alias, dimension] if dimension in frame.columns else [alias]
        frame = frame.sort_values(keys, ascending=[not descending, True][:len(keys)], kind="mergesort")
        frame = frame.assign(__rank=frame[alias].rank(method="min", ascending=not descending).astype(int))

        limit = fields.limit if isinstance(fields.limit, int) and fields.limit > 0 else DEFAULT_TOP
        top, rest = frame.head(limit), frame.iloc[limit:]
        # El % del total solo con métricas que se suman y sin negativos (si no, no reparte un todo).
        shareable = (metric.get("agg") in SHARE_AGGS and not metric.get("window")
                     and bool((frame[alias] >= 0).all()))
        metadata.update({
            "total_groups": len(frame),
            "total": float(frame[alias].sum()) if shareable else None,
            "rest": {"count": len(rest), "value": float(rest[alias].sum()) if shareable else None},
        })
        return WidgetResult(data=top.to_dict(orient="records"), metadata=metadata, fields=fields,
                            type="ranking", frame=top)

    # ------------------------------------------------------------- dibujo
    @staticmethod
    def _label(value) -> str:
        if value is None or (not isinstance(value, str) and pd.isna(value)):
            return "(vacío)"
        return str(to_python(value))

    def compile(
        self,
        result: WidgetResult,
        style: WidgetStyle,
        fields: Optional[WidgetFields] = None,
        metadata: Optional[dict] = None,
    ) -> dict:
        style_dict = style.to_dict()
        meta = metadata or {}
        fields = fields or WidgetFields()
        formats = metric_formats(fields, meta)
        metric = self.ranked_metric(fields)
        alias = metric_alias(metric) if metric else None
        dimension = (fields.dimensions or [None])[0]

        higher_is_better = bool(style_dict.get("higher_is_better", True))
        order = "best" if self.descending(fields) == higher_is_better else "worst"
        total = meta.get("total")

        def share(value):
            return round(value / total * 100, 2) if total and value is not None else None

        frame = result.rows
        items = []
        for _, row in frame.iterrows():
            value = _number(row.get(alias))
            items.append({
                "rank": int(row["__rank"]),
                "label": self._label(row.get(dimension)) if dimension else "",
                "value": value,
                "share": share(value),
            })

        rest = meta.get("rest") or {}
        rest_out = None
        if rest.get("count"):
            rest_out = {"count": rest["count"], "value": _number(rest.get("value")),
                        "share": share(_number(rest.get("value")))}

        return {
            "type": "ranking",
            "dimension": dimension,
            "metric": {"alias": alias, "label": metric_label(metric) if metric else "",
                       "format": formats.get(alias)},
            "order": order,
            "items": items,
            "rest": rest_out,
            "total_groups": meta.get("total_groups", len(items)),
            "style": style_dict,
        }
