"""
DataSpec: la consulta de un widget como objeto de valor tipado, compuesto por piezas
(dsl/parts). Se guarda como JSON en Widget.data_spec (to_dict / from_dict) y el código trabaja
siempre con el objeto.

Cada widget declara su clase de spec con las piezas que admite:

    data_spec = {"source": gid, <clave de cada pieza>: valor, ...}

La base solo tiene `filters`; las clases intermedias del core cubren las formas habituales
(GroupedSpec, ScalarSpec, RowsSpec) y un widget ajusta sus cotas con `with_parts`. Una
extensión que necesita un dato nuevo escribe su pieza y una subclase de DataSpec que la use.
"""
from types import MappingProxyType
from typing import Any, ClassVar

import pandas as pd

from sheets_reports.dsl.context import SheetContext
from sheets_reports.dsl.metrics import Metric
from sheets_reports.dsl.parts import (
    Columns,
    Dimensions,
    Filters,
    Having,
    Limit,
    Metrics,
    OrderBy,
    Pivots,
    SPEC_PARTS,
    Sort,
    SpecPart,
    TopN,
    TrendBy,
)
from sheets_reports.dsl.schema import MAX_COLUMNS, MAX_METRICS


class DataSpec:
    parts: ClassVar[tuple[SpecPart, ...]] = (Filters(),)

    def __init__(self, source: str = "", **values):
        missing = {p.key: p.default() for p in self.parts if p.key not in values}
        object.__setattr__(self, "source", str(source))
        object.__setattr__(self, "_values", MappingProxyType({**values, **missing}))

    # --- acceso -------------------------------------------------------------------------

    def __getattr__(self, key):
        values = self.__dict__.get("_values", {})
        if key in values:
            return values[key]
        raise AttributeError(f"{type(self).__name__} no tiene '{key}'")

    def __setattr__(self, key, value):
        raise AttributeError("DataSpec es inmutable")

    def __eq__(self, other):
        return type(self) is type(other) and (self.source, dict(self._values)) == (other.source, dict(other._values))

    def __repr__(self):
        return f"{type(self).__name__}(source={self.source!r}, {dict(self._values)!r})"

    def get(self, key: str, default=None) -> Any:
        return self._values.get(key, default)

    def has(self, key: str) -> bool:
        return key in self._values

    @classmethod
    def keys(cls) -> list[str]:
        return [p.key for p in cls.parts]

    @classmethod
    def part(cls, key: str) -> SpecPart | None:
        return next((p for p in cls.parts if p.key == key), None)

    def names(self) -> set[str]:
        """Columnas y aliases que nombra el spec (lo que pueden etiquetar las opciones de vista)."""
        return {name for p in self.parts for name in p.names(self._values[p.key])}

    def sort_targets(self) -> list[str]:
        return [t for p in self.parts for t in p.sort_targets(self._values[p.key])]

    @property
    def metrics(self) -> list[Metric]:
        return self.get("metrics", [])

    @property
    def aliases(self) -> list[str]:
        return [m.alias for m in self.metrics]

    def metric(self, alias: str) -> Metric | None:
        return next((m for m in self.metrics if m.alias == alias), None)

    # --- JSON ---------------------------------------------------------------------------

    @classmethod
    def schema(cls, ctx: SheetContext, *, for_ai: bool = False) -> dict:
        """JSON Schema de su data_spec: la unión de los de sus piezas. Con `for_ai` se omite
        `source` (el backend la completa) y las uniones van como anyOf."""
        properties = {p.key: p.schema(ctx, for_ai=for_ai) for p in cls.parts}
        required = cls.keys()
        if not for_ai:
            properties = {"source": {"const": ctx.source}, **properties}
            required = ["source", *required]
        return {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "additionalProperties": False,
            "required": required,
            "properties": properties,
        }

    @classmethod
    def normalize(cls, raw):
        """El dict que llega listo para validar: sin las claves de otras piezas que vienen
        vacías (el builder manda todas). Las ajenas CON valor se dejan: las reporta
        UnsupportedPart."""
        if not isinstance(raw, dict):
            return raw
        own = set(cls.keys())
        return {k: v for k, v in raw.items()
                if k == "source" or k in own or k not in SPEC_PARTS or not SpecPart.is_empty(v)}

    @classmethod
    def with_defaults(cls, raw: dict) -> dict:
        """Con el valor por defecto de sus claves que faltan o vienen en null (lo que el builder
        no manda, o lo que la IA omite cuando el tipo no venía fijado)."""
        out = dict(raw)
        for part in cls.parts:
            if out.get(part.key) is None:
                out[part.key] = part.dump(part.default())
        return out

    @classmethod
    def own(cls, raw: dict) -> dict:
        """Sin las claves de otras piezas (ya las reportó UnsupportedPart): el schema valida el
        resto, y rechaza cualquier clave desconocida."""
        own = set(cls.keys())
        return {k: v for k, v in raw.items() if k == "source" or k in own or k not in SPEC_PARTS}

    @classmethod
    def from_dict(cls, raw: dict) -> "DataSpec":
        """Desde un dict que ya pasó el schema (o uno guardado, que lo pasó al guardarse)."""
        values = {}
        for part in cls.parts:
            value = raw.get(part.key)
            values[part.key] = part.default() if value is None else part.parse(value)
        return cls(raw.get("source", ""), **values)

    def to_dict(self) -> dict:
        return {"source": self.source, **{p.key: p.dump(self._values[p.key]) for p in self.parts}}

    def resolved(self, df: pd.DataFrame) -> "DataSpec":
        """Copia con cada valor relativo convertido en su valor concreto, calculado UNA vez
        sobre `df` (la hoja con los filtros del tablero): "el último año" es el de la hoja, no
        el de cada grupo o punto de la tendencia."""
        return type(self)(self.source, **{p.key: p.resolve(self._values[p.key], df) for p in self.parts})


def union_schema(spec_classes, ctx: SheetContext, *, for_ai: bool = False) -> dict:
    """Schema que acepta el data_spec de cualquiera de `spec_classes` (la tool de la IA cuando
    el tipo de widget no viene fijado): cada clave con los límites del lenguaje y ninguna
    obligatoria; después se valida con el widget elegido, que completa lo que falte."""
    keys = {key for cls in spec_classes for key in cls.keys()}
    properties = {
        part_cls.key: part_cls.loose().schema(ctx, for_ai=for_ai)
        for part_cls in SPEC_PARTS if part_cls.key in keys
    }
    if not for_ai:
        properties = {"source": {"const": ctx.source}, **properties}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
    }


def with_parts(base: type[DataSpec], *parts: SpecPart, without: tuple[str, ...] = ()) -> tuple[SpecPart, ...]:
    """Las piezas de `base` con las de `parts` en lugar de las de su misma clave (las nuevas
    van al final) y sin las de `without`."""
    replacements = {p.key: p for p in parts}
    result = [replacements.pop(p.key, p) for p in base.parts if p.key not in without]
    return (*result, *replacements.values())


# --- Formas habituales del core ------------------------------------------------------------

class GroupedSpec(DataSpec):
    """Agrupa por una dimensión (y opcionalmente un pivote) y resume con métricas: gráficos y
    tablas dinámicas."""
    parts = (Dimensions(1, 1), Pivots(0, 1), Filters(), Metrics(1, MAX_METRICS),
             Having(), OrderBy(), TopN())
    dimensions: list[str]
    pivots: list[str]
    filters: list
    having: list
    sort: Sort | None
    limit: Limit | None


class ScalarSpec(DataSpec):
    """Uno o pocos números, sin agrupar (con su tendencia opcional): el KPI."""
    parts = (Filters(), Metrics(1, 4, types={"agg", "calc", "grouped"}), TrendBy())
    filters: list
    trend_by: str | None


class RowsSpec(DataSpec):
    """Las filas de la hoja tal cual, con las columnas elegidas: la tabla de datos."""
    parts = (Columns(1, MAX_COLUMNS), Filters(), OrderBy())
    columns: list[str]
    filters: list
    sort: Sort | None
