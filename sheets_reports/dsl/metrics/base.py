"""
Métrica: la pieza polimórfica del DSL. Cada tipo (`agg`, `calc`, `grouped`, ...) es una clase
registrada en METRICS que declara su schema, sus reglas y cómo se calcula. El resto del
sistema nunca pregunta `if m["type"] == ...`.

La capa dsl/ no conoce widgets: qué tipos de métrica admite cada widget lo declara el widget
(DataCapabilities.metric_types).

Orden de evaluación ≠ orden de presentación: las métricas se evalúan en orden topológico
según `depends_on()` (evaluation_order). El orden de la lista solo decide el de las columnas,
las series y la leyenda, así que reordenar métricas nunca rompe un cálculo.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar

import pandas as pd

from sheets_reports.dsl.context import SheetContext
from sheets_reports.dsl.registry import Registry
from sheets_reports.dsl.schema import union

METRICS: Registry[type["Metric"]] = Registry("Tipo de métrica", instantiate=False)


class UnsupportedEvaluation(ValueError):
    """La métrica no se puede calcular en esa forma de resultado (ej. una métrica «por grupo»
    dentro de una tabla). La validación del widget lo impide; esto es una salvaguarda."""


@dataclass
class ScalarContext:
    """Evaluación de una métrica a un solo número (KPI y métricas internas)."""
    df: pd.DataFrame               # filas con los filtros del widget
    universe: pd.DataFrame         # filas sin ellos: denominador de los porcentajes
    values: dict = field(default_factory=dict)   # {as: valor} ya calculados


@dataclass
class FlatContext:
    """Evaluación por grupo de una columna (`dimension`), con una fila por cada valor de `keys`."""
    df: pd.DataFrame
    dimension: str
    keys: pd.Index
    columns: dict = field(default_factory=dict)  # {as: Series por grupo} ya calculadas
    totals: dict = field(default_factory=dict)   # {as: total general} ya calculados


class Metric(ABC):
    key: ClassVar[str]
    # Nombre legible del tipo, para los mensajes ("«por grupo»").
    label: ClassVar[str]
    # Se calcula a partir de los valores de otras métricas (no de las filas).
    derived: ClassVar[bool] = False

    alias: str

    # --- schema y parseo ----------------------------------------------------------------

    @classmethod
    @abstractmethod
    def schema(cls, ctx: SheetContext, *, for_ai: bool = False, nested: bool = False) -> dict:
        """JSON Schema de la métrica. `nested`: dentro de otra métrica (sin filtros propios)."""

    @classmethod
    @abstractmethod
    def from_dict(cls, raw: dict) -> "Metric":
        """Construye la métrica desde un dict que ya pasó el schema."""

    @abstractmethod
    def to_dict(self) -> dict: ...

    # --- reglas -------------------------------------------------------------------------

    def validate(self, ctx: SheetContext, scope: dict[str, "Metric"], path: str) -> list[str]:
        """Reglas propias. `scope` son las métricas del mismo nivel, por alias."""
        return []

    def depends_on(self) -> set[str]:
        return set()

    # --- presentación -------------------------------------------------------------------

    def is_percent(self) -> bool:
        return False

    def is_numeric(self) -> bool:
        """False si su valor es un grupo ({label, value}) en vez de un número."""
        return True

    def supports_trend(self) -> bool:
        return self.is_numeric()

    # --- preparación --------------------------------------------------------------------

    def resolved(self, df: pd.DataFrame) -> "Metric":
        """Copia con los valores relativos de sus condiciones resueltos sobre `df`."""
        return self

    def without_eq_filter_on(self, column: str) -> "Metric":
        """Copia sin sus condiciones eq sobre `column` (ver ScalarPlan: tendencia)."""
        return self

    # --- ejecución ----------------------------------------------------------------------

    show_as: ClassVar[str] = "value"
    empty_value: ClassVar[Any] = None

    @abstractmethod
    def scalar(self, ev: ScalarContext):
        """Valor de la métrica sobre todas las filas de `ev.df` (KPI)."""

    def flat(self, fc: FlatContext) -> tuple[pd.Series, Any]:
        """(valor por grupo de `fc.dimension`, total general)."""
        raise UnsupportedEvaluation(f"La métrica «{self.alias}» no se puede calcular por grupo.")

    def aggregate(self, df: pd.DataFrame, by) -> pd.Series:
        """Valor crudo por grupo de las columnas `by` (antes de `show_as`)."""
        raise UnsupportedEvaluation(f"La métrica «{self.alias}» no se puede calcular por celda.")

    def grand_total(self, df: pd.DataFrame):
        raise UnsupportedEvaluation(f"La métrica «{self.alias}» no se puede calcular por celda.")

    def derive(self, values: dict):
        """Solo métricas `derived`: su valor a partir de {as: valor} de las demás."""
        raise UnsupportedEvaluation(f"La métrica «{self.alias}» no se deriva de otras.")


def parse_metric(raw: dict) -> Metric:
    return METRICS.get(raw["type"]).from_dict(raw)


def metric_union(ctx: SheetContext, *, only=None, nested: bool = False, for_ai: bool = False) -> dict:
    """Unión discriminada de los tipos de métrica registrados (o de los de `only`)."""
    return union({
        cls.key: cls.schema(ctx, for_ai=for_ai, nested=nested)
        for cls in METRICS if only is None or cls.key in only
    }, for_ai=for_ai)


class MetricCycleError(ValueError):
    def __init__(self, aliases: list[str]):
        self.aliases = aliases
        super().__init__(f"Ciclo entre las métricas: {', '.join(aliases)}")


def evaluation_order(metrics: list[Metric]) -> list[Metric]:
    """Las métricas en un orden en que cada una va después de aquellas de las que depende.
    Estable: sin dependencias, conserva el orden de la lista. Las referencias a métricas que no
    están en `metrics` se ignoran (ej. la tendencia evalúa un subconjunto)."""
    present = {m.alias for m in metrics}
    done: set[str] = set()
    ordered: list[Metric] = []
    pending = list(metrics)
    while pending:
        ready = [m for m in pending if (m.depends_on() & present) <= done]
        if not ready:
            raise MetricCycleError([m.alias for m in pending])
        for m in ready:
            ordered.append(m)
            done.add(m.alias)
        pending = [m for m in pending if m.alias not in done]
    return ordered


def metrics_errors(metrics: list[Metric], ctx: SheetContext, path: str) -> list[str]:
    """Reglas de una lista de métricas del mismo nivel: alias únicos, las reglas de cada métrica
    y que no haya ciclos entre los cálculos."""
    errors: list[str] = []
    scope = {m.alias: m for m in metrics}
    seen: set[str] = set()
    for i, m in enumerate(metrics):
        where = f"{path}[{i}]"
        if m.alias in seen:
            errors.append(f"{where}.as: el nombre '{m.alias}' está repetido.")
        seen.add(m.alias)
        errors += m.validate(ctx, scope, where)
    if not errors:
        try:
            evaluation_order(metrics)
        except MetricCycleError as e:
            errors.append(f"{path}: las métricas calculadas {', '.join(e.aliases)} dependen unas de "
                          f"otras en círculo.")
    return errors
