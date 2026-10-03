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


@dataclass(frozen=True)
class PanelColumns:
    """Columnas de la hoja que ofrecen los selects del panel del editor. Vacías cuando el
    manifiesto se arma sin leer la hoja (al cargar la página); /schema/ lo devuelve con ellas."""
    all: tuple[str, ...] = ()
    numeric: tuple[str, ...] = ()
    dimension: tuple[str, ...] = ()


def choices(values, labels: dict | None = None) -> list[dict]:
    """Opciones de un select del panel, ya resueltas: [{value, label}]."""
    labels = labels or {}
    return [{"value": v, "label": labels.get(v, str(v))} for v in values]


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

    # --- panel del editor ---------------------------------------------------------------
    # Cómo se edita en el panel: el frontend recorre manifest()["parts"] y monta el componente
    # de cada `ui` (partials/panel/part.html). Sin `ui`, la pieza no tiene control en el panel.

    ui: ClassVar[str | None] = None
    panel_order: ClassVar[int] = 100   # menor = más arriba en el panel
    label: ClassVar[str] = ""
    hint: ClassVar[str] = ""
    # Piezas seguidas con el mismo grupo se dibujan juntas bajo su título: {key, label, hint}.
    group: ClassVar[dict | None] = None

    def panel(self, columns: PanelColumns) -> dict | None:
        """{key, ui, label, hint, group, ...lo propio de su ui}, con las opciones de cada
        select ya resueltas. None si la pieza no tiene control en el panel."""
        if self.ui is None:
            return None
        return {"key": self.key, "ui": self.ui, "label": self.label, "hint": self.hint,
                "group": self.group, **self.panel_fields(columns)}

    def panel_fields(self, columns: PanelColumns) -> dict:
        """Lo propio de su `ui` (ej. item_fields de un field-group)."""
        return {}


# --- Piezas de columnas: lista de columnas de la hoja con cotas -----------------------------

def bounds_text(name: str, low: int, high: int) -> str:
    return f"{name} {low}" if low == high else f"{name} {low}–{high}"


class ColumnListPart(SpecPart):
    """Una lista de columnas de la hoja, sin repetir, entre `low` y `high`."""
    limit: ClassVar[int]

    ui = "column-list"
    # De qué columnas de PanelColumns se elige.
    options_from: ClassVar[str] = "all"
    add_label: ClassVar[str] = "Agregar columna"
    # Opción vacía del select; `empty_selectable`: se puede elegir (si no, solo se ve mientras
    # no hay columna elegida).
    empty_label: ClassVar[str] = "Elige una columna…"
    empty_selectable: ClassVar[bool] = False
    # Listas cuyas columnas ya elegidas no se ofrecen (incluida la propia: sin repetidos).
    excludes: ClassVar[tuple[str, ...]] = ()
    sortable: ClassVar[bool] = False   # se reordena arrastrando
    allow_all: ClassVar[bool] = False  # atajo «Usar todas»

    def __init__(self, low: int = 0, high: int | None = None, **panel):
        """`panel`: textos del panel propios del widget (label, hint, add_label, allow_all...)."""
        self.low = low
        self.high = self.limit if high is None else high
        for name, value in panel.items():
            if not hasattr(type(self), name):
                raise TypeError(f"{type(self).__name__}: opción de panel desconocida '{name}'")
            setattr(self, name, value)

    def panel_fields(self, columns):
        return {
            "min": self.low, "max": self.high,
            "options": choices(getattr(columns, self.options_from)),
            "add_label": self.add_label,
            "empty_label": self.empty_label, "empty_selectable": self.empty_selectable,
            "excludes": list(self.excludes), "sortable": self.sortable, "allow_all": self.allow_all,
        }

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
