"""
Las columnas de la hoja que usa cada widget. Un solo recorrido de `fields` y `style` que
comparten:
  - el renombrado de columnas de una fuente (los widgets siguen a su columna),
  - el aviso de impacto antes de excluir columnas, cambiarles el tipo o reemplazar la hoja,
  - la propuesta de la IA (nombres que solo difieren en espacios → el nombre real).
"""
from __future__ import annotations

import copy
from typing import Callable, Iterable


def map_columns(fields: dict, style: dict, fn: Callable[[str], str]) -> tuple[dict, dict]:
    """Copias de `fields` y `style` con `fn` aplicada a cada referencia a una columna de la hoja.
    `fn` recibe solo textos; lo que no es texto queda igual. `sort_by` conserva su «-» y puede
    ser un alias de métrica: `fn` decide si lo cambia."""
    fields = copy.deepcopy(fields) if isinstance(fields, dict) else {}
    style = copy.deepcopy(style) if isinstance(style, dict) else {}

    def apply(value):
        return fn(value) if isinstance(value, str) and value else value

    def conditions(items):
        for condition in items if isinstance(items, list) else []:
            if isinstance(condition, dict) and "field" in condition:
                condition["field"] = apply(condition["field"])

    for key in ("dimensions", "pivots"):
        if isinstance(fields.get(key), list):
            fields[key] = [apply(v) for v in fields[key]]
    if "trend_by" in fields:
        fields["trend_by"] = apply(fields["trend_by"])
    for item in fields.get("columns") or []:
        if isinstance(item, dict) and "field" in item:
            item["field"] = apply(item["field"])
    for metric in fields.get("metrics") or []:
        if isinstance(metric, dict):
            if "field" in metric:
                metric["field"] = apply(metric["field"])
            conditions(metric.get("filters"))
    conditions(fields.get("filters"))
    sort_by = fields.get("sort_by")
    if isinstance(sort_by, str) and sort_by:
        desc = sort_by.startswith("-")
        name = apply(sort_by[1:] if desc else sort_by)
        fields["sort_by"] = f"-{name}" if desc else name

    # Tablas: formato y orden de columnas guardados por nombre de columna.
    if isinstance(style.get("formattersMap"), dict):
        style["formattersMap"] = {apply(k): v for k, v in style["formattersMap"].items()}
    if isinstance(style.get("columnOrder"), list):
        style["columnOrder"] = [apply(v) for v in style["columnOrder"]]
    return fields, style


def column_refs(fields: dict, style: dict | None = None) -> set[str]:
    """Los nombres que el widget usa como columna (incluye `sort_by` y el orden de tabla, que
    pueden ser alias de métricas: quien pregunta los cruza con las columnas de la hoja)."""
    seen: set[str] = set()

    def collect(value: str) -> str:
        seen.add(value)
        return value

    map_columns(fields, style or {}, collect)
    return seen


def rename_in_widgets(widgets: Iterable, mapping: dict[str, str]) -> int:
    """Reescribe los widgets para que sigan a sus columnas renombradas (`viejo → nuevo`).
    Devuelve cuántos cambiaron."""
    if not mapping:
        return 0
    changed = 0
    for widget in widgets:
        fields, style = map_columns(widget.fields, widget.style, lambda name: mapping.get(name, name))
        if fields != (widget.fields or {}) or style != (widget.style or {}):
            widget.fields, widget.style = fields, style
            widget.save(update_fields=["fields", "style", "updated_at"])
            changed += 1
    return changed


def impact(widgets: Iterable, current: set[str], available: set[str],
           retyped: set[str] = frozenset()) -> list[dict]:
    """Widgets que se rompen con un cambio de columnas: los que usan una columna que hoy existe
    (`current`) y después no (`available`), o cuyo tipo cambia (`retyped`).
    → [{id, title, columns: [...]}], en el orden de los widgets."""
    out = []
    for widget in widgets:
        used = column_refs(widget.fields, widget.style) & set(current)
        broken = sorted(c for c in used if c not in available or c in retyped)
        if broken:
            out.append({"id": widget.id, "title": widget.title, "columns": broken})
    return out
