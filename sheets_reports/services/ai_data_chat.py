"""
Chatbot analítico sobre la fuente de datos del tablero usando Gemini y ejecución de código Python.
Por cada pregunta, una llamada corta elige las columnas que hacen falta; solo esas (con todas las
filas) se suben a Gemini Files API para la consulta, que ejecuta cálculos en pandas y genera
gráficos con matplotlib/seaborn, y el archivo se borra al terminar. La hoja es la original de la
fuente, con los nombres a mostrar de sus columnas (más cortos que los encabezados de un
formulario).
"""
from __future__ import annotations

import logging
import os
import tempfile
from typing import Any


from sheets_reports.engine.context import SheetContext
from sheets_reports.services.ai_spec import (
    AITimeoutError,
    columns_context,
    gemini_delete_file,
    gemini_upload_file,
    generate_json,
    generate_with_code_execution,
)
from sheets_reports.services.sheets import column_display_name, get_field_samples, source_frame
from sheets_reports.utils.data import is_id_column

logger = logging.getLogger(__name__)

SYSTEM_INSTRUCTION = """\
Eres un asistente analítico experto de datos en español para un tablero de control.
El usuario te hará preguntas sobre el conjunto de datos cargado en tu entorno de trabajo (archivo CSV).
Se te proporcionan las columnas de la hoja con su tipo (numérica, fecha o texto) y valores de ejemplo.

Instrucciones:
1. ANÁLISIS CUANTITATIVO Y ESTADÍSTICO:
   - Para responder preguntas sobre cifras, métricas, promedios, agregaciones o comparaciones numéricas, escribe y ejecuta código Python con `pandas` y `numpy` para obtener valores exactos e inalterados a partir del CSV.

2. ANÁLISIS CUALITATIVO Y DE TEXTO LIBRE:
   - Para preguntas sobre comentarios, opiniones, aspectos positivos/negativos, temas de queja, sugerencias o análisis de texto abierto:
     a) Emplea tu capacidad nativa de comprensión de lenguaje natural sobre las respuestas para sintetizar y clasificar cualitativamente los hallazgos: agrupa patrones de opinión, identifica aspectos negativos/positivos principales y resume las conclusiones clave.
     b) NO intentes realizar solo conteos exactos de cadenas (`value_counts()`) sobre texto libre, ya que las respuestas abiertas varían en redacción y requieren interpretación semántica.

3. GRÁFICOS:
   - Si la respuesta amerita un gráfico (o el usuario lo pide), crea el gráfico con `matplotlib.pyplot` o `seaborn`: prepara los datos primero y dibuja la figura en un único paso. Guárdalo obligatoriamente en disco con `plt.savefig('grafico.png', bbox_inches='tight')` y CIERRA SIEMPRE todas las figuras con `plt.close('all')` para evitar la emisión de lienzos en blanco o gráficos duplicados (con fondo limpio, etiquetas claras en español y título descriptivo).

4. PRESENTACIÓN:
   - Presenta tu respuesta final de forma clara, profesional y estructurada usando formato Markdown (títulos, negritas, viñetas o tablas). No incluyas sintaxis de imágenes Markdown (como `![...](...)`) en tu texto explicativo, ya que los gráficos generados se muestran automáticamente.

5. REGLA ESTRICTA DE COMUNICACIÓN:
   - Ve DIRECTO a las respuestas, cifras, síntesis cualitativa y conclusiones. NUNCA narres tus intenciones técnicas ni digas frases como "Para responder...", "Ejecutaré el siguiente código...", "Primero cargaré el archivo CSV...". Entrega únicamente los resultados y conclusiones finales.
"""


# Columnas que puede usar una pregunta: con más, se le pide al usuario que la acote.
MAX_CHAT_COLUMNS = 15

TOO_MANY_COLUMNS = ("Tu pregunta abarca demasiadas columnas. Acótala: sé más específico sobre qué "
                    "quieres comparar o analizar.")
NO_COLUMNS = ("No encontré en los datos las columnas para esa pregunta. Sé más específico: nombra "
              "las preguntas o los datos que quieres analizar.")
TIMEOUT = ("La pregunta tomó demasiado tiempo. Prueba a acotarla (una comparación, unas pocas "
           "preguntas del formulario).")

COLUMNS_PROMPT = """\
Un usuario hace una pregunta sobre una hoja de datos. Elige las columnas de la hoja que hacen falta
para responderla: las que se comparan, agrupan, filtran o analizan, y nada más. Devuelve sus alias
(col_1, col_2, …). La columna ID no hace falta.

{columns}

Pregunta del usuario:
{prompt}
"""

COLUMNS_SCHEMA = {
    "type": "object",
    "properties": {"columns": {"type": "array", "items": {"type": "string"}}},
    "required": ["columns"],
}


def _answer(text: str) -> dict[str, Any]:
    return {"text": text, "images": [], "code": []}


def _with_display_names(df, columns_config) -> Any:
    """La hoja con los nombres a mostrar de la fuente (los que acortó la IA). Si dos columnas
    acabarían con el mismo nombre, esas conservan el encabezado de la hoja."""
    names = {c["name"]: column_display_name(c) for c in columns_config or [] if c.get("name") in df.columns}
    taken = [names.get(c, c) for c in df.columns]
    rename = {c: n for c, n in names.items() if n != c and taken.count(n) == 1}
    return df.rename(columns=rename)


def _chat_columns(prompt: str, ctx: SheetContext) -> list[str]:
    """Las columnas que la IA dice que hacen falta para la pregunta, con su nombre real: sin la
    ID, sin repetir y solo las que existen."""
    raw = generate_json(COLUMNS_PROMPT.format(columns=columns_context(ctx, virtual=False, scales=False), prompt=prompt),
                        COLUMNS_SCHEMA, call="data_chat_columns", thinking_budget=0)
    columns = []
    for alias in (raw.get("columns") if isinstance(raw, dict) else None) or []:
        field = ctx.field_for(str(alias).strip())
        if field in ctx.sheet_fields and not is_id_column(field) and field not in columns:
            columns.append(field)
    return columns


def _upload_csv(df, display_name: str) -> Any:
    """Sube `df` como CSV a Gemini Files API (el archivo temporal local se borra enseguida)."""
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8") as f:
            df.to_csv(f, index=False)
            tmp_path = f.name
        return gemini_upload_file(tmp_path, display_name=display_name)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def ask_data_chat(source, prompt: str) -> dict[str, Any]:
    """
    Responde una pregunta sobre la fuente de datos con ejecución de código en Gemini, con solo
    las columnas que necesita. Devuelve:
      {
        "text": "...",
        "images": ["data:image/png;base64,..."],
        "code": ["código python ejecutado..."]
      }
    Si la pregunta abarca demasiadas columnas o ninguna, o la consulta pasa del tiempo máximo,
    `text` lo explica.
    """
    df = _with_display_names(source_frame(source), getattr(source, "columns", None))
    if df.empty:
        return _answer("La fuente de datos está vacía.")

    gid = getattr(source, "gid", "") or ""
    ctx = SheetContext.from_dataframe(df, gid, samples=get_field_samples(df))
    columns = _chat_columns(prompt, ctx)
    if not columns:
        return _answer(NO_COLUMNS)
    if len(columns) > MAX_CHAT_COLUMNS:
        return _answer(TOO_MANY_COLUMNS)

    ids = [c for c in df.columns if is_id_column(c)]
    subset = df[[*ids, *columns]]
    # El CSV tiene solo las columnas reales: las virtuales de las cuadrículas no se describen.
    full_prompt = (
        f"Conjunto de datos: '{getattr(source, 'label', 'Datos')}'. Total filas: {len(subset)}.\n"
        f"{columns_context(ctx, only=columns, virtual=False, scales=False)}\n\n"
        f"Pregunta del usuario:\n{prompt}"
    )
    file_ref = _upload_csv(subset, f"chat_source_{getattr(source, 'id', 'data')}.csv")
    try:
        return generate_with_code_execution(
            prompt=full_prompt,
            file_ref=file_ref,
            system_instruction=SYSTEM_INSTRUCTION,
        )
    except AITimeoutError:
        logger.warning("El chat de datos pasó del tiempo máximo: %s", prompt[:200])
        return _answer(TIMEOUT)
    finally:
        gemini_delete_file(getattr(file_ref, "name", ""))
