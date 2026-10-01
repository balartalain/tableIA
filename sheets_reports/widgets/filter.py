"""
Caja de filtros del tablero: una barra fija arriba con un control por columna (ej. un selector
múltiple). Lo que el usuario elige NO se guarda en el widget: viaja en la URL como filtros del
tablero (`?filters=[{field, op, value}]`) y define el universo de todos los widgets.
"""
import dataclasses
from dataclasses import dataclass, field
from typing import ClassVar

from sheets_reports.dsl.registry import Registry
from sheets_reports.dsl.spec import DataSpec
from sheets_reports.engine.plans.column_values import ColumnValuesResult
from sheets_reports.widgets.base import WIDGETS, DataCapabilities, ViewOptions, WidgetType

FILTER_CONTROLS: Registry["FilterControl"] = Registry("Tipo de filtro")

MAX_FILTERS_PER_BOX = 10


class FilterControl:
    """Un tipo de control de la caja de filtros. Dice qué datos necesita para dibujarse; la
    condición que produce la arma el frontend con los operadores del DSL."""
    key: ClassVar[str]
    label: ClassVar[str]

    def payload(self, column: str, result: ColumnValuesResult) -> dict:
        raise NotImplementedError


@FILTER_CONTROLS.register
class MultiSelectControl(FilterControl):
    """Selector múltiple: los valores de la columna como opciones; produce `in`."""
    key = "multi_select"
    label = "Selector múltiple"

    def payload(self, column, result):
        out = {"options": result.values.get(column, [])}
        if column in result.truncated:
            out["truncated"] = True
        return out


DEFAULT_CONTROL = MultiSelectControl.key


@dataclass(frozen=True)
class FilterOptions(ViewOptions):
    controls: dict = field(default_factory=dict)  # {columna: tipo de control}

    @classmethod
    def _request_fields(cls, data, previous):
        fields = super()._request_fields(data, previous)
        if isinstance(data.get("controls"), dict):
            fields["controls"] = dict(data["controls"])
        else:
            fields["controls"] = dict(previous.controls) if previous is not None else {}
        return fields

    @classmethod
    def _view_fields(cls, view):
        return {**super()._view_fields(view), "controls": dict(view.get("controls") or {})}

    def reconcile(self, spec: DataSpec):
        """Un control por columna filtrada; un tipo desconocido pasa al selector múltiple."""
        controls = {
            column: self.controls.get(column) if self.controls.get(column) in FILTER_CONTROLS else DEFAULT_CONTROL
            for column in spec.columns
        }
        return dataclasses.replace(super().reconcile(spec), controls=controls)

    def view_fields(self):
        return {"controls": self.controls}


@WIDGETS.register
class FilterWidget(WidgetType[ColumnValuesResult, FilterOptions]):
    key = "filter"
    label = "Filtros"
    capabilities = DataCapabilities(
        dimensions=(0, 0), pivots=(0, 0), columns=(1, MAX_FILTERS_PER_BOX), metrics=(0, 0),
        metric_types=frozenset(), having=False, sort=False, limit=False,
    )
    options_cls = FilterOptions
    plan_key = "column_values"
    # Sus opciones son de toda la hoja: no se recortan con la selección del propio tablero.
    board_filtered = False
    max_per_dashboard = 1
    ai_enabled = False

    def default_title(self, spec: DataSpec, options: FilterOptions) -> str:
        return "Filtros"

    def compile(self, result: ColumnValuesResult, options: FilterOptions, spec: DataSpec) -> dict:
        """{"filters": [{"field", "label", "type", "options", "truncated"?}]} en el orden de
        `columns` (el orden en que el usuario los dejó en el panel)."""
        options = options.reconcile(spec)
        filters = []
        for column in spec.columns:
            control = FILTER_CONTROLS.get(options.controls[column])
            filters.append({
                "field": column,
                "label": options.labels.get(column) or str(column),
                "type": control.key,
                **control.payload(column, result),
            })
        return {"filters": filters}
