"""
Medidas de asociación entre dos columnas:
  - correlación (matriz de correlación), según la escala (ver `utils/scales.py`): Pearson o
    Spearman entre numéricas; Spearman si una es ordinal (por la posición de su valor en
    `order`) o una dummy 0/1 de una nominal (`one_hot`: una por categoría, «¿pertenece?»).
    De -1 a 1.
  - V de Cramér (widget `cramer`), entre categorías: de 0 a 1.
Cada par usa sus filas completas y devuelve cuántas usó; con menos de MIN_ROWS, o con una
columna que no varía, no hay medida (None), no un 0 engañoso.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from sheets_reports.utils.scales import normalize_text, not_applicable

MIN_ROWS = 3
# Escalas que entran en una correlación (una nominal, como sus dummies).
CORRELATED_SCALES = ("continuous", "discrete", "ordinal", "nominal")
# El valor «sí» de una dicotómica: su dummy es la que se muestra.
_YES = {"si", "sí", "yes", "1", "true", "verdadero", "x"}


def _round(value) -> Optional[float]:
    return None if value is None or pd.isna(value) else round(float(value), 4)


def encode_ordinal(series: pd.Series, order: list) -> tuple[pd.Series, int]:
    """La posición de cada valor en `order` (0 el menor); vacío si no está en `order`.
    → (posiciones, cuántas filas con valor no estaban en `order`). Las de «no aplica» son
    vacías sin más: no se informan."""
    rank = {normalize_text(v): i for i, v in enumerate(order or [])}
    filled = series.notna() & ~series.map(lambda v: pd.notna(v) and not_applicable(v)).astype(bool)
    codes = series.map(lambda v: rank.get(normalize_text(v)) if pd.notna(v) else None)
    codes = pd.to_numeric(codes, errors="coerce")
    return codes, int((filled & codes.isna()).sum())


def as_numbers(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def correlation(x: pd.Series, y: pd.Series, method: str) -> tuple[Optional[float], int]:
    """Pearson o Spearman sobre las filas con ambos valores."""
    valid = x.notna() & y.notna()
    n = int(valid.sum())
    if n < MIN_ROWS:
        return None, n
    a, b = x[valid].astype(float), y[valid].astype(float)
    if a.nunique() < 2 or b.nunique() < 2:
        return None, n
    if method == "spearman":
        # Spearman es Pearson sobre los rangos (con empates promediados).
        a, b = a.rank(), b.rank()
    return _round(a.corr(b)), n


def cramers_v(s1: pd.Series, s2: pd.Series) -> tuple[Optional[float], int]:
    """V de Cramér entre dos series categóricas, de 0 a 1."""
    valid = s1.notna() & s2.notna()
    n = int(valid.sum())
    if n < MIN_ROWS:
        return None, n
    ct = pd.crosstab(s1[valid].astype(str), s2[valid].astype(str))
    r, c = ct.shape
    k = min(r - 1, c - 1)
    if k <= 0:
        return None, n
    obs = ct.values.astype(float)
    exp = (obs.sum(axis=1, keepdims=True) @ obs.sum(axis=0, keepdims=True)) / n
    with np.errstate(divide="ignore", invalid="ignore"):
        chi2 = np.where(exp > 0, ((obs - exp) ** 2) / exp, 0.0).sum()
    return _round(float(np.clip(np.sqrt((chi2 / n) / k), 0.0, 1.0))), n


def _category(value) -> str | None:
    """El valor de una categoría como texto comparable (2026.0 = 2026); None si está vacío o
    dice que no aplica."""
    if pd.isna(value):
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = str(value).strip()
    return None if not text or not_applicable(text) else text


def one_hot(series: pd.Series) -> list[tuple[str, pd.Series]]:
    """Las dummies de una nominal: [(categoría, serie 0/1)], de la más a la menos frecuente.
    Una fila vacía o de «no aplica» queda vacía en todas. Una dicotómica (2 categorías) da una
    sola dummy, la del «sí» (o la menos frecuente): la otra sería su espejo."""
    text = series.map(_category)
    keys = text.map(lambda v: normalize_text(v) if v is not None else None)
    counts = keys.value_counts()
    names = {}
    for key, value in zip(keys, text):
        if key is not None:
            names.setdefault(key, value)
    categories = list(counts.index)
    if len(categories) == 2:
        yes = [k for k in categories if k in _YES]
        categories = yes[:1] or categories[-1:]
    filled = keys.notna()
    return [(names[key], (keys == key).astype(float).where(filled)) for key in categories]


def measure_for(scale_a: str, scale_b: str, method: str) -> str:
    """La medida de un par: Spearman si alguna es ordinal (solo su orden tiene sentido) o una
    dummy de una nominal (0/1); si no, el método elegido ("pearson" | "spearman")."""
    return "spearman" if {"ordinal", "nominal"} & {scale_a, scale_b} else method


def associate(a: dict, b: dict, method: str) -> tuple[Optional[float], int, str]:
    """La correlación entre dos variables preparadas (`prepare`): (valor, filas, medida)."""
    measure = measure_for(a["scale"], b["scale"], method)
    value, n = correlation(a["numbers"], b["numbers"], measure)
    return value, n, measure


def prepare(series: pd.Series, scale: str, order: list | None = None) -> list[dict]:
    """Las filas de la matriz que da una columna, como números: una por columna (una ordinal,
    por la posición de su valor en `order`, con cuántos valores no estaban en el orden) o, en
    una nominal, una por dummy (con su categoría en `value`)."""
    if scale == "nominal":
        return [{"scale": scale, "numbers": numbers, "unmatched": 0, "value": value}
                for value, numbers in one_hot(series)]
    unmatched = 0
    if scale == "ordinal":
        numbers, unmatched = encode_ordinal(series, order or [])
    else:
        numbers = as_numbers(series)
    return [{"scale": scale, "numbers": numbers, "unmatched": unmatched}]
