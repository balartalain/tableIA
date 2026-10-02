import dataclasses
from dataclasses import dataclass

from sheets_reports.dsl.spec import DataSpec
from sheets_reports.widgets.base import WIDGETS
from sheets_reports.widgets.chart import ChartOptions, ChartWidget


@dataclass(frozen=True)
class BarOptions(ChartOptions):
    # Barras apiladas: solo visual, nunca cambia el cálculo.
    stacked: bool = False

    @classmethod
    def _request_fields(cls, data, previous):
        fallback = previous.stacked if previous is not None else False
        return {**super()._request_fields(data, previous), "stacked": bool(data.get("stacked", fallback))}

    @classmethod
    def _view_fields(cls, view):
        return {**super()._view_fields(view), "stacked": bool(view.get("stacked"))}

    @classmethod
    def ai_properties(cls):
        return {**super().ai_properties(), "stacked": {"type": "boolean"}}

    @classmethod
    def ai_required(cls):
        return ["stacked"]

    def reconcile(self, spec: DataSpec):
        # Apilar solo tiene sentido con series por pivote.
        return dataclasses.replace(super().reconcile(spec), stacked=self.stacked and bool(spec.pivots))

    def view_fields(self):
        return {**super().view_fields(), "stacked": self.stacked}


@WIDGETS.register
class BarWidget(ChartWidget):
    key = "bar"
    label = "Gráfico de Barras"
    options_cls = BarOptions

    def compile(self, result, options, spec):
        return {**super().compile(result, options, spec), "stacked": options.reconcile(spec).stacked}
