from dataclasses import dataclass, field, replace

import pandas as pd

from sheets_reports.engine.formulas import aggregated_fields, row_fields
from sheets_reports.utils.data import time_fields


@dataclass(frozen=True)
class SheetContext:
    """
    Lo que el motor y la validación necesitan saber de la hoja: sus columnas reales (de ahí salen los `enum` del
    JSON Schema), cuáles son numéricas, cuáles son de tiempo (años, meses, fechas: admiten
    «el periodo más reciente») y el gid de la pestaña que se está leyendo.
    `samples` son valores de ejemplo por columna, solo para el contexto de la IA.
    `aggregated_fields` son los campos calculados agregados de la fuente: no son columnas
    (no agrupan ni filtran), solo métricas con agregación «auto».
    `calculated` son todos los campos calculados de la fuente con lo que calculan
    ({nombre: {formula, kind: "row"|"aggregated", format}}), para el contexto de la IA.
    """
    source: str
    fields: tuple[str, ...]
    numeric_fields: frozenset[str]
    samples: dict = field(default_factory=dict, compare=False)
    time_fields: frozenset[str] = frozenset()
    aggregated_fields: frozenset[str] = frozenset()
    calculated: dict = field(default_factory=dict, compare=False)

    @classmethod
    def from_dataframe(cls, df: pd.DataFrame, source: str, samples: dict | None = None) -> "SheetContext":
        return cls(
            source=str(source),
            fields=tuple(df.columns),
            numeric_fields=frozenset(df.select_dtypes(include="number").columns),
            samples=samples or {},
            time_fields=frozenset(time_fields(df)),
            aggregated_fields=frozenset(aggregated_fields(df)),
            calculated={
                **{name: {"formula": text, "kind": "row", "format": "number"}
                   for name, text in row_fields(df).items()},
                **{name: {"formula": f.formula.text, "kind": "aggregated", "format": f.format}
                   for name, f in aggregated_fields(df).items()},
            },
        )

    def is_aggregated(self, name) -> bool:
        return name in self.aggregated_fields

    def is_numeric(self, column) -> bool:
        return column in self.numeric_fields

    def is_time(self, column) -> bool:
        return column in self.time_fields

    @property
    def ordered_numeric_fields(self) -> list[str]:
        """Columnas numéricas en el orden de la hoja (para enums estables)."""
        return [f for f in self.fields if f in self.numeric_fields]

    def with_samples(self, samples: dict) -> "SheetContext":
        return replace(self, samples=samples)
