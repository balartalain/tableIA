"""Base de los gráficos con eje X y series (barras, líneas)."""
import dataclasses
import re
from dataclasses import dataclass, field

from sheets_reports.dsl.spec import DataSpec
from sheets_reports.dsl.values import is_number
from sheets_reports.engine.plans import PLANS, FlatResult, PivotChartResult
from sheets_reports.widgets.base import DataCapabilities, ViewOptions, WidgetType, percent_metrics
from sheets_reports.widgets.presentation import column_values

REFERENCE_KINDS = ["value", "avg", "max", "min"]
MAX_REFERENCE_LINES = 5
REFERENCE_COLOR = "#d97706"
_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


def clean_reference_line(raw) -> dict | None:
    """
    Línea de referencia normalizada, o None si no se puede dibujar:
      kind   -> "value" (un número fijo: una meta) o "avg"/"max"/"min" de los datos
      value  -> el número, solo con kind "value"
      series -> `as` de una métrica o, con pivote, un valor del pivote; None = todas las series
      label  -> texto de la etiqueta ("" = el de por defecto)
      color  -> "#rrggbb"
    """
    if not isinstance(raw, dict) or raw.get("kind") not in REFERENCE_KINDS:
        return None
    kind = raw["kind"]
    value = raw.get("value")
    if kind == "value" and not is_number(value):
        return None
    series = raw.get("series")
    color = raw.get("color")
    return {
        "kind": kind,
        "value": float(value) if kind == "value" else None,
        "series": str(series) if kind != "value" and series not in (None, "") else None,
        "label": str(raw.get("label") or "").strip(),
        "color": color if isinstance(color, str) and _HEX_COLOR.match(color) else REFERENCE_COLOR,
    }


def clean_reference_lines(raw) -> tuple:
    lines = [clean_reference_line(item) for item in raw] if isinstance(raw, list) else []
    return tuple(line for line in lines if line is not None)[:MAX_REFERENCE_LINES]


@dataclass(frozen=True)
class ChartOptions(ViewOptions):
    # Líneas de referencia (meta, promedio, máximo, mínimo): solo visual, nunca cambia el cálculo.
    reference_lines: tuple = field(default_factory=tuple)

    @classmethod
    def _request_fields(cls, data, previous):
        if "reference_lines" in data:
            lines = clean_reference_lines(data.get("reference_lines"))
        else:
            lines = previous.reference_lines if previous is not None else ()
        return {**super()._request_fields(data, previous), "reference_lines": lines}

    @classmethod
    def _view_fields(cls, view):
        return {**super()._view_fields(view), "reference_lines": clean_reference_lines(view.get("reference_lines"))}

    @classmethod
    def ai_properties(cls):
        return {"reference_lines": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["kind"],
                "properties": {
                    "kind": {"enum": REFERENCE_KINDS},
                    "value": {"type": "number"},
                    "series": {"type": "string"},
                    "label": {"type": "string"},
                },
            },
        }}

    def reconcile(self, spec: DataSpec):
        """Sin pivote, una línea sobre una métrica que ya no está se descarta (con pivote, la
        serie es un valor del pivote: se resuelve al dibujar)."""
        options = super().reconcile(spec)
        aliases = set(spec.aliases)
        lines = tuple(
            line for line in self.reference_lines
            if line["series"] is None or spec.pivots or line["series"] in aliases
        )
        return dataclasses.replace(options, reference_lines=lines)

    def view_fields(self):
        return {"reference_lines": [dict(line) for line in self.reference_lines]}


class ChartWidget(WidgetType[FlatResult | PivotChartResult, ChartOptions]):
    """Una dimensión en el eje X; sin pivote, una serie por métrica; con pivote, UNA métrica y
    una serie por cada valor del pivote."""
    capabilities = DataCapabilities(dimensions=(1, 1), pivots=(0, 1))
    options_cls = ChartOptions

    def plan(self, spec):
        # La forma del dato depende del spec: con o sin pivote.
        return PLANS.get("pivot_chart" if spec.pivots else "flat")

    def data_view(self, spec: DataSpec) -> dict:
        return {
            "x": spec.dimensions[0] if spec.dimensions else None,
            "seriesBy": spec.pivots[0] if spec.pivots else None,
            "metrics": spec.aliases,
        }

    def compile(self, result, options, spec):
        """{"series": [...], "categories": [...], "percent"?: [nombres de serie]}."""
        percent = percent_metrics(spec)
        if isinstance(result, PivotChartResult):
            categories = result.dimension_values
            series = [
                {"name": str(p), "data": [result.rows[d][p][result.metric] for d in categories]}
                for p in result.pivot_values
            ]
            # Con pivote, todas las series son porcentaje si la métrica lo es.
            percent_series = [s["name"] for s in series] if result.metric in percent else []
        elif isinstance(result, FlatResult):
            categories = column_values(result.rows, result.dimension)
            series = [
                {"name": options.label(m), "data": column_values(result.rows, m)}
                for m in spec.aliases
            ]
            percent_series = [options.label(m) for m in spec.aliases if m in percent]
        else:
            raise TypeError(f"{type(self).__name__} no sabe compilar {type(result).__name__}")
        compiled = {"series": series, "categories": [str(c) for c in categories]}
        references = self._references(options.reconcile(spec), spec, {s["name"] for s in series})
        if references:
            compiled["referenceLines"] = references
        if percent_series:
            compiled["percent"] = percent_series
        return compiled

    @staticmethod
    def _references(options, spec, names):
        """Líneas para el frontend, con `series` ya como el nombre de la serie dibujada (la
        etiqueta de la métrica sin pivote). Una serie que no está en el resultado (un valor del
        pivote que los filtros dejaron fuera) no dibuja nada."""
        references = []
        for line in options.reference_lines:
            series = line["series"]
            if series is not None and not spec.pivots:
                series = options.label(series)
            if series is not None and series not in names:
                continue
            references.append({**line, "series": series})
        return references
