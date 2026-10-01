"""Orden cronológico de los valores de una columna (eje de una tendencia o de un gráfico de
líneas): de lo más antiguo a lo más reciente, aunque las filas de la hoja vengan desordenadas."""
import re
import unicodedata

import pandas as pd

from sheets_reports.dsl.values import is_number, sort_key

# Número de mes de sus nombres (español e inglés, completos y abreviados).
_MONTHS = [
    ("enero", "ene", "january", "jan"), ("febrero", "feb", "february"), ("marzo", "mar", "march"),
    ("abril", "abr", "april", "apr"), ("mayo", "may"), ("junio", "jun", "june"),
    ("julio", "jul", "july"), ("agosto", "ago", "august", "aug"),
    ("septiembre", "setiembre", "sep", "sept", "set", "september"), ("octubre", "oct", "october"),
    ("noviembre", "nov", "november"), ("diciembre", "dic", "december", "dec"),
]
MONTH_ORDER = {name: number for number, names in enumerate(_MONTHS, start=1) for name in names}


def _plain(text) -> str:
    """Texto en minúsculas, sin acentos ni punto final ("Sept." -> "sept")."""
    text = unicodedata.normalize("NFD", str(text).strip().lower())
    return "".join(c for c in text if unicodedata.category(c) != "Mn").rstrip(".")


def _natural_key(value):
    """Orden natural: los números dentro del texto se comparan como números
    ("2025-2" antes que "2025-10")."""
    return [(0, int(part), "") if part.isdigit() else (1, 0, part)
            for part in re.split(r"(\d+)", _plain(value)) if part]


def chronological(values: list) -> list:
    """
    `values` (distintos, sin vacíos) de lo más antiguo a lo más reciente: números de menor a
    mayor (años); nombres de mes del 1 al 12; fechas en texto por fecha (ISO o día primero);
    cualquier otro texto (periodos "2025-1", "2026-T1"...) en orden natural.
    """
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
    return sorted(values, key=_natural_key)
