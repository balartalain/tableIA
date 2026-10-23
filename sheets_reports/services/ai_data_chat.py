"""
Chatbot analítico sobre la fuente de datos del tablero usando Gemini y ejecución de código Python.
Sube a Google la hoja original de la fuente (sin su configuración de columnas ni campos
calculados) y reutiliza el archivo mientras siga activo y la hoja no cambie; permite hacer
preguntas analíticas, ejecutar cálculos en pandas y generar gráficos con matplotlib/seaborn.
"""
from __future__ import annotations

import hashlib
import logging
import os
import tempfile
from typing import Any

from django.conf import settings

from sheets_reports.engine.context import SheetContext
from sheets_reports.services.ai_spec import (
    SpecGenerationError,
    columns_context,
    gemini_delete_file,
    gemini_get_file,
    gemini_upload_file,
    generate_with_code_execution,
)
from sheets_reports.services.sheets import source_frame

logger = logging.getLogger(__name__)

SYSTEM_INSTRUCTION = """\
Eres un asistente analítico experto de datos en español para un tablero de control.
El usuario te hará preguntas sobre el conjunto de datos cargado en tu entorno de trabajo (archivo CSV).
Se te proporciona el perfil de las columnas de la hoja, indicando su escala de medida (numéricas, fechas, categorías o columnas de "texto libre" para respuestas abiertas, comentarios, opiniones o sugerencias).

Instrucciones:
1. ANÁLISIS CUANTITATIVO Y ESTADÍSTICO:
   - Para responder preguntas sobre cifras, métricas, promedios, agregaciones o comparaciones numéricas, escribe y ejecuta código Python con `pandas` y `numpy` para obtener valores exactos e inalterados a partir del CSV.

2. ANÁLISIS CUALITATIVO Y DE TEXTO LIBRE:
   - Para preguntas sobre comentarios, opiniones, aspectos positivos/negativos, temas de queja, sugerencias o análisis de texto abierto:
     a) Usa Python con `pandas` para seleccionar la columna de texto libre no nula y listar sus textos o una muestra amplia (ejemplo: `print(df['comentario'].dropna().tolist())`).
     b) Emplea tu capacidad nativa de comprensión de lenguaje natural sobre las respuestas impresas para sintetizar y clasificar cualitativamente los hallazgos: agrupa patrones de opinión, identifica aspectos negativos/positivos principales y resume las conclusiones clave.
     c) NO intentes realizar solo conteos exactos de cadenas (`value_counts()`) sobre texto libre, ya que las respuestas abiertas varían en redacción y requieren interpretación semántica.

3. GRÁFICOS:
   - Si la respuesta amerita un gráfico (o el usuario lo pide), crea el gráfico con `matplotlib.pyplot` o `seaborn`: prepara los datos primero y dibuja la figura en un único paso. Guárdalo obligatoriamente en disco con `plt.savefig('grafico.png', bbox_inches='tight')` y CIERRA SIEMPRE todas las figuras con `plt.close('all')` para evitar la emisión de lienzos en blanco o gráficos duplicados (con fondo limpio, etiquetas claras en español y título descriptivo).

4. PRESENTACIÓN:
   - Presenta tu respuesta final de forma clara, profesional y estructurada usando formato Markdown (títulos, negritas, viñetas o tablas). No incluyas sintaxis de imágenes Markdown (como `![...](...)`) en tu texto explicativo, ya que los gráficos generados se muestran automáticamente.

5. REGLA ESTRICTA DE COMUNICACIÓN:
   - Ve DIRECTO a las respuestas, cifras, síntesis cualitativa y conclusiones. NUNCA narres tus intenciones técnicas ni digas frases como "Para responder...", "Ejecutaré el siguiente código...", "Primero cargaré el archivo CSV...". Entrega únicamente los resultados y conclusiones finales.
"""


def _source_csv(df) -> tuple[str, str]:
    """El CSV que se sube a Gemini y su huella (sha256), para saber si la hoja cambió."""
    csv = df.to_csv(index=False)
    return csv, hashlib.sha256(csv.encode("utf-8")).hexdigest()


def _current_file(source, digest: str) -> Any:
    """El archivo de la fuente en Gemini si tiene estos mismos datos y sigue activo (Gemini lo
    borra a las 48 h); None si hay que subirlo."""
    file_id = (getattr(source, "gemini_file_id", "") or "").strip()
    if not file_id or getattr(source, "gemini_file_hash", "") != digest:
        return None
    return gemini_get_file(file_id)


def get_or_upload_source_file(source, df) -> Any:
    """La referencia del archivo de la fuente en Gemini Files API: el que ya está si sigue
    activo y la hoja no cambió; si no, sube la hoja."""
    csv, digest = _source_csv(df)
    return _current_file(source, digest) or _upload_source_file(source, csv, digest)


def _upload_source_file(source, csv: str, digest: str) -> Any:
    """Sube el CSV a Gemini Files API, guarda el nuevo ID y su huella en la fuente y borra el
    archivo anterior. Sube antes de borrar: el chat nunca queda sin archivo."""
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8") as f:
            f.write(csv)
            tmp_path = f.name

        display_name = f"source_{getattr(source, 'id', 'data')}.csv"
        file_ref = gemini_upload_file(tmp_path, display_name=display_name)
        new_file_id = getattr(file_ref, "name", "")
        old_id = (getattr(source, "gemini_file_id", "") or "").strip()
        if new_file_id and hasattr(source, "save") and getattr(source, "pk", None):
            source.gemini_file_id = new_file_id
            source.gemini_file_hash = digest
            source.save(update_fields=["gemini_file_id", "gemini_file_hash"])
        if old_id and old_id != new_file_id:
            gemini_delete_file(old_id)
        return file_ref
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def resync_source_file(source) -> str:
    """Lo que hace cada noche `sync_gemini_files` con una fuente: la sube si su archivo expiró o
    la hoja cambió → "uploaded"; si sigue vigente no sube nada → "current"; una hoja vacía solo
    pierde su archivo → "empty"."""
    df = source_frame(source)
    if df.empty:
        delete_source_gemini_file(source)
        return "empty"
    csv, digest = _source_csv(df)
    if _current_file(source, digest):
        return "current"
    _upload_source_file(source, csv, digest)
    return "uploaded"


def delete_source_gemini_file(source) -> None:
    """Elimina el archivo asociado a la fuente en Gemini Files API si existe y limpia su ID."""
    file_id = getattr(source, "gemini_file_id", "")
    if not (isinstance(file_id, str) and file_id.strip()):
        return

    gemini_delete_file(file_id.strip())

    if hasattr(source, "save") and getattr(source, "pk", None):
        source.gemini_file_id = ""
        source.gemini_file_hash = ""
        source.save(update_fields=["gemini_file_id", "gemini_file_hash"])


def ask_data_chat(source, prompt: str) -> dict[str, Any]:
    """
    Ejecuta una consulta al chatbot sobre la fuente de datos usando ejecución de código en Gemini.
    Sube la hoja a Gemini únicamente si no se ha subido, si expiró o si cambió.
    Devuelve:
      {
        "text": "...",
        "images": ["data:image/png;base64,..."],
        "code": ["código python ejecutado..."]
      }
    """
    df = source_frame(source)
    if df.empty:
        return {"text": "La fuente de datos está vacía.", "images": [], "code": []}

    file_ref = get_or_upload_source_file(source, df)

    gid = getattr(source, "gid", "") or ""
    ctx = SheetContext.from_dataframe(df, gid)
    # El CSV tiene solo las columnas reales: las virtuales de las cuadrículas no se describen.
    cols_info = columns_context(ctx, virtual=False)

    full_prompt = (
        f"Conjunto de datos: '{getattr(source, 'label', 'Datos')}'. Total filas: {len(df)}.\n"
        f"{cols_info}\n\n"
        f"Pregunta del usuario:\n{prompt}"
    )

    return generate_with_code_execution(
        prompt=full_prompt,
        file_ref=file_ref,
        system_instruction=SYSTEM_INSTRUCTION,
    )
