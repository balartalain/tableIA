import dataclasses
from dataclasses import dataclass
from typing import Any

from sheets_reports.dsl.rules import REFERENCES, Rule
from sheets_reports.dsl.spec import DataSpec
from sheets_reports.dsl.values import is_number
from sheets_reports.engine.plans import ScalarResult
from sheets_reports.widgets.base import WIDGETS, DataCapabilities, ViewOptions, WidgetType, percent_metrics
from sheets_reports.widgets.presentation import number

STATUS_BASES = ["target_pct", "value"]
COMPARE_MODES = ["pct", "abs"]
ROLE_KEYS = ("primary", "compare", "compare_mode", "target", "higher_is_better", "status")


@dataclass(frozen=True)
class KpiOptions(ViewOptions):
    """
    Roles de las métricas de un KPI (solo presentación; nunca cambia el cálculo):
      primary          -> la métrica grande (por defecto, la primera)
      compare          -> métrica contra la que se muestra la variación ▲/▼
      compare_mode     -> "pct" (variación %) o "abs" (diferencia); None sin `compare`
      target           -> meta: `as` de una métrica o un número (barra de progreso)
      higher_is_better -> si subir es bueno (colores de la variación y del semáforo)
      status           -> semáforo {"basis": "target_pct"|"value", "good": n, "warn": n}
    """
    primary: str | None = None
    compare: str | None = None
    compare_mode: str | None = None
    target: Any = None
    higher_is_better: bool = True
    status: dict | None = None

    @classmethod
    def _roles(cls, source: dict) -> dict:
        return {
            "primary": source.get("primary"),
            "compare": source.get("compare"),
            "compare_mode": source.get("compare_mode"),
            "target": source.get("target"),
            "higher_is_better": source.get("higher_is_better", True) is not False,
            "status": source.get("status"),
        }

    @classmethod
    def _request_fields(cls, data, previous):
        fields = super()._request_fields(data, previous)
        if isinstance(data.get("kpi"), dict):
            fields.update(cls._roles(data["kpi"]))
        elif previous is not None:
            fields.update({k: getattr(previous, k) for k in ROLE_KEYS})
        return fields

    @classmethod
    def _view_fields(cls, view):
        return {**super()._view_fields(view), **cls._roles(view)}

    @classmethod
    def ai_properties(cls):
        return {"kpi": {
            "type": "object",
            "properties": {
                "primary": {"type": "string"},
                "compare": {"type": "string"},
                "compare_mode": {"enum": COMPARE_MODES},
                "target_metric": {"type": "string"},
                "target_value": {"type": "number"},
                "higher_is_better": {"type": "boolean"},
            },
        }}

    def reconcile(self, spec: DataSpec):
        """Las referencias a métricas que no existen (o no son números) se descartan."""
        options = super().reconcile(spec)
        metrics = {m.alias: m for m in spec.metrics}
        names = list(metrics)
        primary = self.primary if self.primary in metrics else names[0]
        others = [n for n in names if metrics[n].is_numeric() and n != primary]
        compare = self.compare if self.compare in others else None
        target = self.target
        if not (is_number(target) or target in others):
            target = None
        status = self.status
        valid_status = (
            isinstance(status, dict) and status.get("basis") in STATUS_BASES
            and all(is_number(status.get(k)) for k in ("good", "warn"))
            and (status["basis"] != "target_pct" or target is not None)
            and metrics[primary].is_numeric()
        )
        return dataclasses.replace(
            options,
            primary=primary,
            compare=compare,
            compare_mode=(self.compare_mode if self.compare_mode in COMPARE_MODES else "pct") if compare else None,
            target=target,
            higher_is_better=self.higher_is_better is not False,
            status={"basis": status["basis"], "good": status["good"], "warn": status["warn"]} if valid_status else None,
        )

    def view_fields(self):
        return {k: getattr(self, k) for k in ROLE_KEYS}


class TrendNeedsTrendableMetric(Rule):
    """La tendencia sigue a la métrica principal: alguna métrica tiene que poder tenerla (las
    que devuelven un grupo, como top/bottom, no la tienen)."""
    priority = REFERENCES

    def check(self, spec, widget, ctx):
        if spec.trend_by and not any(m.supports_trend() for m in spec.metrics):
            return ["trend_by: ninguna métrica tiene tendencia (las que devuelven un grupo, "
                    "como top/bottom, no la tienen)."]
        return []


def _status(value, status: dict, higher_is_better: bool):
    """Semáforo: "good" / "warn" / "bad" según los umbrales (invertidos si menos es mejor)."""
    if value is None:
        return None
    if higher_is_better:
        return "good" if value >= status["good"] else "warn" if value >= status["warn"] else "bad"
    return "good" if value <= status["good"] else "warn" if value <= status["warn"] else "bad"


@WIDGETS.register
class KpiWidget(WidgetType[ScalarResult, KpiOptions]):
    key = "kpi"
    label = "Tarjeta KPI"
    capabilities = DataCapabilities(
        dimensions=(0, 0), pivots=(0, 0), metrics=(1, 4),
        metric_types=frozenset({"agg", "calc", "grouped"}),
        having=False, sort=False, limit=False, trend=True,
    )
    options_cls = KpiOptions
    plan_key = "scalar"

    def rules(self):
        return [*super().rules(), TrendNeedsTrendableMetric()]

    def compile(self, result: ScalarResult, options: KpiOptions, spec: DataSpec) -> dict:
        """
        {"value", "label", "percent"?, "text"? (grupo de un top/bottom),
         "compare"?: {"label", "value", "mode", "delta", "delta_pct", "better"},
         "target"?: {"label", "value", "pct"}, "status"?: "good"|"warn"|"bad",
         "trend"?: {"categories", "data"}}
        """
        options = options.reconcile(spec)
        primary = options.primary
        raw = result.values.get(primary)
        compiled = {"label": options.label(primary)}
        if isinstance(raw, dict):
            compiled["text"] = str(raw["label"])
            raw = raw["value"]
        value = number(raw)
        compiled["value"] = value
        if primary in percent_metrics(spec):
            compiled["percent"] = True
        higher_is_better = options.higher_is_better

        if options.compare:
            other = number(result.values.get(options.compare))
            delta = value - other if value is not None and other is not None else None
            compiled["compare"] = {
                "label": options.label(options.compare),
                "value": other,
                "mode": options.compare_mode,
                "delta": round(delta, 2) if delta is not None else None,
                "delta_pct": round(delta / abs(other) * 100, 2) if delta is not None and other else None,
                "better": None if not delta else (delta > 0) == higher_is_better,
            }

        target_pct = None
        if options.target is not None:
            target = options.target
            goal = number(result.values.get(target)) if isinstance(target, str) else number(target)
            target_pct = round(value / goal * 100, 2) if value is not None and goal else None
            compiled["target"] = {
                "label": options.label(target) if isinstance(target, str) else "Meta",
                "value": goal,
                "pct": target_pct,
            }

        if options.status:
            basis = target_pct if options.status["basis"] == "target_pct" else value
            compiled["status"] = _status(basis, options.status, higher_is_better)

        trend = result.trend
        if trend and primary in trend.series:
            compiled["trend"] = {
                "categories": [str(c) for c in trend.categories],
                "data": [number(v) for v in trend.series[primary]],
            }
        return compiled
