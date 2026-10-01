"""Conversión de valores de pandas/numpy a tipos nativos y helpers numéricos compartidos."""
import math

import numpy as np
import pandas as pd


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
    """Valor de una columna de agrupación (dimensión o pivote): como to_python, pero un entero
    que pandas leyó como float (columna con celdas vacías) vuelve a ser entero: 2026, no
    "2026.0" en etiquetas y cabeceras."""
    value = to_python(value)
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


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
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def series_dict(series: pd.Series) -> dict:
    """{clave de grupo: valor}; claves compuestas (varias columnas) como tuplas."""
    return {
        (tuple(to_key(p) for p in k) if isinstance(k, tuple) else to_key(k)): to_python(v)
        for k, v in series.items()
    }
