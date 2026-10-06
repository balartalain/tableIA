"""
Tarjeta KPI: consulta con `WidgetFields` (roles del número elegidos en el style: `primary`,
`compare`, `targetMetric`; tendencia con `trend_by`), apariencia con `WidgetStyle`.
"""
import logging
from typing import Any, ClassVar, Dict, List, Optional

import pandas as pd

from sheets_reports.utils.data import chronological, to_key
from sheets_reports.widgets.base import WIDGETS, BaseWidget, WidgetResult
from sheets_reports.widgets.presentation import metric_alias, metric_label
from sheets_reports.widgets.schemas import WidgetFields, WidgetStyle

logger = logging.getLogger(__name__)

# Grupos de la sparkline: más no se distinguen en una tarjeta.
MAX_TREND_POINTS = 60


def _chronological_keys(series: pd.Series) -> list:
    """Valores distintos de la columna de la tendencia, de lo más antiguo a lo más reciente
    (ver utils/data.chronological). Nunca en el orden de la hoja."""
    keys = list(pd.unique(series.dropna()))
    if pd.api.types.is_numeric_dtype(series):
        by_key = {to_key(k): k for k in keys}
        return [by_key[k] for k in chronological(list(by_key))]
    return chronological(keys)


def _display_key(value):
    """La clave en el JSON de dibujo: numpy → tipo nativo (el JSON no entiende np.int64)."""
    if hasattr(value, "item"):
        try:
            return value.item()
        except ValueError:
            return value
    return value


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
                              'trend_by': 'mes',
                              'metrics': [   {   'agg': 'sum',
                                                 'field': 'ventas',
                                                 'alias': 'ventas_anio',
                                                 'filters': [   {   'field': 'anio',
                                                                    'op': 'eq',
                                                                    'relative': 'current_year'}]}]},
                'style': {'prefix': 'RD$ '}}),
        (   'ventas del último mes y cuánto variaron frente al anterior',
            {   'widget_type': 'kpi',
                'title': 'Ventas del mes',
                'fields': {   'dimensions': [],
                              'trend_by': 'mes',
                              'metrics': [   {   'agg': 'sum',
                                                 'field': 'ventas',
                                                 'alias': 'ventas_ultimo_mes',
                                                 'filters': [   {   'field': 'mes',
                                                                    'op': 'eq',
                                                                    'relative': 'latest'}]},
                                             {   'agg': 'sum',
                                                 'field': 'ventas',
                                                 'alias': 'ventas_mes_anterior',
                                                 'label': 'Mes anterior',
                                                 'filters': [   {   'field': 'mes',
                                                                    'op': 'eq',
                                                                    'relative': 'previous'}]}]},
                'style': {'compare': 'ventas_mes_anterior', 'compareMode': 'pct'}}),
        (   'qué porcentaje de las ventas es de Hogar',
            {   'widget_type': 'kpi',
                'title': 'Participación de Hogar',
                'fields': {   'dimensions': [],
                              'trend_by': 'mes',
                              'metrics': [   {   'agg': 'sum',
                                                 'field': 'ventas',
                                                 'alias': 'ventas_hogar',
                                                 'filters': [   {   'field': 'categoria',
                                                                    'op': 'eq',
                                                                    'value': 'Hogar'}]},
                                             {   'agg': 'sum',
                                                 'field': 'ventas',
                                                 'alias': 'ventas_total'},
                                             {   'type': 'formula',
                                                 'alias': 'participacion',
                                                 'label': 'Participación',
                                                 'expression': 'ventas_hogar / ventas_total * 100'}]},
                'style': {'primary': 'participacion', 'suffix': ' %', 'decimals': 1}}),
        (   'cuántas órdenes entregamos y cómo van contra la meta',
            {   'widget_type': 'kpi',
                'title': 'Órdenes',
                'fields': {   'dimensions': [],
                              'metrics': [   {'agg': 'count', 'alias': 'ordenes'},
                                             {   'agg': 'sum',
                                                 'field': 'plan',
                                                 'alias': 'meta'}]},
                'style': {   'targetMetric': 'fixed',
                             'target': 1000,
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
        # Un solo número: sin ventanas. La participación es una fórmula (ver ai_examples).
        "windows": [],
        "trend": True,
    }

    style_schema: ClassVar[List[Dict[str, Any]]] = [
        {"key": "title", "label": "Título", "type": "string", "default": "Tarjeta KPI"},
        {"key": "decimals", "label": "Decimales", "type": "number", "default": 0},
        {"key": "abbreviate", "label": "Abreviar (1.2M)", "type": "boolean", "default": False},
        {"key": "prefix", "label": "Prefijo (ej. RD$)", "type": "string", "default": ""},
        {"key": "suffix", "label": "Sufijo (ej. uds.)", "type": "string", "default": ""},
        {"key": "primary", "label": "Número principal", "type": "choice", "options_from": "metrics", "default": "", "options": [
            {"value": "", "label": "Primera métrica"},
        ]},
        {"key": "compare", "label": "Comparar con", "type": "choice", "options_from": "metrics", "default": "", "options": [
            {"value": "", "label": "Sin comparación"},
        ]},
        {"key": "compareMode", "label": "Modo de comparación", "type": "choice", "default": "pct", "options": [
            {"value": "pct", "label": "Porcentaje"},
            {"value": "abs", "label": "Absoluto"},
        ]},
        {"key": "target", "label": "Meta (valor)", "type": "number"},
        {"key": "targetMetric", "label": "Valor objetivo", "type": "choice", "options_from": "metrics", "default": "", "options": [
            {"value": "", "label": "— elegir —"},
            {"value": "fixed", "label": "Valor fijo"},
        ]},
        {"key": "targetLabel", "label": "Nombre de la meta", "type": "string", "default": "Meta"},
        {"key": "statusBasis", "label": "Semáforo según", "type": "choice", "default": "", "options": [
            {"value": "", "label": "Automático"},
            {"value": "target_pct", "label": "% de la meta"},
            {"value": "value", "label": "Valor"},
        ]},
        {"key": "status_good", "label": "Umbral «bien» (%)", "type": "number"},
        {"key": "status_warn", "label": "Umbral «ajuste» (%)", "type": "number"},
        {"key": "higher_is_better", "label": "Más alto es mejor", "type": "boolean", "default": True},
    ]

    # ------------------------------------------------------------------ datos
    def process_query(self, df: pd.DataFrame, fields: WidgetFields) -> WidgetResult:
        """El número de siempre y, con `trend_by`, la serie de su mini tendencia."""
        result = super().process_query(df, fields)
        if fields.trend_by:
            try:
                trend = self._trend(df, fields, fields.trend_by)
            except KeyError:
                raise
            except Exception:  # noqa: BLE001 - la tarjeta se dibuja aunque falle la línea
                logger.exception("Tendencia del widget KPI (trend_by=%s)", fields.trend_by)
            else:
                if trend:
                    result.metadata["trend"] = trend
        return result

    def _trend(self, df: pd.DataFrame, fields: WidgetFields, trend_by: str) -> Optional[dict]:
        """{categories, series} con el KPI calculado por cada valor de `trend_by`, de lo más
        antiguo a lo más reciente (con muchos valores, quedan los más recientes)."""
        metrics = [self._without_eq_filter_on(m, trend_by) for m in (fields.metrics or [])]
        point_fields = WidgetFields(dimensions=[trend_by], metrics=metrics,
                                    filters=list(fields.filters or []))
        rows = super().process_query(df, point_fields).rows
        if rows.empty or trend_by not in rows.columns:
            return None
        keys = _chronological_keys(rows[trend_by])[-MAX_TREND_POINTS:]
        indexed = rows.set_index(trend_by)
        series: Dict[str, List[float]] = {}
        for metric in metrics:
            alias = metric_alias(metric)
            if alias and alias in indexed.columns:
                values = indexed[alias]
                series[alias] = [self._number(values.get(key, 0)) for key in keys]
        if not series:
            return None
        return {"categories": [_display_key(k) for k in keys], "series": series}

    @staticmethod
    def _without_eq_filter_on(metric: dict, column: str) -> dict:
        """Dentro de un punto de la serie, una condición eq de la métrica sobre la columna de
        la tendencia ya la fija el punto: sin quitarla vaciaría el valor."""
        if metric.get("type") == "formula" or not metric.get("filters"):
            return metric
        kept = [c for c in metric["filters"]
                if not (isinstance(c, dict) and c.get("field") == column and c.get("op") == "eq")]
        if len(kept) == len(metric["filters"]):
            return metric
        return {**metric, "filters": kept}

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
        aliases = [metric_alias(m) for m in metrics if metric_alias(m)]

        # Helper: buscar la métrica por alias
        def _metric_by_alias(alias: str) -> Optional[dict]:
            for m in metrics:
                if metric_alias(m) == alias:
                    return m
            return None

        # Roles del número: los elige el panel en «Tarjeta KPI», con respaldo en el orden
        # de las métricas (principal = la primera; sin elección de comparación, no compara).
        primary = style_dict.get("primary")
        if primary not in aliases:
            primary = aliases[0] if aliases else None
        chosen_compare = style_dict.get("compare")
        compare = chosen_compare if chosen_compare in aliases and chosen_compare != primary else None

        if df.empty:
            return {"type": "kpi", "value": 0, "formatted_value": "0", "label": "",
                    "compare": None, "target": None, "status": None, "style": style_dict}

        row = df.iloc[0]
        primary_value = self._number(row.get(primary, 0)) if primary else 0.0
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

        # Etiqueta del número principal: usa el «Nombre a mostrar» de la métrica si existe
        primary_label = metric_label(_metric_by_alias(primary)) if primary else ""

        output: Dict[str, Any] = {
            "type": "kpi",
            "value": primary_value,
            "formatted_value": f"{prefix}{format_value(primary_value)}{suffix}",
            "label": primary_label,
            "compare": None,
            "target": None,
            "status": None,
            "style": style_dict,
        }

        higher_is_better = bool(style_dict.get("higher_is_better", True))

        if compare and compare_value:
            delta = primary_value - compare_value
            delta_pct = (delta / compare_value * 100) if compare_value else 0
            # Etiqueta de la comparación: usa el «Nombre a mostrar» de la métrica si existe
            compare_label = metric_label(_metric_by_alias(compare))
            output["compare"] = {
                "label": compare_label,
                "value": compare_value,
                "delta": delta,
                "delta_pct": round(delta_pct, 2),
                "mode": style_dict.get("compareMode", "pct"),
                "better": bool(delta > 0 if higher_is_better else delta < 0),
            }

        # La meta la decide el selector: «— elegir —» no pinta barra aunque quede un valor
        # viejo guardado; «Valor fijo» usa el número; un alias usa la cifra de esa métrica.
        target_value = None
        target_metric = style_dict.get("targetMetric")
        if target_metric == "fixed":
            raw_target = style_dict.get("target")
            if raw_target not in (None, "", 0):
                target_value = float(raw_target)
        elif target_metric and target_metric in df.columns:
            target_value = self._number(row.get(target_metric))
        if target_value is not None:
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
            basis_mode = style_dict.get("statusBasis") or ""
            if basis_mode == "value":
                basis = primary_value
            elif basis_mode == "target_pct":
                basis = output["target"]["pct"] if output["target"] else None
            else:  # automático: % de la meta si hay meta, si no el valor
                basis = output["target"]["pct"] if output["target"] else primary_value
            if basis is not None:
                if higher_is_better:
                    output["status"] = "good" if basis >= good else ("warn" if basis >= warn else "bad")
                else:
                    output["status"] = "good" if basis <= good else ("warn" if basis <= warn else "bad")

        # Mini tendencia del número principal (si `trend_by` no produjo serie, no hay línea).
        trend = metadata.get("trend") if isinstance(metadata, dict) else None
        categories = (trend or {}).get("categories")
        series = ((trend or {}).get("series") or {}).get(primary) if primary else None
        if categories and series is not None and len(series) == len(categories):
            output["trend"] = {"categories": categories, "data": series}

        return output

    @staticmethod
    def _number(value) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0
