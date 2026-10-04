from dataclasses import dataclass, field

import pandas as pd


@dataclass(frozen=True)
class SheetContext:
    """
    Lo que el DSL necesita saber de la hoja: sus columnas reales (de ahí salen los `enum` del
    JSON Schema), cuáles son numéricas y el gid de la pestaña que se está leyendo.
    `samples` son valores de ejemplo por columna, solo para el contexto de la IA.
    """
    source: str
    fields: tuple[str, ...]
    numeric_fields: frozenset[str]
    samples: dict = field(default_factory=dict, compare=False)

    @classmethod
    def from_dataframe(cls, df: pd.DataFrame, source: str, samples: dict | None = None) -> "SheetContext":
        return cls(
            source=str(source),
            fields=tuple(df.columns),
            numeric_fields=frozenset(df.select_dtypes(include="number").columns),
            samples=samples or {},
        )

    def is_numeric(self, column) -> bool:
        return column in self.numeric_fields

    @property
    def ordered_numeric_fields(self) -> list[str]:
        """Columnas numéricas en el orden de la hoja (para enums estables)."""
        return [f for f in self.fields if f in self.numeric_fields]

    def with_samples(self, samples: dict) -> "SheetContext":
        return SheetContext(self.source, self.fields, self.numeric_fields, samples)
