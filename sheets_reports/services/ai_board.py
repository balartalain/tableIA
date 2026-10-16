"""
«Generar con IA»: a partir del pedido del usuario («un tablero de ventas mensuales con…»), la
lista de widgets que propone la IA: tipo, título, descripción, ancho y el pedido en lenguaje
natural que configura cada uno. La IA NO arma aquí los `fields`: cada widget aceptado se
configura después con `generate_widget_form` (el mismo chat del panel, ya validado), con su
`request`.
"""
import json
import logging
import re

from sheets_reports.engine.context import SheetContext
from sheets_reports.services.ai_spec import (
    BI_DATA_RULES,
    THINKING_BUDGETS,
    SpecGenerationError,
    _normalize,
    _omit_unchanged_scales,
    _resolve_columns,
    capabilities_text,
    columns_context,
    form_errors,
    generate_json,
)
from sheets_reports.utils.data import is_id_column
from sheets_reports.widgets import WIDGETS

logger = logging.getLogger(__name__)

MAX_ITEMS = 15
MAX_PROMPT_CHARS = 2000
MAX_TITLE_CHARS = 80
MAX_TEXT_CHARS = 400

BOARD_PROMPT = """\
Eres el asistente de un constructor de tableros sobre una hoja de cálculo. El usuario describe
el tablero que quiere o los widgets que desea añadir; tú propones la lista de widgets que lo forman.

Widgets disponibles (widget_type: para qué sirve. Admite: …):
{catalog}

{columns}

{data_rules}

Reglas:
- Entre 1 y {max_items} widgets, en el orden en que se verán (los números clave arriba). Si el usuario pide algo puntual (ej. un gráfico específico), propón únicamente lo solicitado; si pide un tablero completo, propón un conjunto balanceado (entre 3 y 8 widgets).
- Solo los widget_type de la lista. Si el usuario pide un tipo que no existe (ej. gráfico de
  área), usa el más parecido y dilo en "note".
- Cada widget usa columnas reales de la hoja. Si algo del pedido no se puede con estas columnas,
  no lo inventes: dilo en "note".
- Si una columna está marcada como «fecha y hora en que se envió la respuesta», no es una fecha
  de los datos: no propongas evolución en el tiempo ni comparación de periodos con ella.
- title: corto, en español, como lo leería quien ve el tablero (ej. «Ventas por categoría»).
- description: una frase que explica qué muestra.
- request: el pedido en lenguaje natural para configurar ese widget, con los nombres exactos de
  las columnas, la agregación, el agrupamiento, el orden, el límite y las condiciones (ej.
  «suma de ventas por categoria, las 5 más altas»). Sin JSON.
- columns: los alias de TODAS las columnas que ese widget necesita: por las que agrupa, las que
  calcula, las de sus condiciones y la de tiempo si compara periodos (ej. ["col_3", "col_7"]).
  El widget se configura viendo solo esas columnas: no omitas ninguna. En un "filter" son las
  columnas por las que filtrar.
- note: vacío si se pudo todo; si no, qué cambiaste y por qué, en una frase.
{singletons}
{existing_context}
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
                    "columns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Alias (ej. col_1, col_2) de todas las columnas que usa el widget.",
                    },
                },
                "required": ["widget_type", "title", "description", "request", "columns"],
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


def _plan_columns(raw, ctx: SheetContext) -> list[str]:
    """Las columnas que la IA dijo que usa un widget (alias o nombres), con su nombre real: sin
    la ID, sin repetir y solo las que existen."""
    cols = []
    for c in raw if isinstance(raw, list) else []:
        field = ctx.field_for(str(c).strip())
        if field in ctx.fields and not is_id_column(field) and field not in cols:
            cols.append(field)
    return cols


def clean_plan(raw, existing_types=(), ctx: SheetContext | None = None) -> dict:
    """Lo que devolvió la IA, solo con lo que el tablero puede crear: tipos registrados con IA,
    a lo más uno de un tipo único por tablero (ninguno si ya está), título y pedido no vacíos.
    `columns` son las columnas de la hoja que usa cada widget (su asistente solo ve esas).
    Para 'filter', si se especifican columnas, se resuelven en `fields.dimensions`.
    Sin widgets → SpecGenerationError."""
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
        entry = {
            "widget_type": widget.key,
            "title": title or widget.label,
            "description": _text(item.get("description"), MAX_TEXT_CHARS),
            "request": request,
        }
        cols = _plan_columns(item.get("columns"), ctx) if ctx is not None else []
        if cols:
            entry["columns"] = cols
        if widget.key == "filter" and ctx is not None:
            cols = list(cols)
            if not cols:
                for alias in re.findall(r"\bcol_\d+\b", request, re.IGNORECASE):
                    field = ctx.field_for(alias)
                    if field in ctx.fields and not is_id_column(field) and field not in cols:
                        cols.append(field)
            if not cols:
                for f in ctx.fields:
                    if not is_id_column(f) and re.search(r"\b" + re.escape(f) + r"\b", request, re.IGNORECASE) and f not in cols:
                        cols.append(f)
            if cols:
                entry["fields"] = {"dimensions": cols}
        items.append(entry)
        if len(items) == MAX_ITEMS:
            break
    if not items:
        raise SpecGenerationError("La IA no propuso widgets para ese pedido. Prueba a describir qué "
                                  "quieres ver con los nombres de las columnas.")
    return {"items": items, "note": _text(raw.get("note"), MAX_TEXT_CHARS)}


def propose_board(prompt: str, ctx: SheetContext, existing_types=(), existing_widgets=()) -> dict:
    """`{items: [{widget_type, title, description, request, width}], note}` para el pedido."""
    prompt = str(prompt or "").strip()[:MAX_PROMPT_CHARS]
    if not prompt:
        raise SpecGenerationError("Describe el tablero que quieres.")
    if not ctx.fields:
        raise SpecGenerationError("La hoja no tiene columnas.")
    singles = [w.key for w in _widgets() if w.max_per_dashboard == 1]
    taken = [k for k in singles if k in set(existing_types)]

    existing_text = ""
    if existing_widgets:
        lines = [f"  * {w.get('title') or w.get('type')} ({w.get('type')})" for w in existing_widgets if isinstance(w, dict)]
        if lines:
            existing_text = (
                "- Widgets ya presentes en el tablero:\n"
                + "\n".join(lines)
                + "\n  * Regla sobre lo existente: No dupliques gráficos ni métricas idénticas por iniciativa propia al complementar el tablero. "
                "Sin embargo, si el usuario pide EXPLÍCITAMENTE un gráfico similar, variante o con diferente enfoque de algo que ya existe, atiende su petición.\n"
            )

    contents = BOARD_PROMPT.format(
        catalog=_catalog(), columns=columns_context(ctx), data_rules=BI_DATA_RULES, max_items=MAX_ITEMS,
        singletons=(f"- El tablero ya tiene: {', '.join(taken)}. No los propongas.\n" if taken else ""),
        existing_context=existing_text,
        prompt=prompt,
    )
    raw = generate_json(contents, SCHEMA, call="board_plan",
                        thinking_budget=THINKING_BUDGETS["board_plan"])
    logger.info("Plan de tablero con IA: %s", json.dumps(raw, ensure_ascii=False)[:2000])
    return clean_plan(raw, existing_types, ctx=ctx)


BATCH_BUILD_PROMPT = """\
Eres el compilador técnico de widgets para un tablero de control sobre una hoja de cálculo.
Tu tarea es compilar las especificaciones técnicas completas (`fields` y `style`) de cada uno de los widgets solicitados a continuación.

Widgets disponibles (widget_type: para qué sirve. Admite: …):
{catalog}

{columns}

{data_rules}

Instrucciones:
1. Devuelve la lista `items` con exactamente los widgets pedidos, en el mismo orden.
2. Cada elemento debe incluir:
   - "widget_type": el tipo del widget solicitado.
   - "fields": dimensiones, métricas (con agg y alias en snake_case; opcional label, filters y window), pivotes, orden (sort_by), límite (limit).
   - "style": diccionario con opciones de apariencia según el widget.
   - "calculated_fields": si el pedido requiere un cálculo entre totales (ej. % de cumplimiento, diferencia, margen), defínelo aquí y úsalo con agg="auto" en las métricas.
   - "column_scales": correcciones de escalas (ej. ordinal) solo si aplica.
3. Usa SIEMPRE los nombres y alias reales de las columnas indicadas en la lista de columnas.
4. Para campos calculados, la fórmula debe ser agregada: SUM([Columna]), AVG([Columna]), COUNT([Columna]), IF(condición, sí, no).

Widgets solicitados a compilar:
{widgets_requested}
"""

BATCH_ITEM_SCHEMA = {
    "type": "object",
    "required": ["widget_type", "fields"],
    "properties": {
        "widget_type": {"type": "string"},
        "fields": {
            "type": "object",
            "properties": {
                "dimensions": {"type": "array", "items": {"type": "string"}},
                "metrics": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["agg", "alias"],
                        "properties": {
                            "agg": {"type": "string"},
                            "field": {"type": "string"},
                            "alias": {"type": "string"},
                            "label": {"type": "string"},
                            "filters": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "required": ["field", "op"],
                                    "properties": {
                                        "field": {"type": "string"},
                                        "op": {"type": "string"},
                                        "value": {},
                                        "relative": {"type": "string"},
                                    },
                                },
                            },
                            "window": {
                                "type": "object",
                                "properties": {"type": {"type": "string"}},
                            },
                        },
                    },
                },
                "pivots": {"type": "array", "items": {"type": "string"}},
                "filters": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["field", "op"],
                        "properties": {
                            "field": {"type": "string"},
                            "op": {"type": "string"},
                            "value": {},
                            "relative": {"type": "string"},
                        },
                    },
                },
                "columns": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "field": {"type": "string"},
                            "label": {"type": "string"},
                        },
                    },
                },
                "sort_by": {"type": "string"},
                "limit": {"type": "integer"},
                "trend_by": {"type": "string"},
            },
        },
        "style": {"type": "object"},
        "calculated_fields": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name", "formula", "format"],
                "properties": {
                    "name": {"type": "string"},
                    "formula": {"type": "string"},
                    "format": {"type": "string"},
                },
            },
        },
        "column_scales": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["field", "scale"],
                "properties": {
                    "field": {"type": "string"},
                    "scale": {"type": "string"},
                    "order": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
    },
}

BATCH_BUILD_SCHEMA = {
    "type": "object",
    "required": ["items"],
    "properties": {
        "items": {
            "type": "array",
            "items": BATCH_ITEM_SCHEMA,
        },
    },
}


def build_board_batch(items: list[dict], ctx: SheetContext) -> list[dict]:
    """Compila en una sola llamada a la IA las especificaciones técnicas (fields, style) de todos los widgets."""
    if not items:
        return []
    if not ctx.fields:
        raise SpecGenerationError("La hoja no tiene columnas.")

    widgets_lines = []
    for idx, item in enumerate(items, 1):
        w_type = item.get("widget_type") or "bar"
        title = item.get("title") or f"Widget {idx}"
        req = item.get("request") or title
        cols = ", ".join(item.get("columns") or [])
        line = f"{idx}. Tipo: '{w_type}'. Título: '{title}'. Pedido: '{req}'."
        if cols:
            line += f" Columnas sugeridas: {cols}."
        widgets_lines.append(line)

    prompt = BATCH_BUILD_PROMPT.format(
        catalog=_catalog(),
        columns=columns_context(ctx),
        data_rules=BI_DATA_RULES,
        widgets_requested="\n".join(widgets_lines),
    )

    raw = generate_json(
        prompt, BATCH_BUILD_SCHEMA, call="board_batch_build",
        thinking_budget=THINKING_BUDGETS.get("board_batch_build", 0),
    )

    raw_items = raw.get("items") or []
    compiled_results = []
    current_ctx = ctx
    for idx, req_item in enumerate(items):
        compiled = raw_items[idx] if idx < len(raw_items) and isinstance(raw_items[idx], dict) else {}
        w_type = req_item.get("widget_type") or compiled.get("widget_type")
        normalized = _resolve_columns(_normalize(compiled), current_ctx)
        errors = form_errors(normalized, current_ctx, w_type, require_title=False)
        if not errors:
            _omit_unchanged_scales(normalized, current_ctx)
            new_calcs = normalized.get("calculated_fields") or []
            if new_calcs:
                import dataclasses
                current_ctx = dataclasses.replace(
                    current_ctx,
                    aggregated_fields=current_ctx.aggregated_fields | {f["name"] for f in new_calcs if f.get("name")}
                )
            compiled_results.append({
                "widget_type": w_type,
                "title": req_item.get("title", ""),
                "width": req_item.get("width"),
                "fields": normalized.get("fields"),
                "style": normalized.get("style", {}),
                "calculated_fields": normalized.get("calculated_fields", []),
                "column_scales": normalized.get("column_scales", []),
            })
        else:
            logger.info("Widget batch %s (%s) requiere compilación individual por errores: %s", idx + 1, w_type, errors)
            compiled_results.append({
                "widget_type": w_type,
                "title": req_item.get("title", ""),
                "width": req_item.get("width"),
                "fields": None,
                "style": {},
                "calculated_fields": [],
                "column_scales": [],
            })
    return compiled_results
