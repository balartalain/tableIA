"""Helpers de datos compartidos: conversión de tipos, orden cronológico y tabla ordenada.

"""
from __future__ import annotations

import math
import re
import unicodedata

import numpy as np
import pandas as pd


# ---------- conversión de valores ----------

def to_python(value):
    """Convierte escalares de numpy/pandas a tipos nativos serializables a JSON (NaN -> None)."""
    if value is None:
        return None
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if value is pd.NaT or value is pd.NA:
        return None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value


def to_key(value):
    """Valor de una columna de agrupación: como to_python pero un entero float → int (2026, no "2026.0")."""
    value = to_python(value)
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def column_formats(df: pd.DataFrame) -> dict[str, str]:
    """Formato de las columnas numéricas elegido en la fuente: {columna: "currency" | "percent"}
    (lo deja services/sheets.apply_column_config en `df.attrs`)."""
    return df.attrs.get("column_formats") or {}


# ---------- columna de sistema ----------

# Identifica la fila («id», sin mayúsculas): la de la hoja o una generada
# 1..n. Es de solo lectura —el editor la muestra pero no se puede cambiar ni
# excluir— y el «Conteo» cuenta filas distintas sobre ella.
ID_ALIASES = ("id", "_id")


def is_id_column(name) -> bool:
    """¿El nombre es el de la columna de sistema ID?"""
    return str(name or "").strip().lower() in ID_ALIASES


def find_id_column(columns) -> str | None:
    """La columna de sistema ID del frame, si existe (con su nombre tal cual)."""
    for column in columns:
        if is_id_column(column):
            return str(column)
    return None


def require_id_column(df: pd.DataFrame) -> str:
    """La columna de sistema ID del frame: el sistema la crea al cargar la hoja,
    así que siempre debe existir (el «Conteo» cuenta filas distintas sobre ella)."""
    id_column = find_id_column(df.columns)
    if id_column is None:
        raise ValueError(
            "Falta la columna de sistema ID (una por fila): el sistema la crea al "
            "cargar la hoja. Refresca la fuente.")
    return id_column


def sort_key(value):
    # Números antes que textos, para no comparar tipos distintos.
    return (0, value, "") if isinstance(value, (int, float)) else (1, 0, str(value))


def percent(part, whole):
    """part / whole * 100 redondeado a 2 decimales; None si no hay total contra el que dividir."""
    part, whole = to_python(part), to_python(whole)
    if part is None or not whole:
        return None
    return round(part / whole * 100, 2)


def is_number(value) -> bool:
    """Número de Python o de numpy (np.int64 no es `int`: una suma de una columna de enteros lo
    es); nunca un booleano."""
    return (isinstance(value, (int, float, np.integer, np.floating))
            and not isinstance(value, (bool, np.bool_)))


def series_dict(series: pd.Series) -> dict:
    """{clave de grupo: valor}; claves compuestas (varias columnas) como tuplas."""
    return {
        (tuple(to_key(p) for p in k) if isinstance(k, tuple) else to_key(k)): to_python(v)
        for k, v in series.items()
    }


# ---------- orden cronológico ----------

# Número de mes de sus nombres (español e inglés, completos y abreviados).
_MONTHS = [
    ("enero", "ene", "january", "jan"), ("febrero", "feb", "february"),
    ("marzo", "mar", "march"), ("abril", "abr", "april", "apr"),
    ("mayo", "may"), ("junio", "jun", "june"),
    ("julio", "jul", "july"), ("agosto", "ago", "august", "aug"),
    ("septiembre", "setiembre", "sep", "sept", "set", "september"),
    ("octubre", "oct", "october"),
    ("noviembre", "nov", "november"),
    ("diciembre", "dic", "december", "dec"),
]
MONTH_ORDER = {name: number for number, names in enumerate(_MONTHS, start=1) for name in names}


def _plain(text) -> str:
    """Texto en minúsculas, sin acentos ni punto final ("Sept." -> "sept")."""
    text = unicodedata.normalize("NFD", str(text).strip().lower())
    return "".join(c for c in text if unicodedata.category(c) != "Mn").rstrip(".")


def _natural_key(value):
    """Orden natural: los números dentro del texto se comparan como números ("2025-2" antes que "2025-10")."""
    return [(0, int(part), "") if part.isdigit() else (1, 0, part)
            for part in re.split(r"(\d+)", _plain(value)) if part]


def time_order(values: list) -> list | None:
    """Los valores de lo más antiguo a lo más reciente si son de tiempo (números, meses o
    fechas en texto, ISO o día primero); None si no lo son (categorías, nombres...)."""
    values = list(values)
    if not values:
        return values
    if all(is_number(v) for v in values):
        return sorted(values, key=sort_key)
    if all(_plain(v) in MONTH_ORDER for v in values):
        return sorted(values, key=lambda v: MONTH_ORDER[_plain(v)])
    dates = pd.to_datetime(pd.Series(values, dtype="string"), errors="coerce", dayfirst=True, format="mixed")
    if not dates.isna().any():
        return [values[i] for i in dates.argsort(kind="stable")]
    return None


def time_fields(df: pd.DataFrame) -> list[str]:
    """Columnas de tiempo (números, meses o fechas en texto; ver `time_order`), en el orden de
    la hoja: en ellas «el periodo más reciente / anterior / más antiguo» tiene sentido."""
    return [column for column in df.columns
            if time_order(list({to_key(v) for v in df[column].dropna()})) is not None
            and df[column].notna().any()]


def chronological(values: list) -> list:
    """De lo más antiguo a lo más reciente (`time_order`); si no son de tiempo, en orden natural."""
    ordered = time_order(values)
    return ordered if ordered is not None else sorted(values, key=_natural_key)


OTHERS_LABEL = "Otros"


def sorted_table(table: pd.DataFrame, sort) -> pd.DataFrame:
    """Tabla plana ordenada por una de sus columnas; los vacíos siempre al final."""
    return table.sort_values(
        sort.by, ascending=not sort.descending, na_position="last", kind="stable",
    ).reset_index(drop=True)