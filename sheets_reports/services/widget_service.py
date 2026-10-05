"""
Casos de uso de los widgets de un tablero: crear, editar su `WidgetForm` y calcular.
Las vistas son adaptadores HTTP sobre este servicio. No hace I/O: recibe la hoja ya cargada.
"""
import json
import logging
from typing import Optional

import pandas as pd

from sheets_reports.engine.steps.filter import Condition, apply_filters, condition_errors, parse_conditions
from sheets_reports.engine.context import SheetContext
from sheets_reports.utils.validation import MAX_BOARD_IN_VALUES, SpecValidationError
from sheets_reports.engine import ResultTooLargeError
from sheets_reports.models import Widget, default_position
from sheets_reports.services.ai_spec import form_errors
from sheets_reports.widgets import WIDGETS
from sheets_reports.widgets.base import control_enabled
from sheets_reports.widgets.schemas import WidgetForm

logger = logging.getLogger(__name__)


def clean_position(value, fallback=None) -> dict:
    position = dict(fallback or default_position())
    if isinstance(value, dict):
        for key in ("x", "y", "w", "h"):
            if isinstance(value.get(key), (int, float)) and not isinstance(value.get(key), bool):
                position[key] = int(value[key])
    position["w"] = min(max(position["w"], 2), 12)
    position["x"] = min(max(position["x"], 0), 12)
    position["h"] = min(max(position["h"], 100), 3000)
    return position


class WidgetService:
    """Operaciones sobre los widgets de un tablero, sobre la hoja ya cacheada."""

    def __init__(self, dashboard, df: pd.DataFrame):
        self.dashboard = dashboard
        self.df = df
        self.ctx = SheetContext.from_dataframe(df, dashboard.sheet_gid)

    # ----------------------------------------------------------------- CRUD
    def create(self, widget_type: str, payload: dict) -> Widget:
        """Widget desde el builder o desde la IA. Lanza UnknownKeyError o SpecValidationError."""
        definition = WIDGETS.get(widget_type)
        self._check_singleton(definition)
        title = str(payload.get("title") or "").strip() or definition.label
        form = WidgetForm.from_dict({
            **payload,
            "fields": payload.get("fields") or {},
            "style": self._clean_style(widget_type, payload.get("style") or {}),
        })
        self._validate(widget_type, form, title)
        return Widget.objects.create(
            dashboard=self.dashboard,
            type=widget_type,
            title=title,
            position=clean_position(payload.get("position")),
            fields=form.fields.to_dict(),
            style=form.style.to_dict(),
            source_prompt=payload.get("source_prompt"),
        )

    def update(self, widget: Widget, payload: dict) -> Widget:
        """Edición manual: lo que no viene se conserva del widget. NUNCA llama a la IA."""
        if "type" in payload and payload["type"] != widget.type:
            self._check_singleton(WIDGETS.get(payload["type"]))
            widget.type = payload["type"]

        merged = {
            "fields": {**(widget.fields or {}), **(payload.get("fields") or {})},
            "style": self._clean_style(widget.type, {**(widget.style or {}), **(payload.get("style") or {})}),
        }
        form = WidgetForm.from_dict(merged)
        self._validate(widget.type, form, payload.get("title", widget.title))
        widget.fields = form.fields.to_dict()
        widget.style = form.style.to_dict()
        if "title" in payload:
            widget.title = str(payload["title"] or "").strip() or widget.title
        if "position" in payload:
            widget.position = clean_position(payload["position"], fallback=widget.position)
        widget.save()
        return widget

    def _clean_style(self, widget_type: str, style: dict) -> dict:
        """El estilo lo define el `style_schema` del tipo: una clave fuera de él se descarta, y
        también la de un control deshabilitado que lo pide (`clear_when_disabled`)."""
        style = style or {}
        controls = {c["key"]: c for c in (WIDGETS.get(widget_type).style_schema or [])}
        return {
            k: v for k, v in style.items()
            if k in controls and not (controls[k].get("clear_when_disabled")
                                      and not control_enabled(controls[k], style))
        }

    def _validate(self, widget_type: str, form: WidgetForm, title) -> None:
        """Mismas reglas que la IA: un form que no pasa no llega a guardarse."""
        data = {
            "widget_type": widget_type,
            "title": str(title or "").strip(),
            "fields": form.fields.to_dict(),
            "style": form.style.to_dict(),
        }
        errors = form_errors(data, self.ctx, widget_type)
        if errors:
            raise SpecValidationError(errors)

    def _check_singleton(self, definition) -> None:
        limit = getattr(definition, "max_per_dashboard", None)
        if limit is None:
            return
        if self.dashboard.widgets.filter(type=definition.type_key).count() >= limit:
            raise SpecValidationError([f"Solo se puede agregar un widget «{definition.label}» por tablero."])

    # ------------------------------------------------------------- filtros
    def parse_board_filters(self, raw: Optional[str]) -> tuple[list[Condition], list[str]]:
        """
        Filtros del tablero desde `?filters=[{"field", "op", "value" | "relative"}]`. Cada uno
        se valida con las mismas reglas que los `fields.filters` del widget; uno inválido (ej.
        una URL compartida con una columna que ya no está en la hoja) se ignora y se informa,
        sin tumbar el tablero. Lanza ValueError si el parámetro no es una lista JSON.
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

    # -------------------------------------------------------------- cálculo
    def render(self, widget: Widget, board_filters: Optional[list[Condition]] = None) -> dict:
        """{"data": ...} listo para dibujar, o {"error": ...}. Un error no tumba el tablero."""
        definition = widget.definition
        if definition is None:
            return {"error": f"El tipo de widget «{widget.type}» ya no existe. Edita el widget."}
        try:
            # Los filtros del tablero se aplican antes: definen el universo del widget (el
            # denominador de sus porcentajes), mientras que los del propio widget lo recortan.
            df = apply_filters(self.df, board_filters) if board_filters and definition.board_filtered else self.df
            rendered = definition.render(df, {"fields": widget.fields, "style": widget.style})
        except ResultTooLargeError as e:
            return {"error": str(e)}
        except KeyError as e:
            # La hoja cambió y ya no tiene una columna que el form usa.
            return {"error": f"La columna {e} ya no existe en la hoja. Edita el widget."}
        except Exception:
            logger.exception("Error renderizando el widget %s", widget.id)
            return {"error": "No se pudo calcular este widget."}

        if rendered.get("error"):
            return {"error": rendered["error"]}
        return {"data": rendered.get("render_data"), "form": rendered.get("widget_form")}
