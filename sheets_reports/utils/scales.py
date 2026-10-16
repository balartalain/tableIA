"""
La escala de medida de cada columna (continua, discreta, ordinal, nominal) y un perfil corto de
sus valores. De la escala depende cómo se mide la asociación entre dos columnas (la matriz de
correlación) y el perfil es lo que la IA ve de cada columna en vez de filas de ejemplo.

La escala se detecta con reglas sobre los valores; la que el usuario o la IA confirmaron se
guarda en la fuente (`DataSource.columns[].scale` y, en las ordinales, `order`) y manda sobre la
detectada (`effective_scales`).
"""
from __future__ import annotations

import re
import unicodedata

import pandas as pd

from sheets_reports.utils.data import _plain, is_id_column, time_order, to_key

SCALES = ("continuous", "discrete", "ordinal", "nominal", "none")
SCALE_LABELS = {
    "continuous": "continua",
    "discrete": "discreta",
    "ordinal": "ordinal",
    "nominal": "nominal",
    "none": "no se analiza",
}
# Escalas con las que se puede medir una asociación (las de `none` no: texto libre, fechas, ID).
ANALYZABLE = ("continuous", "discrete", "ordinal", "nominal")

# Texto con hasta estos valores distintos es una categoría; con más, texto libre, salvo que se
# repitan mucho (MAX_SHARE de las filas, hasta MAX_CATEGORIES_LARGE valores).
MAX_CATEGORIES = 15
MAX_CATEGORIES_LARGE = 50
MAX_SHARE = 0.05
# Texto con más de MIN_REPEATED valores, casi todos distintos (más de MAX_UNIQUE_SHARE de las
# filas con valor), es texto libre aunque tenga pocos valores.
MIN_REPEATED = 5
MAX_UNIQUE_SHARE = 0.8
# Una numérica entera con hasta estos valores distintos, en un rango corto, es discreta (conteos,
# notas, edades); con un rango mayor son montos (continua).
MAX_DISCRETE = 20
MAX_DISCRETE_RANGE = 100
# Nombres de columnas que identifican filas (además de la de sistema ID): no se analizan.
_IDENTIFIER = re.compile(r"(^|[_\s-])(id|codigo|matricula)([_\s-]|$)")
# Valores más frecuentes que se le muestran a la IA de una columna de texto libre.
TOP_VALUES = 5

# Escalas ordinales conocidas: niveles de menor a mayor, cada uno con sus sinónimos. Una columna
# cuyos valores caen todos en los niveles de una de ellas es ordinal, con ese orden.
ORDINAL_SCALES = [
    [("totalmente en desacuerdo", "muy en desacuerdo", "completamente en desacuerdo"),
     ("en desacuerdo", "desacuerdo"),
     ("ni de acuerdo ni en desacuerdo", "neutral", "neutro", "indiferente"),
     ("de acuerdo", "acuerdo"),
     ("totalmente de acuerdo", "muy de acuerdo", "completamente de acuerdo")],
    [("muy insatisfecho", "totalmente insatisfecho", "completamente insatisfecho", "nada satisfecho"),
     ("insatisfecho", "poco satisfecho"),
     ("ni satisfecho ni insatisfecho", "neutral", "neutro", "indiferente"),
     ("satisfecho", "bastante satisfecho"),
     ("muy satisfecho", "totalmente satisfecho", "completamente satisfecho")],
    [("nunca",), ("casi nunca", "rara vez", "raramente", "pocas veces"),
     ("a veces", "algunas veces", "ocasionalmente"),
     ("casi siempre", "frecuentemente", "a menudo", "muchas veces", "la mayoria de las veces",
      "la mayoria de veces"), ("siempre",)],
    [("muy bajo",), ("bajo",), ("medio", "intermedio", "moderado", "regular"), ("alto",), ("muy alto",)],
    [("pesimo", "muy malo"), ("malo", "deficiente"), ("regular", "aceptable"), ("bueno",),
     ("muy bueno",), ("excelente",)],
    [("nada importante", "sin importancia"), ("poco importante",),
     ("moderadamente importante", "algo importante"), ("importante",), ("muy importante",)],
    [("nada probable", "muy improbable"), ("poco probable", "improbable"), ("neutral",),
     ("probable",), ("muy probable",)],
    [("muy dificil",), ("dificil",), ("normal", "neutral", "regular"), ("facil",), ("muy facil",)],
    [("nada",), ("poco",), ("algo", "regular", "moderadamente"), ("bastante",), ("mucho", "muchisimo")],
]

_LEADING_NUMBER = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*([-–.:)]|\s)")


def _level_key(text) -> str:
    """Texto comparable con los niveles: sin acentos ni mayúsculas, sin «(a)» ni «/a», y con el
    femenino en masculino («Satisfecha» = «satisfecho»)."""
    plain = _plain(text)
    plain = re.sub(r"\(\s*a\s*\)|/\s*a\b|@", "", plain)
    plain = re.sub(r"[^\w\s]", " ", plain)
    return " ".join(re.sub(r"a$", "o", word) for word in plain.split())


_KNOWN_LEVELS = [
    {_level_key(name): index for index, names in enumerate(scale) for name in names}
    for scale in ORDINAL_SCALES
]


def known_order(values: list[str], ignored_set: set[str] | None = None) -> list[str] | None:
    """Los valores de menor a mayor si todos son niveles de una escala conocida (Likert,
    frecuencia, nivel...) o si todos empiezan con un número («1 - Malo»); None si no.
    Las respuestas no evaluables (No aplica, N/A, etc.) se ignoran por defecto al evaluar
    si coincide con una escala conocida."""
    if ignored_set is None:
        ignored_set = parse_ignored_values(DEFAULT_IGNORED_VALUES)
    eval_values = [v for v in values if normalize_text(v) not in ignored_set] if ignored_set else values
    if len(eval_values) < 2:
        return None
    for levels in _KNOWN_LEVELS:
        ranks = [levels.get(_level_key(v)) for v in eval_values]
        if all(r is not None for r in ranks):
            return [v for _, v in sorted(zip(ranks, eval_values), key=lambda p: p[0])]
    numbers = [_LEADING_NUMBER.match(f"{v} ") for v in eval_values]
    if all(numbers):
        keys = [float(m.group(1).replace(",", ".")) for m in numbers]
        return [v for _, v in sorted(zip(keys, eval_values), key=lambda p: p[0])]
    months = time_order(eval_values)
    if months is not None and not all(isinstance(v, (int, float)) for v in eval_values):
        return [str(v) for v in months]
    return None


def _text_values(series: pd.Series) -> pd.Series:
    values = series.dropna().map(lambda v: str(to_key(v)).strip())
    return values[values != ""]


def profile_column(series: pd.Series, name: str = "", date: bool = False,
                   submission: bool = False, ignored_set: set[str] | None = None) -> dict:
    """
    {scale, order?, uncertain, stats} de una columna. `date` (fechas completas) y `submission`
    (fecha de envío de un formulario) no se analizan. `uncertain`: la escala detectada podría
    ser otra (texto con pocos valores sin escala conocida: nominal, o tal vez ordinal).
    """
    total = len(series)
    filled = series.dropna()
    missing = round(100 * (1 - len(filled) / total), 1) if total else 0.0
    identifier = bool(_IDENTIFIER.search(_plain(name)))
    if is_id_column(name) or identifier or date or submission:
        return {"scale": "none", "uncertain": False, "stats": {"missing": missing}}

    if pd.api.types.is_numeric_dtype(series) and not pd.api.types.is_bool_dtype(series):
        distinct = int(filled.nunique())
        integer = bool(len(filled)) and bool((filled % 1 == 0).all())
        stats = {"distinct": distinct, "missing": missing, "integer": integer}
        if len(filled):
            stats.update(min=to_key(filled.min()), max=to_key(filled.max()))
        if distinct == 2 and set(filled.unique()) <= {0, 1}:
            # 0/1: una casilla marcada o no (dicotómica).
            scale = "nominal"
        elif integer and distinct <= MAX_DISCRETE and filled.max() - filled.min() <= MAX_DISCRETE_RANGE:
            scale = "discrete"
        else:
            scale = "continuous"
        return {"scale": scale, "uncertain": False, "stats": stats}

    values = _text_values(series)
    counts = values.value_counts()
    distinct = len(counts)
    stats = {"distinct": distinct, "missing": missing}
    few = distinct <= MAX_CATEGORIES or (
        distinct <= MAX_CATEGORIES_LARGE and distinct <= MAX_SHARE * max(len(values), 1))
    # Casi un valor distinto por fila (nombres, correos, comentarios): no es una categoría.
    unique = distinct > MIN_REPEATED and distinct > MAX_UNIQUE_SHARE * len(values)
    if not distinct or not few or unique:
        stats["top"] = [str(v) for v in counts.index[:TOP_VALUES]]
        return {"scale": "none", "uncertain": False, "stats": stats}
    # Los valores de una categoría: con ellos se valida que el orden de una ordinal los cubra.
    stats["values"] = [str(v) for v in counts.index]
    order = known_order(list(counts.index), ignored_set=ignored_set)
    if order is not None:
        return {"scale": "ordinal", "order": order, "uncertain": False, "stats": stats}
    # Dos valores (Sí/No) son nominales sin duda; con más, podrían tener un orden que no conocemos.
    return {"scale": "nominal", "uncertain": distinct > 2, "stats": stats}


def column_values(series: pd.Series) -> list[str]:
    """Los valores distintos de una columna como texto (como se comparan con `order`)."""
    return list(_text_values(series).unique())


def effective_scales(df: pd.DataFrame, saved: dict | None = None, only=None,
                     date_columns=(), submission: str | None = None,
                     ignored_set: set[str] | None = None) -> dict:
    """
    {columna: {scale, order?, uncertain, saved, stats}}: la escala detectada de cada columna
    (o de las de `only`), reemplazada por la guardada en la fuente (`saved`, de
    `df.attrs["column_scales"]`) cuando la hay.
    """
    saved = saved or {}
    columns = [c for c in df.columns if only is None or c in set(only)]
    out = {}
    for column in columns:
        series = mask_ignored(df[column], ignored_set)
        profile = profile_column(series, column, date=column in date_columns,
                                 submission=column == submission, ignored_set=ignored_set)
        override = saved.get(column)
        if isinstance(override, dict) and override.get("scale") in SCALES:
            profile = {**profile, "scale": override["scale"], "uncertain": False, "saved": True}
            profile.pop("order", None)
            if override["scale"] == "ordinal" and override.get("order"):
                order = list(override["order"])
                if ignored_set:
                    order = [v for v in order if normalize_text(v) not in ignored_set]
                profile["order"] = order
        else:
            profile["saved"] = False
        out[column] = profile
    return out


def describe(field: str, profile: dict, numeric: bool) -> str:
    """El perfil en una frase corta para el contexto de la IA, ej. «continua: 18 a 64, 47
    valores distintos» u «ordinal: Bajo < Medio < Alto»."""
    scale = profile.get("scale")
    stats = profile.get("stats") or {}
    if scale == "none":
        if stats.get("top"):
            more = stats.get("distinct", 0) - len(stats["top"])
            top = ", ".join(f'"{v}"' for v in stats["top"])
            return f"texto libre, no se analiza; los más frecuentes: {top}" + (f" y {more} más" if more > 0 else "")
        return "no se analiza"
    text = SCALE_LABELS[scale]
    if scale == "ordinal" and profile.get("order"):
        return f"{text}: " + " < ".join(profile["order"])
    if numeric and "min" in stats:
        text += f": {stats['min']} a {stats['max']}, {stats['distinct']} valores distintos"
    return text


DEFAULT_IGNORED_VALUES = "No aplica, N/A, No corresponde, No utilizo este medio, No se utiliza, No he recibido este servicio, Sin respuesta"


def parse_ignored_values(raw: str | list[str] | None) -> set[str]:
    """Conjunto de textos normalizados a ignorar en una correlación."""
    if not raw:
        return set()
    if isinstance(raw, (list, tuple, set)):
        return {normalize_text(v) for v in raw if v and str(v).strip()}
    if isinstance(raw, str):
        return {normalize_text(v) for v in raw.split(",") if v.strip()}
    return set()


def mask_ignored(series: pd.Series, ignored_set: set[str] | None) -> pd.Series:
    """Reemplaza por None los valores de la serie que están en `ignored_set` (sin distinguir mayúsculas ni espacios)."""
    if not ignored_set:
        return series
    return series.map(lambda v: None if pd.notna(v) and normalize_text(v) in ignored_set else v)


def normalize_text(value) -> str:
    """Comparación de valores de una escala con los de la hoja: espacios y mayúsculas aparte."""
    text = unicodedata.normalize("NFC", str(value)).strip()
    return " ".join(text.split()).casefold()


def scale_errors(name: str, scale, order=None) -> list[str]:
    """Una escala guardada o propuesta es válida: de la lista, y con `order` (textos sin
    repetir) solo si es ordinal y lo trae."""
    if scale not in SCALES:
        return [f"Escala no válida para '{name}': usa {', '.join(SCALES)}"]
    if order is None:
        return []
    if scale != "ordinal":
        return [f"'{name}': solo una escala ordinal lleva orden de valores"]
    if not isinstance(order, list) or not all(isinstance(v, str) and v.strip() for v in order):
        return [f"El orden de '{name}' debe ser una lista de valores"]
    if len({normalize_text(v) for v in order}) != len(order):
        return [f"El orden de '{name}' repite valores"]
    return []


def order_gaps(series: pd.Series, order, ignored_set: set[str] | None = None) -> list[str]:
    """Los valores de la columna que no están en `order`: una ordinal debe cubrirlos todos,
    salvo los valores a ignorar (No aplica, N/A, etc.) si se proporcionan."""
    known = {normalize_text(v) for v in order or []} | (ignored_set or set())
    return [v for v in column_values(series) if normalize_text(v) not in known]


def dummy_count(profile: dict | None) -> int:
    """Cuántas dummies (filas 0/1) da una nominal: una por categoría; una sola si es dicotómica."""
    stats = (profile or {}).get("stats") or {}
    values = stats.get("values")
    # Una numérica marcada como nominal no trae sus valores: cuenta los distintos.
    count = (len({normalize_text(v) for v in values}) if values
             else stats.get("distinct", 0))
    return 1 if count <= 2 else count
