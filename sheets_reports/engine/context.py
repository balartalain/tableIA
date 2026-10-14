from dataclasses import dataclass, field, replace

import pandas as pd

from sheets_reports.engine.formulas import aggregated_fields, row_fields
from sheets_reports.utils.data import date_fields, time_fields
from sheets_reports.utils.scales import effective_scales


@dataclass(frozen=True)
class SheetContext:
    """
    Lo que el motor y la validación necesitan saber de la hoja: sus columnas reales (de ahí salen los `enum` del
    JSON Schema), cuáles son numéricas, cuáles son de tiempo (años, meses, fechas: admiten
    «el periodo más reciente») y el gid de la pestaña que se está leyendo.
    `samples` son valores de ejemplo por columna, solo para el contexto de la IA.
    `date_fields` son las columnas de fechas completas: en ellas «este año» o «este mes» comparan
    el año o el mes de cada fecha.
    `submission_field` es, en una hoja de respuestas de formulario, la columna con la fecha de
    envío de cada respuesta: no describe los datos (solo para el contexto de la IA).
    `aggregated_fields` son los campos calculados agregados de la fuente: no son columnas
    (no agrupan ni filtran), solo métricas con agregación «auto».
    `calculated` son todos los campos calculados de la fuente con lo que calculan
    ({nombre: {formula, kind: "row"|"aggregated", format}}), para el contexto de la IA.
    `scales` es la escala de medida de cada columna (continua, discreta, ordinal, nominal o
    `none`), la guardada en la fuente o la detectada, con su perfil
    (`utils/scales.effective_scales`): la usan la matriz de correlación y el contexto de la IA.
    """
    source: str
    fields: tuple[str, ...]
    numeric_fields: frozenset[str]
    samples: dict = field(default_factory=dict, compare=False)
    time_fields: frozenset[str] = frozenset()
    date_fields: frozenset[str] = frozenset()
    submission_field: str | None = field(default=None, compare=False)
    aggregated_fields: frozenset[str] = frozenset()
    calculated: dict = field(default_factory=dict, compare=False)
    aliases: dict = field(default_factory=dict, compare=False)
    scales: dict = field(default_factory=dict, compare=False)

    def __post_init__(self):
        if not self.aliases and self.fields:
            default_aliases = {f"col_{i + 1}": field for i, field in enumerate(self.fields)}
            object.__setattr__(self, "aliases", default_aliases)

    @classmethod
    def from_dataframe(cls, df: pd.DataFrame, source: str, samples: dict | None = None) -> "SheetContext":
        attr_aliases = df.attrs.get("column_aliases") or {}
        aliases = {}
        for i, col in enumerate(df.columns):
            alias = attr_aliases.get(col) or f"col_{i + 1}"
            aliases[alias.lower()] = col
        dates = frozenset(date_fields(df))
        submission = df.attrs.get("submission_field")
        return cls(
            source=str(source),
            fields=tuple(df.columns),
            numeric_fields=frozenset(df.select_dtypes(include="number").columns),
            samples=samples or {},
            time_fields=frozenset(time_fields(df)),
            date_fields=dates,
            submission_field=submission,
            aggregated_fields=frozenset(aggregated_fields(df)),
            calculated={
                **{name: {"formula": text, "kind": "row", "format": "number"}
                   for name, text in row_fields(df).items()},
                **{name: {"formula": f.formula.text, "kind": "aggregated", "format": f.format}
                   for name, f in aggregated_fields(df).items()},
            },
            aliases=aliases,
            scales=effective_scales(df, df.attrs.get("column_scales"), date_columns=dates,
                                    submission=submission),
        )

    def alias_for(self, field: str) -> str:
        for alias, name in self.aliases.items():
            if name == field:
                return alias
        return field

    def field_for(self, alias_or_field: str) -> str:
        clean = alias_or_field.strip().strip("[]").lower()
        return self.aliases.get(clean, alias_or_field)

    def is_aggregated(self, name) -> bool:
        return name in self.aggregated_fields

    def is_numeric(self, column) -> bool:
        return column in self.numeric_fields

    def is_time(self, column) -> bool:
        return column in self.time_fields

    def is_date(self, column) -> bool:
        return column in self.date_fields

    def scale_of(self, column) -> str | None:
        """La escala de medida de la columna (None si no es una columna de la hoja)."""
        profile = self.scales.get(column)
        return profile["scale"] if profile else None

    def order_of(self, column) -> list | None:
        """El orden de los valores de una ordinal, de menor a mayor."""
        profile = self.scales.get(column)
        return profile.get("order") if profile else None

    @property
    def ordered_numeric_fields(self) -> list[str]:
        """Columnas numéricas en el orden de la hoja (para enums estables)."""
        return [f for f in self.fields if f in self.numeric_fields]

    def with_samples(self, samples: dict) -> "SheetContext":
        return replace(self, samples=samples)
