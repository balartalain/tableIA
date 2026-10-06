"""
Paso 1 del motor (`filter_rows`) y las condiciones sobre filas ({field, op, value | relative})
que lo componen: los filtros del widget, de una métrica y del tablero.

Cada operador es una estrategia registrada en FILTER_OPS que reúne su regla (qué valor lleva,
si exige columna numérica) y su implementación (la máscara de pandas). Cada valor relativo
("el año actual", "el periodo más reciente de la columna") es otra estrategia en RELATIVE_VALUES.
Nunca se evalúa texto: solo operaciones vectorizadas de pandas.
"""
import datetime
import operator
import unicodedata
from dataclasses import dataclass, replace
from typing import Any, ClassVar

import pandas as pd

from sheets_reports.engine.context import SheetContext
from sheets_reports.utils.data import is_number, sort_key, time_order, to_key
from sheets_reports.utils.registry import Registry
from sheets_reports.utils.validation import (
    MAX_FILTERS, MAX_IN_VALUES, SCALAR, enum_of, field_enum, schema_errors, unique,
)

FILTER_OPS: Registry["FilterOperator"] = Registry("Operador de filtro")
RELATIVE_VALUES: Registry["RelativeValue"] = Registry("Valor relativo")


# ---------------------------------------------------------------------------
# Valores relativos
# ---------------------------------------------------------------------------

class RelativeValue:
    """Valor de un filtro que se calcula al ejecutar: del reloj o de los datos de la columna."""
    key: ClassVar[str]
    # Sale de los valores de la columna (un periodo): solo vale en columnas de tiempo.
    data_based: ClassVar[bool] = False

    def resolve(self, series: pd.Series, today: datetime.date):
        raise NotImplementedError


class _ClockValue(RelativeValue):
    def resolve(self, series, today):
        return self.from_today(today)

    def from_today(self, today: datetime.date):
        raise NotImplementedError


@RELATIVE_VALUES.register
class CurrentYear(_ClockValue):
    key = "current_year"

    def from_today(self, today):
        return today.year


@RELATIVE_VALUES.register
class PreviousYear(_ClockValue):
    key = "previous_year"

    def from_today(self, today):
        return today.year - 1


@RELATIVE_VALUES.register
class CurrentMonth(_ClockValue):
    key = "current_month"

    def from_today(self, today):
        return today.month


class _DataValue(RelativeValue):
    """Un periodo tomado de los valores distintos de una columna de tiempo, de lo más antiguo a
    lo más reciente (años, meses Ene→Dic, fechas en texto; ver utils/data.time_order). None si
    no hay valores o la columna no es de tiempo (ej. categorías): la condición no deja filas."""
    data_based = True

    def resolve(self, series, today):
        values = time_order(sorted({to_key(v) for v in series.dropna()}, key=sort_key))
        return self.pick(values) if values else None

    def pick(self, values: list):
        raise NotImplementedError


@RELATIVE_VALUES.register
class LatestValue(_DataValue):
    """El periodo más reciente de la columna (ej. el último mes con datos)."""
    key = "latest"

    def pick(self, values):
        return values[-1]


@RELATIVE_VALUES.register
class PreviousValue(_DataValue):
    """El periodo anterior al más reciente (ej. el mes o año anterior en los datos)."""
    key = "previous"

    def pick(self, values):
        return values[-2] if len(values) > 1 else None


@RELATIVE_VALUES.register
class EarliestValue(_DataValue):
    """El periodo más antiguo de la columna."""
    key = "earliest"

    def pick(self, values):
        return values[0]


def resolve_relative(series: pd.Series, relative: str, today: datetime.date | None = None):
    """Valor concreto de un filtro relativo. None si la columna no tiene valores."""
    return RELATIVE_VALUES.get(relative).resolve(series, today or datetime.date.today())


# ---------------------------------------------------------------------------
# Operadores
# ---------------------------------------------------------------------------

def _coerce_value(series: pd.Series, value):
    """Adapta el valor del filtro al tipo de la columna (ej. "2026" que llega por URL contra
    una columna numérica, o 2026 contra una columna de texto)."""
    if pd.api.types.is_numeric_dtype(series):
        if isinstance(value, str):
            try:
                return float(value.replace(",", ""))
            except ValueError:
                return value
        return value
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (int, float)):
        return str(value)
    return value


def _comparable(series: pd.Series) -> pd.Series:
    if pd.api.types.is_numeric_dtype(series):
        return series
    return series.astype("string").str.strip()


def _text_key(value):
    # Orden para mostrar: números de menor a mayor, luego textos sin distinguir mayúsculas ni
    # acentos ("Árbol" antes que "Ropa").
    if is_number(value):
        return (0, value, "")
    plain = unicodedata.normalize("NFD", str(value).casefold())
    return (1, 0, "".join(c for c in plain if unicodedata.category(c) != "Mn"))


def distinct_values(series: pd.Series) -> list:
    """Valores distintos no vacíos de una columna, normalizados igual que los compara una
    condición (`_comparable` + `_coerce_value`): texto recortado y enteros sin ".0". Así una
    opción elegida de esta lista siempre coincide al filtrar con `in`/`eq`."""
    values = series.dropna()
    if pd.api.types.is_numeric_dtype(series):
        keys = {to_key(v) for v in values}
    else:
        text = _comparable(values)
        keys = {str(v) for v in text[text != ""]}
    return sorted(keys, key=_text_key)


def _blank(raw: pd.Series) -> pd.Series:
    return raw.isna() | (_comparable(raw).astype("string").str.strip() == "")


class FilterOperator:
    """Un operador de condición. `validate` recibe el dict ya validado por el schema."""
    key: ClassVar[str]

    def validate(self, cond: dict, ctx: SheetContext, path: str) -> list[str]:
        if "value" in cond and "relative" in cond:
            return [f"{path}: usa 'value' o 'relative', no ambos."]
        return self.check_value(cond, ctx, path)

    def check_value(self, cond: dict, ctx: SheetContext, path: str) -> list[str]:
        raise NotImplementedError

    def mask(self, raw: pd.Series, value) -> pd.Series:
        raise NotImplementedError


class _NoValue(FilterOperator):
    def check_value(self, cond, ctx, path):
        if "value" in cond or "relative" in cond:
            return [f"{path}: '{self.key}' no lleva valor."]
        return []


@FILTER_OPS.register
class IsEmpty(_NoValue):
    key = "is_empty"

    def mask(self, raw, value):
        return _blank(raw)


@FILTER_OPS.register
class NotEmpty(_NoValue):
    key = "not_empty"

    def mask(self, raw, value):
        return ~_blank(raw)


class _ListOperator(FilterOperator):
    def check_value(self, cond, ctx, path):
        value = cond.get("value")
        if "relative" in cond or not isinstance(value, list) or not value:
            return [f"{path}: '{self.key}' lleva una lista de valores en 'value'."]
        return []

    def _isin(self, raw, value) -> pd.Series:
        return _comparable(raw).isin([_coerce_value(raw, v) for v in value])


@FILTER_OPS.register
class In(_ListOperator):
    key = "in"

    def mask(self, raw, value):
        return self._isin(raw, value)


@FILTER_OPS.register
class NotIn(_ListOperator):
    key = "not_in"

    def mask(self, raw, value):
        return ~self._isin(raw, value)


@FILTER_OPS.register
class Between(FilterOperator):
    key = "between"

    def check_value(self, cond, ctx, path):
        field, value = cond["field"], cond.get("value")
        if not ctx.is_numeric(field):
            return [f"{path}: 'between' solo se usa con columnas numéricas y '{field}' no lo es."]
        if "relative" in cond or not isinstance(value, list) or len(value) != 2 or not all(map(is_number, value)):
            return [f"{path}: 'between' lleva una lista de dos números [desde, hasta] en 'value'."]
        return []

    def mask(self, raw, value):
        low, high = value
        return _comparable(raw).between(min(low, high), max(low, high))


@FILTER_OPS.register
class Contains(FilterOperator):
    key = "contains"

    def check_value(self, cond, ctx, path):
        value = cond.get("value")
        if "relative" in cond or not isinstance(value, str) or not value:
            return [f"{path}: 'contains' lleva un texto en 'value'."]
        return []

    def mask(self, raw, value):
        return _comparable(raw).astype("string").str.contains(str(value), case=False, regex=False)


class _Comparison(FilterOperator):
    """eq/ne y comparaciones de orden: un escalar o un valor relativo."""
    compare: ClassVar[Any]

    def check_value(self, cond, ctx, path):
        if "value" not in cond and "relative" not in cond:
            return [f"{path}: falta 'value' (o 'relative')."]
        if isinstance(cond.get("value"), list):
            return [f"{path}: '{self.key}' lleva un único valor; para varios usa 'in'."]
        relative = cond.get("relative")
        if relative in RELATIVE_VALUES and RELATIVE_VALUES.get(relative).data_based \
                and not ctx.is_time(cond["field"]):
            return [f"{path}: '{relative}' solo aplica a columnas de tiempo (años, meses, fechas); "
                    f"'{cond['field']}' no lo es: usa un valor concreto."]
        return []

    def mask(self, raw, value):
        if value is None:  # valor relativo sin datos (ej. no hay "penúltimo año")
            return pd.Series(False, index=raw.index)
        return type(self).compare(_comparable(raw), _coerce_value(raw, value))


class _OrderComparison(_Comparison):
    def check_value(self, cond, ctx, path):
        errors = super().check_value(cond, ctx, path)
        if errors:
            return errors
        field = cond["field"]
        if not ctx.is_numeric(field):
            return [f"{path}: la columna '{field}' no es numérica; '{self.key}' solo compara números."]
        if "value" in cond and not is_number(cond["value"]):
            return [f"{path}: '{self.key}' compara contra un número."]
        return []


@FILTER_OPS.register
class Eq(_Comparison):
    key, compare = "eq", operator.eq


@FILTER_OPS.register
class Ne(_Comparison):
    key, compare = "ne", operator.ne


@FILTER_OPS.register
class Lt(_OrderComparison):
    key, compare = "lt", operator.lt


@FILTER_OPS.register
class Lte(_OrderComparison):
    key, compare = "lte", operator.le


@FILTER_OPS.register
class Gt(_OrderComparison):
    key, compare = "gt", operator.gt


@FILTER_OPS.register
class Gte(_OrderComparison):
    key, compare = "gte", operator.ge


# ---------------------------------------------------------------------------
# Condición
# ---------------------------------------------------------------------------

def condition_schema(ctx: SheetContext, max_in_values: int = MAX_IN_VALUES) -> dict:
    """Una condición {field, op, value | relative}. Qué valor lleva cada operador lo decide el
    operador (FilterOperator.validate), con mensajes legibles."""
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["field", "op"],
        "properties": {
            "field": field_enum(ctx.fields),
            "op": enum_of(FILTER_OPS),
            "value": {"anyOf": [SCALAR, {"type": "array", "maxItems": max_in_values, "items": SCALAR}]},
            "relative": enum_of(RELATIVE_VALUES),
        },
    }


def conditions_schema(ctx: SheetContext, max_in_values: int = MAX_IN_VALUES) -> dict:
    return {"type": "array", "maxItems": MAX_FILTERS, "items": condition_schema(ctx, max_in_values)}


_MISSING = object()


@dataclass(frozen=True)
class Condition:
    field: str
    op: FilterOperator
    value: Any = _MISSING
    relative: RelativeValue | None = None

    @classmethod
    def from_dict(cls, raw: dict) -> "Condition":
        return cls(
            field=raw["field"],
            op=FILTER_OPS.get(raw["op"]),
            value=raw.get("value", _MISSING),
            relative=RELATIVE_VALUES.get(raw["relative"]) if "relative" in raw else None,
        )

    def to_dict(self) -> dict:
        out = {"field": self.field, "op": self.op.key}
        if self.value is not _MISSING:
            out["value"] = self.value
        if self.relative is not None:
            out["relative"] = self.relative.key
        return out

    def resolved(self, df: pd.DataFrame, today: datetime.date | None = None) -> "Condition":
        """Copia con el valor relativo convertido en su valor concreto, calculado sobre `df`."""
        if self.relative is None:
            return self
        value = self.relative.resolve(df[self.field], today or datetime.date.today())
        return replace(self, value=value, relative=None)

    def mask(self, df: pd.DataFrame) -> pd.Series:
        raw = df[self.field]
        if self.relative is not None:
            value = self.relative.resolve(raw, datetime.date.today())
        else:
            value = None if self.value is _MISSING else self.value
        return self.op.mask(raw, value)


def parse_conditions(raw: list[dict] | None) -> list[Condition]:
    return [Condition.from_dict(c) for c in raw or []]


def condition_errors(filters, ctx: SheetContext, path: str = "filters",
                     max_in_values: int = MAX_IN_VALUES) -> list[str]:
    """Valida una lista de condiciones (filtros del widget, de una métrica o del tablero)."""
    schema = {"type": "object", "properties": {"filters": conditions_schema(ctx, max_in_values)}}
    errors = [e.replace("filters", path, 1) for e in schema_errors(schema, {"filters": filters}, ctx)]
    if errors:
        return errors
    return conditions_errors(parse_conditions(filters), ctx, path)


def conditions_errors(conditions, ctx: SheetContext, path: str = "filters") -> list[str]:
    """Reglas de cada operador sobre condiciones ya parseadas (que pasaron el schema)."""
    errors = []
    for i, cond in enumerate(conditions):
        errors += cond.op.validate(cond.to_dict(), ctx, f"{path}[{i}]")
    return unique(errors)


def apply_filters(df: pd.DataFrame, conditions: list[Condition] | None) -> pd.DataFrame:
    mask = pd.Series(True, index=df.index)
    for cond in conditions or []:
        mask &= cond.mask(df).fillna(False).astype(bool)
    return df[mask]


def filter_rows(df: pd.DataFrame, filters: list[dict] | None, metadata: dict) -> pd.DataFrame:
    """Paso 1: las condiciones del widget recortan las FILAS que entran (los filtros propios
    de cada métrica se aplican al agregar)."""
    if filters:
        conditions = parse_conditions(filters)
        df = apply_filters(df, conditions)
        metadata["filters_applied"] = len(conditions)
    return df
