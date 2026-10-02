"""
SpecPart: una pieza del data_spec (una clave del JSON) con todo lo suyo: su JSON Schema, cómo
se lee y se escribe, sus reglas, sus mensajes de error, su paso en el pipeline de ejecución y
lo que aporta al manifiesto del editor y a la ficha de la IA.

Un DataSpec es una composición de piezas (dsl/spec.py). El resto del sistema recorre las
piezas en vez de nombrar claves: agregar un dato al data_spec es escribir una pieza (en un
widget de extensión, en su mismo archivo) y registrarla en SPEC_PARTS.
"""
from dataclasses import dataclass
from typing import Any, ClassVar

import pandas as pd

from sheets_reports.dsl.context import SheetContext
from sheets_reports.dsl.registry import Registry
from sheets_reports.dsl.rules import STRUCTURE, Rule, Stage
from sheets_reports.dsl.schema import field_enum

# Todas las piezas conocidas (las del core y las de las extensiones), por clave. Se recorre
# para avisar de una clave que el widget no admite y para la tool de la IA sin tipo fijado.
SPEC_PARTS: Registry[type["SpecPart"]] = Registry("Pieza del data_spec", instantiate=False)


@dataclass
class Rows:
    """Estado del pipeline de ejecución: las filas que siguen en juego."""
    df: pd.DataFrame
    has_others: bool = False   # el Top N agrupó el resto como «Otros»


class SpecPart:
    key: ClassVar[str]
    # Orden de prepare() en el pipeline (menor = antes).
    order: ClassVar[int] = 100
    # Si un widget no la tiene, la ficha de la IA lo dice ("sin having").
    describe_absent: ClassVar[bool] = False

    # --- schema y valores ---------------------------------------------------------------

    def schema(self, ctx: SheetContext, *, for_ai: bool = False) -> dict:
        raise NotImplementedError

    def parse(self, raw) -> Any:
        """Valor tipado desde el JSON (que ya pasó el schema)."""
        return raw

    def dump(self, value) -> Any:
        return value

    def default(self) -> Any:
        """Valor cuando la clave no viene."""
        return None

    @staticmethod
    def is_empty(raw) -> bool:
        return raw is None or raw == [] or raw == {}

    @classmethod
    def loose(cls) -> "SpecPart":
        """La pieza con los límites del lenguaje (no los de un widget): la tool de la IA sin
        tipo fijado usa esta."""
        return cls()

    # --- lo que aporta a otras piezas -----------------------------------------------------

    def names(self, value) -> list[str]:
        """Columnas o aliases que introduce (las etiquetas de la vista se reconcilian con esto)."""
        return []

    def sort_targets(self, value) -> list[str]:
        """Por qué se puede ordenar gracias a esta pieza."""
        return []

    # --- ejecución ----------------------------------------------------------------------

    def resolve(self, value, df: pd.DataFrame) -> Any:
        """Valor con lo relativo ("el último año") ya concreto, calculado sobre `df`."""
        return value

    def prepare(self, spec, rows: Rows, *, aggregates: bool) -> Rows:
        """Paso del pipeline antes del plan (filtrar filas, Top N...). `aggregates`: el plan
        trabaja con grupos."""
        return rows

    # --- validación ---------------------------------------------------------------------

    def rules(self) -> list[Rule]:
        return []

    def readable(self, error, path: str, ctx: SheetContext) -> str | None:
        """Mensaje legible para un error del schema en esta clave (None: el genérico)."""
        return None

    def absent_hint(self, widget) -> str:
        """Mensaje si la clave viene con valor y el widget no la tiene."""
        return f"{self.key}: «{widget.label}» no admite '{self.key}'."

    # --- manifiesto del editor y ficha de la IA -----------------------------------------

    def manifest(self) -> dict:
        """Lo que aporta a manifest()["data"] cuando el widget la tiene."""
        return {}

    @classmethod
    def absent_manifest(cls) -> dict:
        """Lo que aporta a manifest()["data"] cuando el widget NO la tiene."""
        return {}

    def describe(self) -> list[str]:
        """Lo que admite, para la ficha de la IA ("dimensions 1–3")."""
        return []

    def missing(self) -> list[str]:
        """Lo que NO admite aun teniéndola, para la ficha ("show_as")."""
        return []


# --- Piezas de columnas: lista de columnas de la hoja con cotas -----------------------------

def bounds_text(name: str, low: int, high: int) -> str:
    return f"{name} {low}" if low == high else f"{name} {low}–{high}"


class ColumnListPart(SpecPart):
    """Una lista de columnas de la hoja, sin repetir, entre `low` y `high`."""
    limit: ClassVar[int]

    def __init__(self, low: int = 0, high: int | None = None):
        self.low = low
        self.high = self.limit if high is None else high

    @classmethod
    def loose(cls):
        return cls(0, cls.limit)

    def schema(self, ctx, *, for_ai=False):
        schema = {"type": "array", "items": field_enum(ctx.fields), "maxItems": self.high}
        if self.low:
            schema["minItems"] = self.low
        return schema

    def parse(self, raw):
        return list(raw or [])

    def dump(self, value):
        return list(value)

    def default(self):
        return []

    def names(self, value):
        return list(value)

    def readable(self, error, path, ctx):
        if "[" in path and error.validator == "enum":
            return column_message(error, path, ctx)
        return None

    def manifest(self):
        return {self.key: [self.low, self.high]}

    @classmethod
    def absent_manifest(cls):
        return {cls.key: [0, 0]}

    def describe(self):
        return [bounds_text(self.key, self.low, self.high)]

    def rules(self):
        return [NoRepeatedColumn(self.key)]


def column_message(error, path: str, ctx: SheetContext) -> str:
    """Mensaje de una columna que no existe (o que no es numérica donde hace falta)."""
    value = error.instance
    if value in ctx.fields and not ctx.is_numeric(value):
        return (f"{path}: la columna '{value}' no es numérica; solo se puede usar con "
                f"agg 'count'/'count_distinct' o en condiciones de igualdad.")
    return f"{path}: la columna '{value}' no existe en la hoja."


class NoRepeatedColumn(Rule):
    def __init__(self, key: str):
        self.key = key

    def check(self, spec, widget, ctx):
        values = spec.get(self.key, [])
        return [f"{self.key}: no se puede repetir una columna."] if len(set(values)) < len(values) else []


# --- Regla del core: claves que el widget no admite -----------------------------------------

class UnsupportedPart(Rule):
    """Una clave conocida (de SPEC_PARTS) que el widget no tiene y viene con valor: se avisa
    con el mensaje de su pieza ("«Tarjeta KPI» no agrupa…") en vez de un error de schema."""
    stage, priority = Stage.PRE_SCHEMA, STRUCTURE

    def check(self, raw, widget, ctx):
        if not isinstance(raw, dict):
            return []
        own = widget.spec_cls.keys()
        return [
            part_cls.loose().absent_hint(widget)
            for key, part_cls in zip(SPEC_PARTS.keys(), SPEC_PARTS.values())
            if key not in own and not SpecPart.is_empty(raw.get(key))
        ]
