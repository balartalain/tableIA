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
MAX_AVOID = 10
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
- Si comparas periodos, di contra cuál (este año frente al anterior, el último mes frente al
  anterior); nunca «variación anual» o «evolución» sin referencia.
{capability_rules}- Sin términos técnicos (KPI, widget, métrica, condición, pivote, alias).
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


def _capability_rules(widget) -> str:
    """Reglas que salen de lo que el widget exige (ej. un gráfico siempre agrupa por una
    columna: «ventas del último mes» a secas no se puede dibujar como barras)."""
    dimensions = (widget.capabilities or {}).get("dimensions") or [0, 0]
    if dimensions[0] >= 1:
        return ("- Este widget siempre agrupa por una columna: cada pedido dice por cuál (ej.\n"
                "  «ventas por categoría», «… por vendedor: último mes frente al anterior»).\n")
    return ""


def _cache_key(source: str, widget_type: str, prompt: str, preview: str) -> str:
    """Si cambian las columnas, sus tipos o las primeras filas, cambia la clave. Si cambia el
    prompt, también cambia la clave."""
    digest = hashlib.sha1((prompt + preview).encode("utf-8")).hexdigest()[:16]
    return f"widget_suggestions:{source}:{widget_type}:{digest}"


def _ask_model(contents: str) -> object:
    # Algo de temperatura: que no sean siempre las mismas frases.
    return generate_json(contents, {"type": "array", "items": {"type": "string"}}, temperature=0.7)


def _clean(raw, avoid: list[str] | None = None) -> list[str]:
    """Textos no vacíos, de largo razonable, sin repetir y distintos de `avoid` (sin distinguir
    mayúsculas); como mucho `SUGGESTIONS`."""
    seen = {a.lower() for a in avoid or []}
    result: list[str] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, str):
            continue
        text = item.strip().strip('"«»').strip()
        text = text[:1].upper() + text[1:]
        if text and len(text) <= MAX_SUGGESTION_CHARS and text.lower() not in seen:
            seen.add(text.lower())
            result.append(text)
    return result[:SUGGESTIONS]


def widget_suggestions(widget, df: pd.DataFrame, source: str, refresh: bool = False,
                       avoid: list[str] | None = None) -> list[str]:
    """Dos pedidos sugeridos para `widget` sobre la hoja `df` (`source` la identifica en la
    caché). Si la IA falla devuelve [] y no lo cachea: se reintenta en la próxima apertura.

    `refresh` («otras ideas» en el panel) no lee la caché y reemplaza lo guardado con las
    nuevas; `avoid` son las que el usuario ya ve: la IA no debe repetirlas."""
    preview = _sheet_preview(df)
    # Los pedidos de los ejemplos del widget marcan el tono (son de otra hoja: no sus columnas).
    examples = [f"- {p}" for p, _args in widget.ai_examples]
    prompt = SUGGESTIONS_PROMPT.format(
        n=SUGGESTIONS, label=widget.label,
        doc=f"{widget.ai_doc}\n" if widget.ai_doc else "",
        capabilities=capabilities_text(widget),
        examples=("Así escriben los usuarios (de otra hoja, no copies sus columnas):\n"
                  + "\n".join(examples) + "\n") if examples else "",
        capability_rules=_capability_rules(widget),
    )
    key = _cache_key(source, widget.key, prompt, preview)
    if not refresh:
        cached = cache.get(key)
        if cached is not None:
            return cached
    avoid = [str(a).strip()[:MAX_SUGGESTION_CHARS] for a in (avoid or []) if str(a).strip()][:MAX_AVOID]
    if avoid:
        prompt += ("\n- No repitas estas ni propongas variantes casi iguales:\n"
                   + "\n".join(f"  - {a}" for a in avoid))
    try:
        suggestions = _clean(_ask_model(f"{prompt}\n\n{preview}"), avoid)
    except Exception:  # noqa: BLE001 - sin sugerencias el chat funciona igual
        logger.exception("Sugerencias de la IA (widget_type=%s)", widget.key)
        return []
    if suggestions:
        cache.set(key, suggestions, CACHE_TTL_SECONDS)
    return suggestions
