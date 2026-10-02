"""
Casos de uso de los widgets de un tablero: crear, editar el data_spec y calcular. Las vistas
son adaptadores HTTP sobre este servicio. No hace I/O: recibe la hoja ya cargada.
"""
import json
import logging

import pandas as pd

from sheets_reports.dsl.conditions import Condition, apply_filters, condition_errors, parse_conditions
from sheets_reports.dsl.context import SheetContext
from sheets_reports.dsl.errors import SpecValidationError
from sheets_reports.dsl.parts import SPEC_PARTS
from sheets_reports.dsl.schema import MAX_BOARD_IN_VALUES
from sheets_reports.engine import ResultTooLargeError
from sheets_reports.models import Widget, default_position
from sheets_reports.widgets import WIDGETS

logger = logging.getLogger(__name__)

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

    def _raw_data_spec(self, definition, payload: dict) -> dict:
        """data_spec desde el body del builder: las claves de las piezas conocidas (el resto
        son opciones de vista), con el valor por defecto de las que el builder no manda."""
        raw = {"source": self.dashboard.sheet_gid}
        raw.update({key: payload[key] for key in SPEC_PARTS.keys() if key in payload})
        return definition.spec_cls.with_defaults(raw)

    def create(self, widget_type: str, payload: dict) -> Widget:
        """Widget desde el builder. NUNCA llama a la IA. Lanza UnknownKeyError o
        SpecValidationError (mismas validaciones que el camino de IA)."""
        definition = WIDGETS.get(widget_type)
        limit = definition.max_per_dashboard
        if limit is not None and self.dashboard.widgets.filter(type=widget_type).count() >= limit:
            raise SpecValidationError([f"Solo se puede agregar un widget «{definition.label}» por tablero."])
        spec = definition.validate(self._raw_data_spec(definition, payload), self.ctx)
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
        spec = definition.validate(self._raw_data_spec(definition, payload), self.ctx)
        widget.data_spec = spec.to_dict()
        widget.view_spec = definition.build_view(spec, definition.options(payload, widget.view_spec or {}))
        widget.save()
        return widget

    def parse_board_filters(self, raw: str | None) -> tuple[list[Condition], list[str]]:
        """
        Filtros del tablero desde `?filters=[{"field", "op", "value" | "relative"}]` (los que
        elige el usuario en la caja de filtros). Cada uno se valida con las mismas reglas que
        las condiciones del data_spec; un `in` admite tantos valores como opciones da un
        selector. Un filtro inválido (ej. una URL compartida con una columna que ya no está en
        la hoja) se ignora y se informa, sin tumbar el tablero.
        Retorna (condiciones válidas, mensajes de los ignorados). Lanza ValueError si el
        parámetro no es una lista JSON.
        """
        if not raw:
            return [], []
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            raise ValueError("El parámetro 'filters' no es JSON válido.")
        if not isinstance(parsed, list):
            raise ValueError("El parámetro 'filters' debe ser una lista.")
        valid, ignored = [], []
        for item in parsed:
            errors = condition_errors([item], self.ctx, path="filtro", max_in_values=MAX_BOARD_IN_VALUES)
            if errors:
                ignored.append(f"Filtro del tablero ignorado: {errors[0]}")
            else:
                valid += parse_conditions([item])
        return valid, ignored

    def render(self, widget: Widget, board_filters: list[Condition] | None = None) -> dict:
        """{"data": ...} listo para dibujar, o {"error": ...}. Un error en un widget no tumba
        el tablero."""
        try:
            # Los filtros del tablero se aplican antes: definen el universo del widget (el
            # denominador de sus porcentajes), mientras que los del propio widget lo recortan.
            definition = widget.definition
            df = apply_filters(self.df, board_filters) if board_filters and definition.board_filtered else self.df
            return {"data": definition.render(widget.data_spec, widget.view_spec, df)}
        except ResultTooLargeError as e:
            return {"error": str(e)}
        except KeyError as e:
            # La hoja cambió y ya no tiene una columna que el spec usa.
            return {"error": f"La columna {e} ya no existe en la hoja. Edita el widget."}
        except Exception:
            logger.exception("Error renderizando el widget %s", widget.id)
            return {"error": "No se pudo calcular este widget."}
