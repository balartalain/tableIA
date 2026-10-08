"""
«Generar con IA»: a partir del pedido del usuario («un tablero de ventas mensuales con…»), la
lista de widgets que propone la IA: tipo, título, descripción, ancho y el pedido en lenguaje
natural que configura cada uno. La IA NO arma aquí los `fields`: cada widget aceptado se
configura después con `generate_widget_form` (el mismo chat del panel, ya validado), con su
`request`.
"""
import json
import logging

from sheets_reports.engine.context import SheetContext
from sheets_reports.services.ai_spec import (SpecGenerationError, capabilities_text, columns_context,
                                             generate_json)
from sheets_reports.widgets import WIDGETS

logger = logging.getLogger(__name__)

MAX_ITEMS = 8
MAX_PROMPT_CHARS = 2000
MAX_TITLE_CHARS = 80
MAX_TEXT_CHARS = 400
MIN_WIDTH, MAX_WIDTH, DEFAULT_WIDTH = 3, 12, 6

BOARD_PROMPT = """\
Eres el asistente de un constructor de tableros sobre una hoja de cálculo. El usuario describe
el tablero que quiere; tú propones la lista de widgets que lo forman.

Widgets disponibles (widget_type: para qué sirve. Admite: …):
{catalog}

{columns}

Reglas:
- Entre 2 y {max_items} widgets, en el orden en que se verán (los números clave arriba).
- Solo los widget_type de la lista. Si el usuario pide un tipo que no existe (ej. gráfico de
  área), usa el más parecido y dilo en "note".
- Cada widget usa columnas reales de la hoja. Si algo del pedido no se puede con estas columnas,
  no lo inventes: dilo en "note".
- title: corto, en español, como lo leería quien ve el tablero (ej. «Ventas por categoría»).
- description: una frase que explica qué muestra.
- request: el pedido en lenguaje natural para configurar ese widget, con los nombres exactos de
  las columnas, la agregación, el agrupamiento, el orden, el límite y las condiciones (ej.
  «suma de ventas por categoria, las 5 más altas»). Sin JSON.
- width: columnas de ancho en una grilla de 12 (KPI 3 o 4, gráficos 6, tablas 12).
- note: vacío si se pudo todo; si no, qué cambiaste y por qué, en una frase.
{singletons}
Pedido del usuario:
{prompt}
"""

SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "widget_type": {"type": "string"},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "request": {"type": "string"},
                    "width": {"type": "integer"},
                },
                "required": ["widget_type", "title", "description", "request", "width"],
            },
        },
        "note": {"type": "string"},
    },
    "required": ["items"],
}


def _widgets():
    return [w for w in (WIDGETS.get(k) for k in WIDGETS.keys()) if w.ai_enabled]


def _catalog() -> str:
    return "\n".join(f"- {w.key}: {w.ai_doc} Admite: {capabilities_text(w)}." for w in _widgets())


def _text(value, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _width(value) -> int:
    try:
        width = int(value)
    except (TypeError, ValueError):
        return DEFAULT_WIDTH
    return max(MIN_WIDTH, min(MAX_WIDTH, width))


def clean_plan(raw, existing_types=()) -> dict:
    """Lo que devolvió la IA, solo con lo que el tablero puede crear: tipos registrados con IA,
    a lo más uno de un tipo único por tablero (ninguno si ya está), título y pedido no vacíos y
    el ancho dentro de la grilla. Sin widgets → SpecGenerationError."""
    allowed = {w.key: w for w in _widgets()}
    taken = {t for t in existing_types if (allowed.get(t) and allowed[t].max_per_dashboard == 1)}
    if not isinstance(raw, dict):
        raw = {}
    items = []
    for item in raw.get("items") or []:
        if not isinstance(item, dict):
            continue
        widget = allowed.get(str(item.get("widget_type") or "").strip())
        title = _text(item.get("title"), MAX_TITLE_CHARS)
        request = _text(item.get("request"), MAX_TEXT_CHARS)
        if not widget or not request:
            continue
        if widget.max_per_dashboard == 1:
            if widget.key in taken:
                continue
            taken.add(widget.key)
        items.append({
            "widget_type": widget.key,
            "title": title or widget.label,
            "description": _text(item.get("description"), MAX_TEXT_CHARS),
            "request": request,
            "width": MAX_WIDTH if widget.max_per_dashboard == 1 else _width(item.get("width")),
        })
        if len(items) == MAX_ITEMS:
            break
    if not items:
        raise SpecGenerationError("La IA no propuso widgets para ese pedido. Prueba a describir qué "
                                  "quieres ver con los nombres de las columnas.")
    return {"items": items, "note": _text(raw.get("note"), MAX_TEXT_CHARS)}


def propose_board(prompt: str, ctx: SheetContext, existing_types=()) -> dict:
    """`{items: [{widget_type, title, description, request, width}], note}` para el pedido."""
    prompt = str(prompt or "").strip()[:MAX_PROMPT_CHARS]
    if not prompt:
        raise SpecGenerationError("Describe el tablero que quieres.")
    if not ctx.fields:
        raise SpecGenerationError("La hoja no tiene columnas.")
    singles = [w.key for w in _widgets() if w.max_per_dashboard == 1]
    taken = [k for k in singles if k in set(existing_types)]
    contents = BOARD_PROMPT.format(
        catalog=_catalog(), columns=columns_context(ctx), max_items=MAX_ITEMS,
        singletons=(f"- El tablero ya tiene: {', '.join(taken)}. No los propongas.\n" if taken else ""),
        prompt=prompt,
    )
    raw = generate_json(contents, SCHEMA)
    logger.info("Plan de tablero con IA: %s", json.dumps(raw, ensure_ascii=False)[:2000])
    return clean_plan(raw, existing_types)
