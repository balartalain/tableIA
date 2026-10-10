"""
Pipeline «tidy» para hojas de respuestas de Google Forms (`DataSource.is_form_response`).

Una hoja de respuestas guarda cada pregunta en su propia columna y cada aspecto de
una cuadrícula en «Pregunta [Aspecto]». Este módulo reescribe ese marco ancho en
uno largo: una fila por opción seleccionada, con «Pregunta | Aspecto | Respuesta»
y las columnas de contexto (ID de la respuesta, marca de tiempo y el resto de
preguntas planas) repetidas en cada fila. Con él, un tablero cuenta respuestas
únicas con el Conteo (o `count_distinct` sobre `ID`) y selecciones de casillas
con `SUM(IF(condición, 1, 0))` (ver docs/form_tidy.md).

La detección (`analyze`) y la transformación (`to_tidy`) corren sobre el frame
crudo de la hoja (`dtype=str`), antes de cualquier coerción numérica: así
«1, 2» de una casilla sigue siendo dos opciones y no el número 12.
"""
import re

import pandas as pd

from sheets_reports.utils.data import ID_ALIASES, find_id_column

# ------------------------------------------------------------------ umbrales
# Una columna de casillas se reconoce por: muchas celdas con comas, poca
# cardinalidad (pocas opciones distintas que se repiten) y tokens cortos.
CHECKBOX_PCT_COMMAS = 0.2   # ≥ 20% de las celdas con comas → casilla (confianza alta)
CHECKBOX_CARDINALITY = 0.4  # únicos / total < 0.4: el vocabulario se repite
CHECKBOX_MAX_TOKEN = 35     # tokens largos son texto libre, no opciones
BAND_PCT_COMMAS = 0.05      # banda media: 5–20% de celdas con comas
BAND_REPETITION = 0.9       # …si casi todo el vocabulario aparece en ≥ 2 celdas
BAND_MAX_VOCAB = 50         # …y el vocabulario es pequeño

QUESTION_TYPES = ("SCALAR", "CHECKBOX", "GRID", "CHECKBOX_GRID", "FREE_TEXT_WITH_COMMAS")

# Columnas que no son preguntas: el identificador de la respuesta y su fecha.
TIMESTAMP_ALIASES = ("marca de tiempo", "timestamp", "marca_tiempo")

SEPARATOR_DEFAULT = " ["   # «Pregunta [Aspecto]»
_DUPLICATE_SUFFIX = re.compile(r"\.\d+$")  # pandas: «Eval [A].1» ante encabezados duplicados

_EXCLUDED_COLUMNS = {alias.lower() for alias in ID_ALIASES + TIMESTAMP_ALIASES}


def _split_tokens(value) -> list[str]:
    """Opciones de una celda multi-valor («A, B, C»), sin vacíos; None/NaN → ninguna."""
    if value is None or pd.isna(value):
        return []
    return [token.strip() for token in re.split(r"\s*,\s*", str(value)) if token.strip()]


def _clean_header(name: str) -> str:
    """Encabezado sin el sufijo de duplicado de pandas («Eval [A].1» → «Eval [A]»)."""
    return _DUPLICATE_SUFFIX.sub("", str(name))


def _grid_re(separator: str) -> re.Pattern:
    """«Pregunta [Aspecto]»: el padre queda antes del ÚLTIMO separador (grupo
    codicioso) y el aspecto entre él y el cierre «]»."""
    return re.compile(rf"^(.*){re.escape(separator)}(.*)\]$")


def _grid_structure(columns, separator: str = SEPARATOR_DEFAULT) -> dict[str, list[tuple[str, str]]]:
    """Agrupa columnas «Pregunta [Aspecto]» por su pregunta padre:
    {padre: [(columna, aspecto), …]}. Solo padres con ≥ 2 columnas: una sola
    podría ser texto libre que lleva corchetes."""
    groups: dict[str, list[tuple[str, str]]] = {}
    for column in columns:
        match = _grid_re(separator).match(_clean_header(column))
        if match:
            parent = match.group(1).strip()
            aspect = match.group(2).strip()
            if parent and aspect:
                groups.setdefault(parent, []).append((column, aspect))
    return {parent: items for parent, items in groups.items() if len(items) >= 2}


def _multi_value_metrics(series: pd.Series) -> dict:
    """Métricas de una columna sobre sus celdas no nulas, para decidir si es de
    casillas (multi-valor separado por comas) o texto libre con comas."""
    cells = [str(v) for v in series.dropna() if str(v).strip()]
    tokens_per_cell = [_split_tokens(v) for v in cells]
    tokens = [token for cell in tokens_per_cell for token in cell]
    total = len(tokens)
    unique = sorted(set(tokens))
    cells_per_token = {token: 0 for token in unique}
    for cell in tokens_per_cell:
        for token in set(cell):
            cells_per_token[token] += 1
    repeated = sum(1 for token in unique if cells_per_token[token] >= 2)
    return {
        "cells": len(cells),
        "pct_commas": len([c for c in cells if "," in c]) / len(cells) if cells else 0.0,
        "cardinality_ratio": len(unique) / total if total else 0.0,
        "avg_token_length": sum(len(token) for token in tokens) / total if total else 0.0,
        "repetition_rate": repeated / len(unique) if unique else 0.0,
        "vocabulary": len(unique),
    }


def _is_checkbox(metrics: dict) -> bool:
    """Regla de casillas: muchas comas + poca cardinalidad + tokens cortos (confianza
    alta), o la banda media con vocabulario muy repetido y pequeño (confianza media)."""
    if (metrics["pct_commas"] >= CHECKBOX_PCT_COMMAS
            and metrics["cardinality_ratio"] < CHECKBOX_CARDINALITY
            and metrics["avg_token_length"] < CHECKBOX_MAX_TOKEN):
        return True
    return (BAND_PCT_COMMAS <= metrics["pct_commas"] < CHECKBOX_PCT_COMMAS
            and metrics["repetition_rate"] >= BAND_REPETITION
            and metrics["vocabulary"] <= BAND_MAX_VOCAB)


def _confidence(metrics: dict) -> float:
    """Confianza (0–1) de que la columna es de casillas: combinación normalizada de
    % de comas, inversa de la cardinalidad y tasa de repetición."""
    pct_commas = min(metrics["pct_commas"], 1.0)
    low_cardinality = 1 - min(metrics["cardinality_ratio"], 1.0)
    repetition = metrics["repetition_rate"]
    return round(0.4 * pct_commas + 0.3 * low_cardinality + 0.3 * repetition, 2)


def analyze(df: pd.DataFrame, custom_separator: str = SEPARATOR_DEFAULT) -> list[dict]:
    """Clasifica las columnas de una hoja de respuestas (frame crudo, `dtype=str`)
    → [{name, inferred_type, confidence, details}]. Excluye `ID` y la marca de
    tiempo; detecta cuadrículas («Pregunta [Aspecto]» con ≥ 2 columnas por padre,
    de casillas si sus celdas son multi-valor) y columnas de casillas frente a
    texto libre con comas (nunca se explosiona)."""
    names = [c for c in df.columns if str(c).strip().lower() not in _EXCLUDED_COLUMNS]
    grids = _grid_structure(names, custom_separator)
    grid_columns = {column for items in grids.values() for column, _ in items}
    report = []
    for name in names:
        if name in grid_columns:
            continue  # las de una cuadrícula se clasifican juntas, más abajo
        metrics = _multi_value_metrics(df[name])
        if _is_checkbox(metrics):
            inferred = "CHECKBOX"
        elif metrics["pct_commas"] > 0:
            inferred = "FREE_TEXT_WITH_COMMAS"
        else:
            inferred = "SCALAR"
        report.append({
            "name": name,
            "inferred_type": inferred,
            "confidence": _confidence(metrics) if inferred == "CHECKBOX" else 1.0,
            # Las de texto con comas que cumplieron parcialmente (muchas comas pero
            # vocabulario grande, p. ej.) se marcan para revisión manual.
            "details": metrics | {"needs_review": inferred == "FREE_TEXT_WITH_COMMAS"
                                  and metrics["pct_commas"] >= BAND_PCT_COMMAS},
        })
    for parent, items in grids.items():
        combined = pd.concat([df[column] for column, _ in items], ignore_index=True)
        metrics = _multi_value_metrics(combined)
        grid_type = "CHECKBOX_GRID" if _is_checkbox(metrics) else "GRID"
        for column, aspect in items:
            report.append({
                "name": column,
                "inferred_type": grid_type,
                "confidence": _confidence(metrics) if grid_type == "CHECKBOX_GRID" else 1.0,
                "details": metrics | {"parent": parent, "aspect": aspect, "needs_review": False},
            })
    return report


def _find_alias(columns, aliases) -> str | None:
    """La primera columna cuyo nombre coincide (sin mayúsculas) con un alias."""
    wanted = {alias.lower() for alias in aliases}
    for column in columns:
        if str(column).strip().lower() in wanted:
            return column
    return None


def _context_frame(df: pd.DataFrame, column: str, question: str,
                   context: list[str]) -> pd.DataFrame:
    """Casilla plana: las opciones de cada celda («A, B») explotan en una fila
    cada una, con la pregunta y `Aspecto` vacío."""
    frame = df[["ID"] + context + [column]].copy()
    frame["Respuesta"] = frame[column].map(_split_tokens)
    frame = frame.explode("Respuesta")
    frame["Pregunta"] = question
    frame["Aspecto"] = ""
    return frame.drop(columns=[column])


def _grid_frame(df: pd.DataFrame, items: list[tuple[str, str]], grid_type: str,
                parent: str, context: list[str]) -> pd.DataFrame:
    """Cuadrícula: `melt` de sus columnas (una fila por aspecto y respuesta); si
    es de casillas, además explota las opciones de cada celda."""
    columns = [column for column, _ in items]
    aspect_of = {column: aspect for column, aspect in items}
    frame = df.melt(id_vars=["ID"] + context, value_vars=columns,
                    var_name="_columna", value_name="Respuesta")
    frame["Pregunta"] = parent
    frame["Aspecto"] = frame["_columna"].map(aspect_of)
    frame = frame.drop(columns=["_columna"])
    if grid_type == "CHECKBOX_GRID":
        frame["Respuesta"] = frame["Respuesta"].map(_split_tokens)
        frame = frame.explode("Respuesta")
    return frame


def to_tidy(df: pd.DataFrame, custom_separator: str = SEPARATOR_DEFAULT) -> pd.DataFrame:
    """El modelo tidy: `ID` (el de la hoja o `1..n` generado), `marca_tiempo` si
    existe, columnas de contexto (`SCALAR` y `FREE_TEXT_WITH_COMMAS`, con su
    nombre de encabezado) y `Pregunta | Aspecto | Respuesta` — una fila por
    opción seleccionada. La clasificación es automática (`analyze`) y definitiva:
    incluir, excluir, renombrar y tipar las columnas del resultado es tarea de
    la configuración de columnas de la fuente (`apply_column_config`), igual
    que en una hoja normal. Se eliminan filas con `Respuesta` nula o vacía: el
    respondente que no contestó ninguna pregunta compleja desaparece."""
    df = df.copy()
    id_name = find_id_column(df.columns)
    timestamp_name = _find_alias(df.columns, TIMESTAMP_ALIASES)
    if id_name is None:
        df["ID"] = range(1, len(df) + 1)
    else:
        df = df.rename(columns={id_name: "ID"})
    context: list[str] = []
    if timestamp_name is not None:
        df = df.rename(columns={timestamp_name: "marca_tiempo"})
        context.append("marca_tiempo")

    inferred = {a["name"]: a["inferred_type"] for a in analyze(df, custom_separator)}
    questions = [c for c in df.columns if c not in ("ID", "marca_tiempo")]
    context += [name for name in questions
                if inferred.get(name, "SCALAR") in ("SCALAR", "FREE_TEXT_WITH_COMMAS")]

    # Cuadrículas: por padre (las que no agrupan — clasificadas a mano — quedan
    # como pregunta plana con su propio nombre).
    grid_types = [name for name in questions
                  if inferred.get(name) in ("GRID", "CHECKBOX_GRID")]
    grids = _grid_structure(grid_types, custom_separator)
    grouped = {column for items in grids.values() for column, _ in items}
    for name in grid_types:
        if name not in grouped:
            grids.setdefault(name, []).append((name, ""))

    frames = [_context_frame(df, name, name, context)
              for name in questions if inferred.get(name) == "CHECKBOX"]
    for parent, items in grids.items():
        grid_type = ("CHECKBOX_GRID"
                     if any(inferred.get(column) == "CHECKBOX_GRID"
                            for column, _ in items) else "GRID")
        frames.append(_grid_frame(df, items, grid_type, parent, context))

    order = (["ID"] + (["marca_tiempo"] if "marca_tiempo" in context else [])
             + [name for name in context if name != "marca_tiempo"]
             + ["Pregunta", "Aspecto", "Respuesta"])
    if not frames:
        return pd.DataFrame(columns=order)
    tidy = pd.concat(frames, ignore_index=True)
    tidy = tidy[tidy["Respuesta"].notna() & (tidy["Respuesta"].astype(str).str.strip() != "")]
    return tidy[order].reset_index(drop=True)
