"""
Sugerir nombres limpios (labels) para las columnas de una fuente con IA.
Toma los nombres originales de las columnas y devuelve nombres coherentes, fáciles de leer
y fáciles de interpretar por una IA a la hora de generar código.
Si el nombre es suficientemente descriptivo o corto, puede quedar como está.
"""
from __future__ import annotations

import json
import logging

from sheets_reports.services.ai_spec import THINKING_BUDGETS, SpecGenerationError, generate_json
from sheets_reports.utils.data import is_id_column

logger = logging.getLogger(__name__)

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "columns": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "original": {"type": "string"},
                    "clean": {"type": "string"},
                },
                "required": ["original", "clean"],
            },
        },
    },
    "required": ["columns"],
}

COLUMN_LABELS_PROMPT = """\
Analiza la siguiente lista de nombres de columnas de una hoja de cálculo y propón un nombre limpio, \
conciso y fácil de leer para cada una.

Objetivo:
- Que los nombres sean claros para los usuarios en tablas, gráficos y filtros.
- Que sean directos y fáciles de interpretar por un modelo de lenguaje / IA cuando deba generar fórmulas, consultas y código.

Reglas:
1. Prefijos y texto repetitivo:
   - Si varias columnas provienen de un formulario, encuesta o matriz y comparten una parte repetida \
     (por ejemplo: "¿En qué medida está de acuerdo con: [Atención recibida]?", "Sección 2 - Datos demográficos: Edad"), \
     elimina el preámbulo repetitivo o la pregunta larga y quédate con el núcleo temático diferencial \
     (ej: "Atención recibida", "Edad").
2. Nombres demasiado largos o verbosos:
   - Simplifica oraciones extensas a términos comerciales o descriptivos breves (ej: \
     "Cantidad total de unidades defectuosas reportadas por el cliente" -> "Unidades defectuosas").
3. Nombres cortos o suficientemente descriptivos:
   - Si el nombre ya es corto, claro y descriptivo (ej: "Fecha", "Cliente", "Total", "Monto", "Estado", "Categoría", "ID", "País"), \
     mantenlo exactamente como está.
4. Unicidad:
   - Ninguna columna debe terminar con el mismo nombre limpio que otra. Si dos columnas tratan aspectos similares, \
     asegúrate de que el nombre limpio conserve su elemento distintivo.
5. Formato:
   - Usa mayúsculas y minúsculas naturales (tipo oración o título).
   - No incluyas corchetes, comillas ni signos de interrogación innecesarios.
   - Longitud ideal: 1 a 4 palabras (máximo 40 caracteres).
6. Totalidad:
   - Debes incluir exactamente una entrada para cada columna recibida, con su nombre original en "original" \
     y el nombre propuesto en "clean".

Lista de columnas originales:
{columns}
"""


def _ensure_unique_labels(mapping: dict[str, str], original_columns: list[str]) -> dict[str, str]:
    """
    Garantiza que ningún label resultante esté duplicado entre las columnas.
    Si hay colisiones, desambigua agregando un sufijo o manteniendo el original.
    """
    out: dict[str, str] = {}
    seen_labels: set[str] = set()

    for col in original_columns:
        clean = (mapping.get(col) or "").strip()
        if not clean:
            clean = col

        if clean not in seen_labels:
            seen_labels.add(clean)
            out[col] = clean
        else:
            # Desambiguar si la IA generó nombres duplicados
            candidate = f"{clean} (2)"
            counter = 2
            while candidate in seen_labels:
                counter += 1
                candidate = f"{clean} ({counter})"
            seen_labels.add(candidate)
            out[col] = candidate

    return out


def suggest_column_labels(column_names: list[str]) -> dict[str, str]:
    """
    Devuelve un diccionario {nombre_original: nombre_limpio} para la lista de columnas dada.
    Lanza SpecGenerationError si la IA no responde o no se pudo generar el resultado.
    """
    # Filtrar nombres vacíos y la columna de sistema ID
    valid_columns = [str(c).strip() for c in column_names if str(c).strip() and not is_id_column(str(c).strip())]
    # Mantener orden sin duplicados
    valid_columns = list(dict.fromkeys(valid_columns))

    if not valid_columns:
        return {}

    lines = "\n".join(f"- {json.dumps(c, ensure_ascii=False)}" for c in valid_columns)
    prompt = COLUMN_LABELS_PROMPT.format(columns=lines)

    try:
        answer = generate_json(
            prompt,
            RESPONSE_SCHEMA,
            temperature=0,
            call="column_labels",
            thinking_budget=THINKING_BUDGETS.get("column_labels", 256),
        )
    except SpecGenerationError:
        raise
    except Exception as e:
        logger.exception("Falló la sugerencia de nombres de columnas con IA")
        raise SpecGenerationError("La IA no respondió correctamente. Intenta de nuevo.") from e

    items = (answer or {}).get("columns") or []
    raw_mapping = {}
    for item in items:
        if isinstance(item, dict) and "original" in item and "clean" in item:
            orig = str(item["original"]).strip()
            clean = str(item["clean"]).strip()
            if orig:
                raw_mapping[orig] = clean

    # Asegurar que todas las columnas recibidas tengan una respuesta y sean únicas
    return _ensure_unique_labels(raw_mapping, valid_columns)
