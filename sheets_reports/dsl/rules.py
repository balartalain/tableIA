"""
Reglas semánticas de un data_spec: lo que el JSON Schema no puede expresar (referencias entre
métricas, columnas repetidas, capacidades del widget...). Una clase por regla; cada pieza del
data_spec (dsl/parts) trae las suyas y el widget puede agregar las propias.

El orden en que se reportan los errores está declarado, no depende de dónde se agregue cada
regla a la lista: cada regla dice su etapa, su prioridad (bandas de abajo) y si corta la
validación. WidgetType.errors las ordena por (stage, priority); los empates conservan el
orden de rules().

Las reglas reciben el widget para consultar su data_spec (`widget.spec_cls`) y su nombre
(`widget.label`); nunca preguntan por un widget concreto.
"""
from enum import IntEnum
from typing import ClassVar

from sheets_reports.dsl.context import SheetContext


class Stage(IntEnum):
    PRE_SCHEMA = 0     # sobre el dict crudo, antes de jsonschema
    SEMANTIC = 1       # sobre DataSpec


# Bandas de prioridad (menor = antes).
BUSINESS = 0       # reglas de negocio con mensaje para el usuario
STRUCTURE = 100    # columnas, dimensiones, pivotes y capacidades del widget
METRICS = 200      # condiciones y métricas
REFERENCES = 300   # lo que apunta a métricas ya validadas


class Rule:
    stage: ClassVar[Stage] = Stage.SEMANTIC
    priority: ClassVar[int] = STRUCTURE
    # Si falla, se devuelven solo sus errores (y no se sigue validando).
    blocking: ClassVar[bool] = False

    def check(self, spec, widget, ctx: SheetContext) -> list[str]:
        """`spec` es el dict crudo en PRE_SCHEMA y un DataSpec en SEMANTIC."""
        raise NotImplementedError
