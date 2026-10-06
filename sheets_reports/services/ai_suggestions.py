"""
Sugerencias de pedidos para el chat del panel: dos frases cortas, como las escribiría el
usuario, pensadas para el tipo de widget a partir de las cabeceras y las primeras filas de la
hoja. Se cachean por hoja, tipo y esa vista previa: abrir el panel de nuevo no vuelve a llamar
a la IA.
"""
import hashlib
import json
import logging

import pandas as pd
from django.core.cache import cache

from sheets_reports.services.ai_spec import capabilities_text, generate_json

logger = logging.getLogger(__name__)

SUGGESTIONS = 2
PREVIEW_ROWS = 3
MAX_SUGGESTION_CHARS = 80
CACHE_TTL_SECONDS = 24 * 60 * 60

SUGGESTIONS_PROMPT = """\
Eres el asistente de un constructor de tableros sobre una hoja de cálculo. Propón {n} pedidos
que un usuario escribiría en el chat para configurar un widget «{label}».
{doc}Este widget admite: {capabilities}.

{examples}
Reglas:
- En español, cortos (máximo 60 caracteres cada uno), en lenguaje natural, sin comillas.
- Usa los nombres reales de las columnas; las filas son solo para entender qué hay en cada una.
- Prefiere los análisis que más se usan: totales, comparaciones, evolución en el tiempo,
  reparto o ranking por una categoría.
- No sugieras un año, una fecha ni un valor suelto. Un valor concreto, solo si aparece en las
  filas y para compararlo con el total o con otro (ej. «ventas de Hogar frente al total»).
- Sin términos técnicos (KPI, widget, métrica, condición, pivote, alias).
- Que el widget los pueda representar y que sean distintos entre sí (otra columna u otro
  enfoque).
- Responde solo con la lista JSON de textos."""


def _sheet_preview(df: pd.DataFrame) -> str:
    """Las cabeceras con su tipo y las primeras filas en CSV: lo que la IA ve de la hoja."""
    numeric = set(df.select_dtypes(include="number").columns)
    columns = ", ".join(f"{json.dumps(str(c), ensure_ascii=False)} ({'numérica' if c in numeric else 'texto'})"
                        for c in df.columns)
    rows = df.head(PREVIEW_ROWS).to_csv(index=False).strip()
    return f"Columnas: {columns}\n\nPrimeras filas:\n{rows}"


def _cache_key(source: str, widget_type: str, preview: str) -> str:
    """Si cambian las columnas, sus tipos o las primeras filas, cambia la clave."""
    digest = hashlib.sha1(preview.encode("utf-8")).hexdigest()[:16]
    return f"widget_suggestions:{source}:{widget_type}:{digest}"


def _ask_model(contents: str) -> object:
    # Algo de temperatura: que no sean siempre las mismas frases.
    return generate_json(contents, {"type": "array", "items": {"type": "string"}}, temperature=0.7)


def _clean(raw) -> list[str]:
    """Textos no vacíos, de largo razonable y sin repetir; como mucho `SUGGESTIONS`."""
    result: list[str] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, str):
            continue
        text = item.strip().strip('"«»').strip()
        text = text[:1].upper() + text[1:]
        if text and len(text) <= MAX_SUGGESTION_CHARS and text.lower() not in {r.lower() for r in result}:
            result.append(text)
    return result[:SUGGESTIONS]


def widget_suggestions(widget, df: pd.DataFrame, source: str) -> list[str]:
    """Dos pedidos sugeridos para `widget` sobre la hoja `df` (`source` la identifica en la
    caché). Si la IA falla devuelve [] y no lo cachea: se reintenta en la próxima apertura."""
    preview = _sheet_preview(df)
    key = _cache_key(source, widget.key, preview)
    cached = cache.get(key)
    if cached is not None:
        return cached

    # Los pedidos de los ejemplos del widget marcan el tono (son de otra hoja: no sus columnas).
    examples = [f"- {p}" for p, _args in widget.ai_examples]
    prompt = SUGGESTIONS_PROMPT.format(
        n=SUGGESTIONS, label=widget.label,
        doc=f"{widget.ai_doc}\n" if widget.ai_doc else "",
        capabilities=capabilities_text(widget),
        examples=("Así escriben los usuarios (de otra hoja, no copies sus columnas):\n"
                  + "\n".join(examples) + "\n") if examples else "",
    )
    try:
        suggestions = _clean(_ask_model(f"{prompt}\n\n{preview}"))
    except Exception:  # noqa: BLE001 - sin sugerencias el chat funciona igual
        logger.exception("Sugerencias de la IA (widget_type=%s)", widget.key)
        return []
    if suggestions:
        cache.set(key, suggestions, CACHE_TTL_SECONDS)
    return suggestions
