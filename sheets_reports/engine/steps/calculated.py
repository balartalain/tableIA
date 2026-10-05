"""Paso 3 del motor: métricas calculadas (`type: "formula"`) sobre las columnas ya agregadas."""
from __future__ import annotations

import pandas as pd


def apply_calculated_metrics(df: pd.DataFrame, metrics: list | None, metadata: dict) -> pd.DataFrame:
    """Evalúa cada `expression` (con `DataFrame.eval`, nunca `eval` de Python) y la guarda en
    su `alias`. Una fórmula mala deja la columna vacía en vez de tumbar el widget."""
    if metadata.get("nested") is not None:
        # En la tabla dinámica la fórmula ya se calculó celda a celda (con sus subtotales).
        return df
    formulas = [m for m in metrics or []
                if m.get("type") == "formula" and m.get("alias") and m.get("expression")]
    scalar = metadata.get("scalar_result")
    if scalar is not None:
        # Sin dimensiones el resultado son los valores sueltos (KPI): la fórmula se evalúa
        # sobre ellos, en el orden de la lista (una puede usar a la anterior).
        values = scalar.get("values")
        if isinstance(values, dict):
            for metric in formulas:
                values[metric["alias"]] = _evaluate(pd.DataFrame([values]), metric["expression"], scalar=True)
        return df
    for metric in formulas:
        df[metric["alias"]] = _evaluate(df, metric["expression"])
    return df


def _evaluate(frame: pd.DataFrame, expression: str, scalar: bool = False):
    try:
        result = frame.eval(expression)
    except Exception:  # noqa: BLE001 - una fórmula mala no tumba el widget entero
        return None
    return result.iloc[0] if scalar else result
