"""
Chatbot analítico sobre la fuente de datos del tablero usando Gemini y ejecución de código Python.
Sube la fuente de datos a Google una sola vez, reutiliza el identificador de archivo en consultas
subsecuentes, permite hacer preguntas analíticas, ejecutar cálculos en pandas y generar gráficos
con matplotlib/seaborn.
"""
from __future__ import annotations

import logging
import os
import tempfile
from typing import Any

from django.conf import settings

from sheets_reports.services.ai_spec import (
    SpecGenerationError,
    gemini_delete_file,
    gemini_get_file,
    gemini_upload_file,
    generate_with_code_execution,
)
from sheets_reports.services.sheets import load_source

logger = logging.getLogger(__name__)

SYSTEM_INSTRUCTION = """\
Eres un asistente analítico experto de datos en español para un tablero de control.
El usuario te hará preguntas sobre el conjunto de datos cargado en tu entorno de trabajo (archivo CSV).

Instrucciones:
1. Para responder preguntas cuantitativas, estadísticas o comparaciones, escribe y ejecuta código Python con `pandas` y `numpy` para obtener cifras exactas y comprobadas a partir del CSV.
2. Si la respuesta amerita un gráfico (o el usuario lo pide), crea el gráfico con `matplotlib.pyplot` o `seaborn`: prepara los datos primero y dibuja la figura en un único paso. No invoques `plt.figure()` ni `plt.show()` de forma aislada sin datos. Guárdalo obligatoriamente en disco con `plt.savefig('grafico.png', bbox_inches='tight')` y CIERRA SIEMPRE todas las figuras con `plt.close('all')` para evitar la emisión de lienzos en blanco o gráficos duplicados (con fondo limpio, etiquetas claras en español y título descriptivo).
3. Presenta tu respuesta final de forma clara, profesional y estructurada, usando formato Markdown (títulos, negritas, listas o tablas formateadas). No incluyas etiquetas ni sintaxis de imágenes Markdown (como `![...](...)`) en tu texto explicativo, ya que los gráficos generados se muestran automáticamente.
4. Explica directamente los hallazgos o conclusiones clave de los datos analizados.
5. REGLA ESTRICTA DE COMUNICACIÓN: Ve DIRECTO a las respuestas, cifras y conclusiones. NUNCA narres tus intenciones técnicas ni digas frases como "Para responder...", "Ejecutaré el siguiente código...", "Primero cargaré el archivo CSV...", "A continuación presento el código...". Al usuario final no le interesan los pasos técnicos internos; solo entrega los resultados y conclusiones finales.
"""


def get_or_upload_source_file(source, df) -> Any:
    """
    Obtiene la referencia del archivo en Gemini Files API para la fuente dada.
    Reutiliza `source.gemini_file_id` si el archivo existe y sigue activo en Gemini.
    Si no existe o expiró, exporta el DataFrame a CSV temporal, lo sube y guarda el nuevo ID en la fuente.
    """
    file_id = getattr(source, "gemini_file_id", "")
    if isinstance(file_id, str) and file_id.strip():
        existing = gemini_get_file(file_id.strip())
        if existing:
            return existing

    # Subir DataFrame a Gemini Files API
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8") as f:
            df.to_csv(f, index=False)
            tmp_path = f.name

        display_name = f"source_{getattr(source, 'id', 'data')}.csv"
        file_ref = gemini_upload_file(tmp_path, display_name=display_name)
        new_file_id = getattr(file_ref, "name", "")
        if new_file_id and hasattr(source, "save") and getattr(source, "pk", None):
            source.gemini_file_id = new_file_id
            source.save(update_fields=["gemini_file_id"])
        return file_ref
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def delete_source_gemini_file(source) -> None:
    """Elimina el archivo asociado a la fuente en Gemini Files API si existe y limpia su ID."""
    file_id = getattr(source, "gemini_file_id", "")
    if not (isinstance(file_id, str) and file_id.strip()):
        return

    gemini_delete_file(file_id.strip())

    if hasattr(source, "save") and getattr(source, "pk", None):
        source.gemini_file_id = ""
        source.save(update_fields=["gemini_file_id"])


def ask_data_chat(source, prompt: str) -> dict[str, Any]:
    """
    Ejecuta una consulta al chatbot sobre la fuente de datos usando ejecución de código en Gemini.
    Sube el archivo CSV a Gemini únicamente si no se ha subido previamente o si expiró.
    Devuelve:
      {
        "text": "...",
        "images": ["data:image/png;base64,..."],
        "code": ["código python ejecutado..."]
      }
    """
    df = load_source(source)
    if df.empty:
        return {"text": "La fuente de datos está vacía.", "images": [], "code": []}

    file_ref = get_or_upload_source_file(source, df)

    cols_preview = ", ".join(list(df.columns)[:40])
    full_prompt = (
        f"Conjunto de datos: '{getattr(source, 'label', 'Datos')}'. Total filas: {len(df)}. Columnas: {cols_preview}.\n\n"
        f"Pregunta del usuario:\n{prompt}"
    )

    return generate_with_code_execution(
        prompt=full_prompt,
        file_ref=file_ref,
        system_instruction=SYSTEM_INSTRUCTION,
    )
