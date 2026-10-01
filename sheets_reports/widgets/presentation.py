"""Helpers de presentación compartidos por los compiladores de los widgets (formatos que
consume el frontend: ApexCharts para kpi/bar/line/donut, Tabulator para table)."""
import pandas as pd

from sheets_reports.dsl.values import is_number, to_python

PIVOT_FIELD_PREFIX = "__pivots"
TOTAL_FIELD_PREFIX = "__total"
TOTAL_LABEL = "Total general"
# Separa los valores de una clave de columna con varios pivotes ("2026" + "Ene"). Es un
# carácter de control: no aparece en los valores de una hoja.
KEY_SEPARATOR = "\x1f"


def humanize(name) -> str:
    text = str(name).replace("_", " ").strip()
    return text[:1].upper() + text[1:]


def pivot_field(pivot_value, metric: str) -> str:
    """Clave plana de la celda (fila, valor de pivote) en las filas de una tabla con pivote.
    Es una clave literal (no una ruta anidada): el frontend desactiva el separador de campos
    anidados de Tabulator, así los puntos dentro de un valor de pivote no rompen nada."""
    return f"{PIVOT_FIELD_PREFIX}.{pivot_value}.{metric}"


def cell_field(col_key: tuple, metric: str) -> str:
    """Clave de la celda (fila, columna) de una tabla dinámica; con un pivote coincide con
    pivot_field, así las preferencias guardadas por columna (formatos) se mantienen."""
    return pivot_field(KEY_SEPARATOR.join(str(v) for v in col_key), metric)


def total_field(metric: str) -> str:
    """Clave de la columna "Total general" de una métrica en una tabla con pivote."""
    return f"{TOTAL_FIELD_PREFIX}.{metric}"


def number(value):
    value = to_python(value)
    return value if is_number(value) else None


def column_values(df: pd.DataFrame, column: str) -> list:
    return [to_python(v) for v in df[column]]
