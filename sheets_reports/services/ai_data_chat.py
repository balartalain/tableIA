"""
Chatbot analítico sobre la fuente de datos del tablero usando Gemini y ejecución de código Python.
Sube la fuente de datos a Google y permite hacer preguntas analíticas, ejecutar cálculos en pandas
y generar gráficos con matplotlib/seaborn.
"""
from __future__ import annotations

import logging
import os
import tempfile
from typing import Any

from sheets_reports.services.ai_spec import SpecGenerationError, generate_with_code_execution
from sheets_reports.services.sheets import load_source

logger = logging.getLogger(__name__)

SYSTEM_INSTRUCTION = """\
Eres un asistente analítico experto de datos en español para un tablero de control.
El usuario te hará preguntas sobre el conjunto de datos cargado en tu entorno de trabajo (archivo CSV).

Instrucciones:
1. Para responder preguntas cuantitativas, estadísticas o comparaciones, escribe y ejecuta código Python con `pandas` y `numpy` para obtener cifras exactas y comprobadas a partir del CSV.
2. Si la respuesta amerita un gráfico (o el usuario lo pide), crea el gráfico con `matplotlib.pyplot` o `seaborn` y guárdalo obligatoriamente en disco con `plt.savefig('grafico.png', bbox_inches='tight')` (con fondo limpio, etiquetas claras en español y título descriptivo).
3. Presenta tu respuesta final de forma clara, profesional y estructurada, usando formato Markdown (títulos, negritas, listas o tablas formateadas).
4. Explica brevemente los hallazgos o conclusiones clave de los datos analizados.
"""


def ask_data_chat(source, prompt: str) -> dict[str, Any]:
    """
    Ejecuta una consulta al chatbot sobre la fuente de datos usando ejecución de código en Gemini.
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

    tmp_path = None
    try:
        # Guardar DataFrame como CSV temporal
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8") as f:
            df.to_csv(f, index=False)
            tmp_path = f.name

        cols_preview = ", ".join(list(df.columns)[:40])
        full_prompt = (
            f"Conjunto de datos: '{source.label}'. Total filas: {len(df)}. Columnas: {cols_preview}.\n\n"
            f"Pregunta del usuario:\n{prompt}"
        )

        return generate_with_code_execution(
            file_path=tmp_path,
            prompt=full_prompt,
            system_instruction=SYSTEM_INSTRUCTION,
        )
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
