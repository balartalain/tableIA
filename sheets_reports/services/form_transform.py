"""
Pipeline de transformación para hojas de respuestas de Google Forms (`DataSource.is_form_response`).

Una hoja de respuestas guarda cada pregunta en su propia columna. Este módulo mantiene
el formato ancho (1 fila por respuesta `_ID`), conserva las columnas escalares y de cuadrículas
simples (`GRID`), y transforma las preguntas de casillas (`CHECKBOX` y `CHECKBOX_GRID`) creando
una columna binaria por cada valor posible en formato `pregunta_valor` (ver docs/form_transform.md).

La detección (`analyze`) y la transformación (`transform`) corren sobre el frame
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

# Columnas que no son preguntas: el identificador de la respuesta.
# Valor de una opción de casilla en cada respuesta (ver `_expand_checkbox_column`).
CHECKED, UNCHECKED = "Sí", "No"
SEPARATOR_DEFAULT = " ["   # «Pregunta [Aspecto]»
_DUPLICATE_SUFFIX = re.compile(r"\.\d+$")  # pandas: «Eval [A].1» ante encabezados duplicados

_EXCLUDED_COLUMNS = {alias.lower() for alias in ID_ALIASES}


def _split_tokens(value) -> list[str]:
    """Opciones de una celda multi-valor («A, B, C»), sin vacíos; None/NaN → ninguna. Una coma
    dentro de paréntesis o corchetes es parte de la opción («Comunicación (WhatsApp, correo)»),
    no un separador."""
    if value is None or pd.isna(value):
        return []
    tokens, current, depth = [], [], 0
    for char in str(value):
        if char in "([":
            depth += 1
        elif char in ")]":
            depth = max(depth - 1, 0)
        if char == "," and depth == 0:
            tokens.append("".join(current))
            current = []
        else:
            current.append(char)
    tokens.append("".join(current))
    return [token.strip() for token in tokens if token.strip()]


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


def _expand_checkbox_column(series: pd.Series, col_name: str) -> pd.DataFrame:
    """Para una columna de casillas (CHECKBOX o CHECKBOX_GRID), obtiene los valores
    únicos separados por coma y genera una columna `col_name_valor` por cada uno
    con «Sí» si fue seleccionado y «No» en otro caso: una columna de texto, como cualquier
    pregunta de sí o no (se agrupa por ella y se cuenta; no se suma ni se promedia)."""
    clean_name = _clean_header(col_name)
    tokens_per_cell = [
        _split_tokens(v) if v is not None and not pd.isna(v) else []
        for v in series
    ]
    unique_tokens = []
    seen = set()
    for tokens in tokens_per_cell:
        for token in tokens:
            if token not in seen:
                seen.add(token)
                unique_tokens.append(token)

    if not unique_tokens:
        return pd.DataFrame(index=series.index)

    cols = {}
    for token in unique_tokens:
        cols[f"{clean_name}_{token}"] = [
            CHECKED if token in tokens else UNCHECKED
            for tokens in tokens_per_cell
        ]
    return pd.DataFrame(cols, index=series.index)


def transform(df: pd.DataFrame, custom_separator: str = SEPARATOR_DEFAULT) -> pd.DataFrame:
    """Transforma las respuestas de formulario: mantiene el formato ancho (1 fila por respuesta `_ID`),
    conserva las columnas escalares y de cuadrículas simples (`GRID`), y transforma las columnas
    multi-opción (`CHECKBOX` y `CHECKBOX_GRID`) creando una columna por cada valor posible
    en formato `pregunta_valor` con «Sí» si fue seleccionado y «No» si no."""
    df = df.copy()
    id_name = find_id_column(df.columns)
    if id_name is None:
        df["_ID"] = range(1, len(df) + 1)
    else:
        df = df.rename(columns={id_name: "_ID"})

    report = analyze(df, custom_separator)
    inferred = {a["name"]: a["inferred_type"] for a in report}
    grid_of = {a["name"]: (a["details"]["parent"], a["details"]["aspect"])
               for a in report if a["inferred_type"] in ("GRID", "CHECKBOX_GRID")}

    # Las columnas se juntan primero y el frame se arma de una vez: insertarlas una a una lo
    # fragmenta y, con muchas casillas expandidas, hace lenta la carga de la fuente.
    cols = {}
    if "_ID" in df.columns:
        cols["_ID"] = df["_ID"]
    grids: dict[str, dict] = {}
    checkboxes = []

    for col in df.columns:
        if col == "_ID":
            continue
        kind = inferred.get(col, "SCALAR")
        if kind in ("CHECKBOX", "CHECKBOX_GRID"):
            expanded = _expand_checkbox_column(df[col], col)
            cols.update(expanded.items())
            produced = {name[len(_clean_header(col)) + 1:]: name for name in expanded.columns}
        else:
            clean_col = _clean_header(col)
            if clean_col in cols:
                clean_col = col
            cols[clean_col] = df[col]
            produced = clean_col
        if col in grid_of:
            parent, aspect = grid_of[col]
            grid = grids.setdefault(parent, {"kind": kind, "aspects": []})
            grid["aspects"].append([aspect, produced])
        elif kind == "CHECKBOX" and produced:
            checkboxes.append((_clean_header(col), produced))

    # Una pregunta de casillas es una cuadrícula en ancho: cada opción es un aspecto con su
    # columna Sí/No.
    for question, produced in checkboxes:
        name = question
        while name in grids:
            name += " (casillas)"
        grids[name] = {"kind": "CHECKBOX", "aspects": [[option, column] for option, column in produced.items()]}

    out = pd.DataFrame(cols, index=df.index)
    # Las cuadrículas y las preguntas de casillas, para verlas en formato largo (ver
    # `grid_long`): {pregunta: {kind, aspects: [[aspecto, columna]]}}. En una cuadrícula de
    # casillas la columna es {opción: columna «pregunta [aspecto]_opción»}; en una pregunta de
    # casillas (CHECKBOX) cada opción es un aspecto con su columna Sí/No.
    out.attrs["grids"] = grids
    return out


def grid_long(df: pd.DataFrame, grid: dict) -> pd.DataFrame:
    """Una cuadrícula (`df.attrs["grids"][pregunta]`) en formato largo: una fila por respuesta
    y aspecto (`row` = índice de la fila de `df`, `aspect`, `value`), en el orden de los aspectos.
    En una cuadrícula de casillas, una fila por opción marcada; las celdas vacías no generan
    filas. Las columnas que ya no están en `df` se omiten."""
    parts = []
    for aspect, column in grid["aspects"]:
        options = column.items() if isinstance(column, dict) else [(None, column)]
        for option, name in options:
            if name not in df.columns:
                continue
            series = df[name]
            if option is None:
                keep = series.notna() & (series.astype(str).str.strip() != "")
                values = series[keep]
            else:
                values = pd.Series(option, index=series.index[series == CHECKED])
            parts.append(pd.DataFrame({"row": values.index, "aspect": aspect,
                                       "value": values.to_numpy()}))
    if not parts:
        return pd.DataFrame({"row": [], "aspect": [], "value": []})
    long = pd.concat(parts, ignore_index=True)
    # En el orden de las respuestas y, dentro de cada una, de los aspectos.
    order = {aspect: i for i, (aspect, _) in enumerate(grid["aspects"])}
    long["_order"] = long["aspect"].map(order)
    return long.sort_values(["row", "_order"], kind="stable").drop(columns="_order").reset_index(drop=True)


# Alias de compatibilidad
to_tidy = transform
