"""Agregaciones de una métrica `agg`: cada una sabe si necesita columna, si la columna debe
ser numérica, qué vale un grupo sin filas y cómo se calcula con pandas."""
from typing import ClassVar

import pandas as pd

from sheets_reports.dsl.registry import Registry

AGGREGATIONS: Registry["Aggregation"] = Registry("Agregación")


class Aggregation:
    key: ClassVar[str]
    # Método de pandas que resume una columna (ej. "mean").
    pandas_method: ClassVar[str] = ""
    needs_field: ClassVar[bool] = True
    numeric_only: ClassVar[bool] = True
    # Una combinación sin filas suma/cuenta 0; su promedio, mínimo, etc. no existe.
    empty_value: ClassVar = None

    def by_group(self, grouped, field: str | None) -> pd.Series:
        """Valor crudo por grupo de un DataFrameGroupBy."""
        return getattr(grouped[field], self.pandas_method)()

    def total(self, df: pd.DataFrame, field: str | None):
        return getattr(df[field], self.pandas_method)()


@AGGREGATIONS.register
class Sum(Aggregation):
    key, pandas_method, empty_value = "sum", "sum", 0


@AGGREGATIONS.register
class Avg(Aggregation):
    key, pandas_method = "avg", "mean"


@AGGREGATIONS.register
class Count(Aggregation):
    """Cuenta filas: no lleva columna."""
    key, needs_field, numeric_only, empty_value = "count", False, False, 0

    def by_group(self, grouped, field):
        return grouped.size()

    def total(self, df, field):
        return len(df)


@AGGREGATIONS.register
class CountDistinct(Aggregation):
    key, pandas_method, numeric_only, empty_value = "count_distinct", "nunique", False, 0


@AGGREGATIONS.register
class Min(Aggregation):
    key, pandas_method = "min", "min"


@AGGREGATIONS.register
class Max(Aggregation):
    key, pandas_method = "max", "max"


@AGGREGATIONS.register
class Median(Aggregation):
    key, pandas_method = "median", "median"
