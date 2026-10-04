"""
Cada tipo de widget declara su `style_schema`, sus capacidades y su lógica de consulta.

Tres capas, sin DSL:
  1. `WidgetFields` / `WidgetStyle` (dataclasses planas) describen la consulta y la apariencia.
  2. `style_schema` (lista declarativa estática) dice a la UI qué controles dibujar.
  3. `process_query` delega la ejecución al `PipelineExecutor` (Filter → Aggregation/Pivot →
     Calculated → Window → Sort/Limit).
"""
from __future__ import annotations

from abc import ABC
from dataclasses import dataclass, field
from typing import Any, ClassVar, Dict, List, Optional

import pandas as pd

from sheets_reports.dsl.registry import Registry
from sheets_reports.engine.pipeline import PipelineExecutor
from sheets_reports.widgets.schemas import WidgetFields, WidgetForm, WidgetStyle

# Registry global de widgets
WIDGETS: Registry["BaseWidget"] = Registry("Tipo de widget")


@dataclass
class WidgetResult:
    """Salida de `process_query`: la data plana más la metadata de la consulta."""

    data: Any
    metadata: Dict[str, Any] = field(default_factory=dict)
    fields: Optional[WidgetFields] = None
    type: str = ""
    frame: Optional[pd.DataFrame] = None

    @classmethod
    def from_pipeline(cls, raw: Dict[str, Any]) -> "WidgetResult":
        metadata = raw.get("metadata") or {}
        return cls(
            data=raw.get("data"),
            metadata=metadata,
            fields=metadata.get("fields"),
            type=raw.get("type", ""),
        )

    @property
    def rows(self) -> pd.DataFrame:
        """DataFrame de trabajo (el frame crudo si el widget lo guardó, si no, el de `data`)."""
        if self.frame is not None:
            return self.frame
        data = self.data
        if isinstance(data, pd.DataFrame):
            return data
        if isinstance(data, list):
            return pd.DataFrame(data)
        if data is None:
            return pd.DataFrame()
        if isinstance(data, dict) and isinstance(data.get("values"), dict):
            return pd.DataFrame([data["values"]])
        if isinstance(data, dict):
            return pd.DataFrame([data])
        return pd.DataFrame([data])

    @property
    def columns(self) -> List[str]:
        return list(self.rows.columns)


class BaseWidget(ABC):
    """Widget base: declara estilo y ejecuta consultas. Sin specs ni piezas."""

    key: ClassVar[str]
    type_key: ClassVar[str]
    label: ClassVar[str]

    # Schema declarativo para el panel «Personalizar» (backend-driven).
    style_schema: ClassVar[List[Dict[str, Any]]] = []

    # Configuración por defecto del estilo.
    defaults: ClassVar[dict] = {}

    # Reglas del tipo, planas y declarativas (antes vivían en subclases de SpecPart).
    capabilities: ClassVar[dict] = {}
    max_per_dashboard: ClassVar[Optional[int]] = None
    board_filtered: ClassVar[bool] = True
    ai_enabled: ClassVar[bool] = True
    ai_doc: ClassVar[str] = ""
    ai_examples: ClassVar[List[tuple]] = []

    # ------------------------------------------------------------------ datos
    def process_query(self, df: pd.DataFrame, fields: WidgetFields) -> WidgetResult:
        """Pipeline secuencial de transformación sobre el DataFrame (o QuerySet)."""
        raw = PipelineExecutor().execute(df, fields, widget_type=self.type_key)
        return WidgetResult.from_pipeline(raw)

    def compile(
        self,
        result: WidgetResult,
        style: WidgetStyle,
        fields: Optional[WidgetFields] = None,
        metadata: Optional[dict] = None,
    ) -> dict:
        """Convierte el resultado del pipeline en el JSON que dibuja el frontend."""
        raise NotImplementedError

    # --------------------------------------------------------------- entrada
    def render(self, df: pd.DataFrame, form_data: dict) -> dict:
        """
        Punto de entrada único. Recibe el DataFrame y el form plano
        `{"fields": {...}, "style": {...}}` y devuelve `render_data` + `widget_form`,
        o `error` si la consulta falló (un widget roto nunca tumba el tablero).
        Los estilos se completan con los defaults del `style_schema`.
        """
        raw = form_data or {}
        style_values = raw.get("style") if isinstance(raw.get("style"), dict) else {}
        form = WidgetForm.from_dict({**raw, "style": {**self.style_defaults(), **style_values}})
        try:
            result = self.process_query(df, form.fields)
            render_data = self.compile(result, form.style, form.fields, result.metadata)
        except KeyError as exc:
            # La hoja cambió y ya no tiene una columna que el form usa.
            return {"error": f"La columna {exc} ya no existe en la hoja. Edita el widget.",
                    "widget_form": form.to_dict()}
        except Exception as exc:  # noqa: BLE001 - el error se informa al usuario
            return {"error": str(exc), "widget_form": form.to_dict()}
        return {"render_data": render_data, "widget_form": form.to_dict()}

    # ---------------------------------------------------------------- estilo
    def get_style_schema(self) -> List[Dict[str, Any]]:
        """El schema declarativo para el panel «Personalizar»."""
        return self.style_schema

    def style_defaults(self) -> dict:
        """Valores por defecto de los controles del `style_schema`."""
        return {c["key"]: c["default"] for c in self.style_schema if "default" in c}
