"""
Casos de uso de los widgets de un tablero: crear, editar el data_spec y calcular. Las vistas
son adaptadores HTTP sobre este servicio. No hace I/O: recibe la hoja ya cargada.
"""
import logging

import pandas as pd

from sheets_reports.dsl.conditions import Condition, apply_filters, condition_errors, parse_conditions
from sheets_reports.dsl.context import SheetContext
from sheets_reports.engine import ResultTooLargeError
from sheets_reports.models import Widget, default_position
from sheets_reports.widgets import WIDGETS

logger = logging.getLogger(__name__)

# Valor de cada clave del data_spec que el builder no manda.
_BUILDER_DEFAULTS = {
    "dimensions": [], "pivots": [], "columns": [], "filters": [], "metrics": [], "having": [],
    "sort": None, "limit": None, "trend_by": None,
}


def clean_position(value, fallback=None) -> dict:
    position = dict(fallback or default_position())
    if isinstance(value, dict):
        for key in ("x", "y", "w", "h"):
            if isinstance(value.get(key), (int, float)) and not isinstance(value.get(key), bool):
                position[key] = int(value[key])
    position["w"] = min(max(position["w"], 1), 12)
    position["x"] = min(max(position["x"], 0), 12)
    position["h"] = min(max(position["h"], 100), 3000)
    return position


class WidgetService:
    def __init__(self, dashboard, df: pd.DataFrame):
        self.dashboard = dashboard
        self.df = df
        self.ctx = SheetContext.from_dataframe(df, dashboard.sheet_gid)

    def _raw_data_spec(self, payload: dict) -> dict:
        """data_spec desde los controles del builder; sin una clave, su valor por defecto."""
        raw = {"source": self.dashboard.sheet_gid}
        for key, default in _BUILDER_DEFAULTS.items():
            value = payload.get(key, default)
            raw[key] = default if value is None and isinstance(default, list) else value
        return raw

    def create(self, widget_type: str, payload: dict) -> Widget:
        """Widget desde el builder. NUNCA llama a la IA. Lanza UnknownKeyError o
        SpecValidationError (mismas validaciones que el camino de IA)."""
        definition = WIDGETS.get(widget_type)
        spec = definition.validate(self._raw_data_spec(payload), self.ctx)
        return Widget.objects.create(
            dashboard=self.dashboard,
            type=widget_type,
            position=clean_position(payload.get("position")),
            data_spec=spec.to_dict(),
            view_spec=definition.build_view(spec, definition.options(payload)),
        )

    def update_spec(self, widget: Widget, payload: dict) -> Widget:
        """Edición manual desde el builder: valida, reconstruye view_spec (lo que no viene se
        conserva del widget) y guarda. NUNCA llama a la IA."""
        definition = widget.definition
        spec = definition.validate(self._raw_data_spec(payload), self.ctx)
        widget.data_spec = spec.to_dict()
        widget.view_spec = definition.build_view(spec, definition.options(payload, widget.view_spec or {}))
        widget.save()
        return widget

    def board_filters(self, filters: list) -> list[Condition]:
        """Filtros del tablero, con las mismas reglas que las condiciones del data_spec.
        Lanza ValueError con un mensaje legible."""
        errors = condition_errors(filters, self.ctx)
        if errors:
            raise ValueError(f"Filtro del tablero inválido: {errors[0]}")
        return parse_conditions(filters)

    def render(self, widget: Widget, board_filters: list[Condition] | None = None) -> dict:
        """{"data": ...} listo para dibujar, o {"error": ...}. Un error en un widget no tumba
        el tablero."""
        try:
            # Los filtros del tablero se aplican antes: definen el universo del widget (el
            # denominador de sus porcentajes), mientras que los del propio widget lo recortan.
            df = apply_filters(self.df, board_filters) if board_filters else self.df
            return {"data": widget.definition.render(widget.data_spec, widget.view_spec, df)}
        except ResultTooLargeError as e:
            return {"error": str(e)}
        except KeyError as e:
            # La hoja cambió y ya no tiene una columna que el spec usa.
            return {"error": f"La columna {e} ya no existe en la hoja. Edita el widget."}
        except Exception:
            logger.exception("Error renderizando el widget %s", widget.id)
            return {"error": "No se pudo calcular este widget."}
