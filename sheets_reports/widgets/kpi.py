"""
Tarjeta KPI: consulta con `WidgetFields` (primera métrica = principal, segunda = comparación),
apariencia con `WidgetStyle`.
"""
from typing import Any, ClassVar, Dict, List, Optional

from sheets_reports.widgets.base import WIDGETS, BaseWidget, WidgetResult
from sheets_reports.widgets.presentation import humanize, metric_alias
from sheets_reports.widgets.schemas import WidgetFields, WidgetStyle


@WIDGETS.register
class KpiWidget(BaseWidget):
    key = "kpi"
    type_key = "kpi"
    label = "Tarjeta KPI"
    ai_doc = 'uno o pocos números a la vista (ventas del mes, cumplimiento de una meta).'
    ai_examples = [
       (   'ventas totales de este año',
            {   'widget_type': 'kpi',
                'title': 'Ventas del año',
                'fields': {   'dimensions': [],
                              'metrics': [   {   'agg': 'sum',
                                                 'field': 'ventas',
                                                 'alias': 'ventas_anio',
                                                 'filters': [   {   'field': 'anio',
                                                                    'op': 'eq',
                                                                    'relative': 'current_year'}]}]},
                'style': {'prefix': 'RD$ '}}),
        (   'cuántas órdenes entregamos y cómo van contra la meta',
            {   'widget_type': 'kpi',
                'title': 'Órdenes',
                'fields': {   'dimensions': [],
                              'metrics': [   {'agg': 'count', 'alias': 'ordenes'},
                                             {   'agg': 'sum',
                                                 'field': 'plan',
                                                 'alias': 'meta'}]},
                'style': {   'target': 1000,
                             'targetLabel': 'Meta',
                             'status_good': 100,
                             'status_warn': 80}})
    ]

    capabilities: ClassVar[dict] = {
        "dimensions": [0, 0],
        "pivots": [0, 0],
        "metrics": [1, 4],
        "sort": False,
        "limit": False,
        "filters": True,
    }

    style_schema: ClassVar[List[Dict[str, Any]]] = [
        {"key": "title", "label": "Título", "ui": "text", "default": "Tarjeta KPI"},
        {"key": "decimals", "label": "Decimales", "ui": "number", "min": 0, "step": 1, "default": 0},
        {"key": "abbreviate", "label": "Abreviar (1.2M)", "ui": "checkbox", "default": False},
        {"key": "prefix", "label": "Prefijo (ej. RD$)", "ui": "text", "default": ""},
        {"key": "suffix", "label": "Sufijo (ej. uds.)", "ui": "text", "default": ""},
        {"key": "compareMode", "label": "Modo de comparación", "ui": "select", "options": [
            {"value": "pct", "label": "Porcentaje"},
            {"value": "abs", "label": "Absoluto"},
        ], "default": "pct"},
        {"key": "higher_is_better", "label": "Más alto es mejor", "ui": "checkbox", "default": True},
        {"key": "target", "label": "Meta (valor)", "ui": "number", "step": 1},
        {"key": "targetLabel", "label": "Etiqueta de la meta", "ui": "text", "default": "Meta"},
        {"key": "status_good", "label": "Umbral «bien» (%)", "ui": "number", "min": 0, "step": 1},
        {"key": "status_warn", "label": "Umbral «ajuste» (%)", "ui": "number", "min": 0, "step": 1},
    ]

    def compile(
        self,
        result: WidgetResult,
        style: WidgetStyle,
        fields: Optional[WidgetFields] = None,
        metadata: Optional[dict] = None,
    ) -> dict:
        style_dict = style.to_dict()
        metrics = list(fields.metrics or []) if fields else []
        df = result.rows

        primary = metric_alias(metrics[0]) if metrics else None
        compare = metric_alias(metrics[1]) if len(metrics) > 1 else None

        if df.empty:
            return {"type": "kpi", "value": 0, "formatted_value": "0", "label": "",
                    "compare": None, "target": None, "status": None, "style": style_dict}

        row = df.iloc[0]
        primary_value = self._number(row.get(primary, 0))
        compare_value = self._number(row.get(compare, 0)) if compare else 0

        decimals = int(style_dict.get("decimals", 0) or 0)
        abbreviate = bool(style_dict.get("abbreviate"))
        prefix = style_dict.get("prefix") or ""
        suffix = style_dict.get("suffix") or ""

        def format_value(value: float) -> str:
            if abbreviate:
                if abs(value) >= 1_000_000:
                    return f"{value / 1_000_000:.1f}M"
                if abs(value) >= 1_000:
                    return f"{value / 1_000:.1f}K"
            return f"{value:,.{decimals}f}"

        output: Dict[str, Any] = {
            "type": "kpi",
            "value": primary_value,
            "formatted_value": f"{prefix}{format_value(primary_value)}{suffix}",
            "label": humanize(primary) if primary else "",
            "compare": None,
            "target": None,
            "status": None,
            "style": style_dict,
        }

        higher_is_better = bool(style_dict.get("higher_is_better", True))

        if compare and compare_value:
            delta = primary_value - compare_value
            delta_pct = (delta / compare_value * 100) if compare_value else 0
            output["compare"] = {
                "label": humanize(compare),
                "value": compare_value,
                "delta": delta,
                "delta_pct": round(delta_pct, 2),
                "mode": style_dict.get("compareMode", "pct"),
                "better": bool(delta > 0 if higher_is_better else delta < 0),
            }

        target_value = style_dict.get("target")
        if target_value not in (None, "", 0):
            target_value = float(target_value)
            pct = (primary_value / target_value * 100) if target_value else 0
            output["target"] = {
                "label": style_dict.get("targetLabel") or "Meta",
                "value": target_value,
                "pct": round(pct, 1),
            }

        good = style_dict.get("status_good")
        warn = style_dict.get("status_warn")
        if good is not None or warn is not None:
            good = float(good) if good not in (None, "") else 100
            warn = float(warn) if warn not in (None, "") else 80
            basis = output["target"]["pct"] if output["target"] else primary_value
            if higher_is_better:
                output["status"] = "good" if basis >= good else ("warn" if basis >= warn else "bad")
            else:
                output["status"] = "good" if basis <= good else ("warn" if basis <= warn else "bad")

        return output

    @staticmethod
    def _number(value) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0
