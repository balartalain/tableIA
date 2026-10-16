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
from sheets_reports.utils.scales import (DEFAULT_IGNORED_VALUES, MAX_CATEGORIES_LARGE, SCALES,
                                         column_values, describe, effective_scales, mask_ignored, normalize_text,
                                         parse_ignored_values, scale_errors)

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
  con los valores de la escala escritos exactamente igual.
  IMPORTANTE: Respuestas no evaluables como «No aplica», «N/A», «No utilizo este medio»,
  «No se utiliza», «Sin respuesta» o similares NO son niveles de la escala ordinal; son opciones
  a ignorar (equivalentes a nulos): NUNCA las incluyas en `order`.
  Si la columna tiene una escala ordinal más opciones no evaluables, clasifícala como ordinal
  incluyendo en `order` únicamente los niveles reales de menor a mayor.
- nominal: categorías sin orden (carrera, provincia, Sí/No, sexo).
- none: no tiene sentido medir su relación (identificadores, nombres, correos, texto libre,
  fechas).

Para cada columna, `reason` explica en una frase corta (en español) por qué.

Columnas (con lo que detectaron las reglas automáticas y sus valores evaluables):
{columns}
"""


def _column_line(field: str, series: pd.Series, profile: dict, ignored_set: set[str] | None = None) -> str:
    numeric = pd.api.types.is_numeric_dtype(series)
    line = (f"- {json.dumps(field, ensure_ascii=False)} ({'numérica' if numeric else 'texto'}; "
            f"detectada: {profile['scale']} — {describe(field, profile, numeric)})")
    if not numeric:
        eval_series = mask_ignored(series, ignored_set) if ignored_set else series
        values = column_values(eval_series)
        if 0 < len(values) <= MAX_VALUES:
            line += f" — valores: {json.dumps(values, ensure_ascii=False)}"
    return line


def _clean(item: dict, df: pd.DataFrame, ignored_set: set[str] | None = None) -> dict | None:
    """La propuesta de una columna, si es válida: con el orden escrito como en la hoja."""
    field, scale = item.get("field"), item.get("scale")
    order = item.get("order") if scale == "ordinal" else None
    if field not in df.columns or scale_errors(field, scale, order):
        return None
    # Una columna de texto no puede ser continuous ni discrete
    if not pd.api.types.is_numeric_dtype(df[field]) and scale in ("continuous", "discrete"):
        return None
    out = {"scale": scale, "reason": str(item.get("reason") or "").strip()}
    if scale == "ordinal":
        series = mask_ignored(df[field], ignored_set) if ignored_set else df[field]
        actual = {normalize_text(v): v for v in column_values(series)}
        # Limpiar del orden cualquier valor que pertenezca a ignored_set
        written = [actual.get(normalize_text(v)) for v in order or [] if normalize_text(v) in actual]
        if not actual or None in written or set(written) != set(actual.values()):
            return None
        out["order"] = written
    return out


def classify_scales(df: pd.DataFrame, columns: list[str], scales: dict,
                    ignored_values: str | None = None) -> dict:
    """{columna: {scale, order?, reason}} que propone la IA para `columns` (de `df`), con el
    perfil detectado de cada una (`scales`, de `SheetContext.scales`). Lanza
    SpecGenerationError si la IA no responde."""
    columns = [c for c in dict.fromkeys(columns) if c in df.columns][:MAX_COLUMNS]
    if not columns:
        raise SpecGenerationError("Elige al menos una columna para clasificar.")
    ignored_set = parse_ignored_values(ignored_values) if ignored_values else set()
    detected_scales = effective_scales(df, saved=scales, only=columns, ignored_set=ignored_set)
    lines = "\n".join(_column_line(c, df[c], detected_scales.get(c) or {"scale": "none"}, ignored_set=ignored_set) for c in columns)
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
            clean = _clean(item, df, ignored_set=ignored_set)
            if clean:
                out[item["field"]] = clean
    return out
