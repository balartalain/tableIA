"""
«Clasificar con IA» en la matriz de correlación: la escala de medida de las columnas elegidas
(continua, discreta, ordinal, nominal o `none`) y, en las ordinales, el orden de sus valores de
menor a mayor. La IA ve el perfil de cada columna (lo que detectaron las reglas de
`utils/scales.py` y sus valores) y propone; el panel muestra la propuesta y la guarda en la
fuente si el usuario la acepta. Una propuesta inválida (un orden que no cubre los valores de la
columna) se descarta para esa columna: queda la escala detectada.
"""
import json
import logging

import pandas as pd

from sheets_reports.services.ai_spec import THINKING_BUDGETS, SpecGenerationError, generate_json
from sheets_reports.utils.scales import (MAX_CATEGORIES_LARGE, SCALES, column_values, describe,
                                         normalize_text, not_applicable, scale_errors)

logger = logging.getLogger(__name__)

MAX_COLUMNS = 20
# Valores de una columna que se le muestran a la IA para ordenar una ordinal.
MAX_VALUES = MAX_CATEGORIES_LARGE

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "columns": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "field": {"type": "string"},
                    "scale": {"type": "string", "enum": list(SCALES)},
                    "order": {"type": "array", "items": {"type": "string"}},
                    "reason": {"type": "string"},
                },
                "required": ["field", "scale", "reason"],
            },
        },
    },
    "required": ["columns"],
}

SCALES_PROMPT = """\
Clasifica la escala de medida de cada columna de una hoja de cálculo, para medir qué tan
relacionadas están entre sí.

Escalas:
- continuous: números que pueden tomar cualquier valor en un rango (montos, tiempos, promedios).
- discrete: números enteros que cuentan o puntúan (cantidad de hijos, nota de 1 a 10).
- ordinal: categorías con un orden natural (Bajo < Medio < Alto, escalas de acuerdo o
  satisfacción, niveles educativos, rangos de edad). Da su orden en `order`, de menor a mayor,
  con todos los valores de la columna escritos exactamente igual, salvo los que dicen que la
  pregunta no aplica («No aplica», «No utilizo este medio»): esos no van y cuentan como vacíos.
- nominal: categorías sin orden (carrera, provincia, Sí/No, sexo).
- none: no tiene sentido medir su relación (identificadores, nombres, correos, texto libre,
  fechas).

Para cada columna, `reason` explica en una frase corta (en español) por qué.

Columnas (con lo que detectaron las reglas automáticas y sus valores):
{columns}
"""


def _column_line(field: str, series: pd.Series, profile: dict) -> str:
    numeric = pd.api.types.is_numeric_dtype(series)
    line = (f"- {json.dumps(field, ensure_ascii=False)} ({'numérica' if numeric else 'texto'}; "
            f"detectada: {profile['scale']} — {describe(field, profile, numeric)})")
    if not numeric:
        values = column_values(series)
        if 0 < len(values) <= MAX_VALUES:
            line += f" — valores: {json.dumps(values, ensure_ascii=False)}"
    return line


def _clean(item: dict, df: pd.DataFrame) -> dict | None:
    """La propuesta de una columna, si es válida: con el orden escrito como en la hoja."""
    field, scale = item.get("field"), item.get("scale")
    order = item.get("order") if scale == "ordinal" else None
    if field not in df.columns or scale_errors(field, scale, order):
        return None
    out = {"scale": scale, "reason": str(item.get("reason") or "").strip()}
    if scale == "ordinal":
        # Las de «no aplica» no van en el orden: cuentan como vacías.
        actual = {normalize_text(v): v for v in column_values(df[field]) if not not_applicable(v)}
        written = [actual.get(normalize_text(v)) for v in order or [] if not not_applicable(v)]
        if not actual or None in written or set(written) != set(actual.values()):
            return None
        out["order"] = written
    return out


def classify_scales(df: pd.DataFrame, columns: list[str], scales: dict) -> dict:
    """{columna: {scale, order?, reason}} que propone la IA para `columns` (de `df`), con el
    perfil detectado de cada una (`scales`, de `SheetContext.scales`). Lanza
    SpecGenerationError si la IA no responde."""
    columns = [c for c in dict.fromkeys(columns) if c in df.columns][:MAX_COLUMNS]
    if not columns:
        raise SpecGenerationError("Elige al menos una columna para clasificar.")
    lines = "\n".join(_column_line(c, df[c], scales.get(c) or {"scale": "none"}) for c in columns)
    try:
        answer = generate_json(SCALES_PROMPT.format(columns=lines), RESPONSE_SCHEMA, call="scales",
                               thinking_budget=THINKING_BUDGETS["scales"])
    except SpecGenerationError:
        raise
    except Exception:
        logger.exception("Falló la clasificación de escalas con IA")
        raise SpecGenerationError("La IA no respondió correctamente. Intenta de nuevo.")
    out = {}
    for item in (answer or {}).get("columns") or []:
        if isinstance(item, dict) and item.get("field") in columns:
            clean = _clean(item, df)
            if clean:
                out[item["field"]] = clean
    return out
