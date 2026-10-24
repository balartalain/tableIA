"""
«Generar con IA»: a partir del pedido del usuario («un tablero de ventas mensuales con…»), la
lista de widgets que propone la IA (tipo, título, descripción, el pedido en lenguaje natural de
cada uno y las columnas que usa). Antes de mostrarla, cada widget se configura con el asistente
del widget (`generate_widget_form`: el schema de su tipo y sus reintentos), varios a la vez; los
que no se pueden configurar se quitan. Lo que ve el usuario es lo que se crea, tal cual.
"""
import dataclasses
import logging
import re
from concurrent.futures import ThreadPoolExecutor

from sheets_reports.engine.context import SheetContext
from sheets_reports.services.ai_spec import (
    BI_DATA_RULES,
    THINKING_BUDGETS,
    SpecGenerationError,
    _audit,
    _normalize,
    _omit_unchanged_scales,
    _resolve_columns,
    capabilities_text,
    columns_context,
    form_errors,
    generate_json,
    generate_widget_form,
)
from sheets_reports.utils.data import is_id_column
from sheets_reports.widgets import WIDGETS
from sheets_reports.widgets.schemas import WidgetForm

logger = logging.getLogger(__name__)

MAX_ITEMS = 15
MAX_PROMPT_CHARS = 2000
MAX_TITLE_CHARS = 80
MAX_TEXT_CHARS = 400
# Lo que se muestra de cada error de un widget que se quita del plan.
MAX_DROPPED_ERRORS = 2
# Widgets del plan que se configuran con el asistente a la vez.
BUILD_CONCURRENCY = 5
# Las claves del formulario de un widget.
SPEC_KEYS = ("fields", "style", "calculated_fields", "column_scales")

BOARD_PROMPT = """\
Eres el asistente de un constructor de tableros sobre una hoja de cálculo. El usuario describe
el tablero que quiere o los widgets que desea añadir; tú propones la lista de widgets que lo forman.

Widgets disponibles (widget_type: para qué sirve. Admite: …):
{catalog}

{columns}

{data_rules}

Reglas:
- Entre 1 y {max_items} widgets, en el orden en que se verán (los números clave arriba). Si el usuario pide algo puntual (ej. un gráfico específico), propón únicamente lo solicitado; si pide un tablero completo, propón un conjunto balanceado (entre 3 y 8 widgets).
- Un pedido genérico («un tablero», «los principales indicadores», «un resumen», «lo más
  importante») es un tablero completo: unos pocos números clave (kpi) y, sobre todo, gráficos o
  tablas que muestren distribuciones, comparaciones entre grupos y evolución. «Indicadores» no
  significa solo kpi.
- Un kpi muestra un número que dice algo por sí solo (total de registros, un promedio de una
  columna numérica, un % que cumple una condición). Sobre una pregunta de texto, el kpi es el %
  de una o varias respuestas (ej. «% que respondió Satisfecho o Muy satisfecho»), nunca el conteo
  de sus respuestas: ese número es casi el total de registros y no dice nada de la pregunta.
- Solo los widget_type de la lista. Si el usuario pide un tipo que no existe (ej. gráfico de
  área), usa el más parecido y dilo en "note".
- Cada widget usa columnas reales de la hoja. Si algo del pedido no se puede con estas columnas,
  no lo inventes: dilo en "note".
- El pedido de cada widget cabe en lo que admite su tipo («Admite»: cuántas dimensiones,
  pivotes, métricas o columnas). No le pidas a un widget más de lo que acepta.
- Si la hoja trae columnas virtuales de una cuadrícula o pregunta de casillas («<pregunta> ·
  Aspecto/Opción» y «<pregunta> · Respuesta»), úsalas para esa batería en un solo widget, según
  la nota de cuadrículas. No propongas pedidos que esa nota marca como «No se puede» (ej. un %
  que junte varias respuestas): propón su alternativa y dilo en "note".
- Sin columnas virtuales, varias preguntas de una misma batería o matriz de un formulario (el
  mismo enunciado y un «[…]» distinto) son columnas separadas: no se pueden juntar como si fueran una sola variable,
  ni poner todas como dimensiones o pivotes de un mismo widget. Para mostrar la distribución de
  respuestas de cada una, propón un widget por pregunta (las más relevantes si son muchas, dentro
  del máximo de widgets), aunque el usuario lo haya pedido como un solo widget, y dilo en
  "note". Para compararlas en un solo widget, cada pregunta es una métrica (ej. cuántos
  respondieron cierta opción en cada una, o el promedio si la columna es numérica), dentro del
  máximo de métricas del widget.
  Ej.: «conteo por nivel de interés de 6 tópicos, por área» → 6 tablas dinámicas, cada una
  con área en filas y un tópico en columnas; NO una tabla con los 6 tópicos como pivotes.
- Si una columna está marcada como «fecha y hora en que se envió la respuesta», no es una fecha
  de los datos: no propongas evolución en el tiempo ni comparación de periodos con ella.
- title: corto, en español, como lo leería quien ve el tablero (ej. «Ventas por categoría»).
- description: una frase que explica qué muestra.
- request: el pedido en lenguaje natural para configurar ese widget, con los nombres exactos de
  las columnas, la agregación de cada métrica dicha de forma explícita, el agrupamiento, el
  orden, el límite y las condiciones (ej. «suma de ventas por categoria, las 5 más altas»). Sobre
  una columna de texto, solo conteo, conteo distinto, distribución o % por condición (ej.
  «conteo de respuestas por Region en filas y Satisfecho_General en columnas»). Sin JSON.
- columns: los alias de TODAS las columnas que ese widget necesita: por las que agrupa, las que
  calcula, las de sus condiciones y la de tiempo si compara periodos (ej. ["col_3", "col_7"]).
  El widget se configura viendo solo esas columnas: no omitas ninguna. En un "filter" son las
  columnas por las que filtrar.
- requested: true solo si el texto del usuario nombra ese widget o ese análisis en concreto: su
  columna, su métrica o su gráfico (ej. «un gráfico de ventas por región» → true para ese
  gráfico). En un pedido genérico («un tablero», «los principales indicadores») TODOS son false:
  los eliges tú. Ante la duda, false.
- note: vacío si se pudo todo lo pedido. Solo si cambiaste o dejaste fuera algo del pedido, qué
  y por qué, en una frase. NO resumas ni describas el tablero que propones: eso ya lo dicen el
  título y la descripción de cada widget.
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
                    "requested": {"type": "boolean"},
                },
                "required": ["widget_type", "title", "description", "request", "columns", "requested"],
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


def _filter_columns(item: dict, columns: list[str], ctx: SheetContext) -> list[str]:
    """Las columnas de un 'filter': las que dijo la IA o, si no dijo, las que nombra su pedido
    (por alias o por nombre)."""
    if columns:
        return columns
    request = str(item.get("request") or "")
    cols = []
    for alias in re.findall(r"\bcol_\d+\b", request, re.IGNORECASE):
        field = ctx.field_for(alias)
        if field in ctx.fields and not is_id_column(field) and field not in cols:
            cols.append(field)
    if not cols:
        cols = [f for f in ctx.fields if not is_id_column(f)
                and re.search(r"\b" + re.escape(f) + r"\b", request, re.IGNORECASE)]
    return cols


def _grid_problem(widget, columns: list[str], ctx: SheetContext) -> str:
    """Por qué un widget planificado no puede usar sus columnas virtuales de cuadrícula (ver
    engine/steps/grid.py), o "" si puede: el tipo no las acepta o son de dos cuadrículas."""
    questions = list(dict.fromkeys(ctx.grid_fields[c]["question"] for c in columns if c in ctx.grid_fields))
    if questions and not widget.grid_fields:
        return f"«{widget.label}» no usa columnas de cuadrícula"
    if len(questions) > 1:
        return f"usa dos cuadrículas a la vez ({' y '.join(questions)})"
    return ""


def clean_plan(raw, ctx: SheetContext, existing_types=()) -> dict:
    """Lo que devolvió la IA, solo con lo que el tablero puede crear: tipos registrados con IA,
    a lo más uno de un tipo único por tablero (ninguno si ya está), pedido no vacío, hasta
    MAX_ITEMS. → `{items: [{widget, item, columns, problem}], note}`; `columns` son las columnas
    reales que usa cada widget (su asistente solo ve esas) y `problem` el motivo por el que no
    se puede configurar sin preguntar a la IA ("" si no hay)."""
    allowed = {w.key: w for w in _widgets()}
    taken = {t for t in existing_types if (allowed.get(t) and allowed[t].max_per_dashboard == 1)}
    planned = []
    for item in (raw.get("items") if isinstance(raw, dict) else None) or []:
        if not isinstance(item, dict):
            continue
        widget = allowed.get(str(item.get("widget_type") or "").strip())
        if not widget or not _text(item.get("request"), MAX_TEXT_CHARS):
            continue
        if widget.max_per_dashboard == 1:
            if widget.key in taken:
                continue
            taken.add(widget.key)
        columns = _plan_columns(item.get("columns"), ctx)
        if widget.key == "filter":
            columns = _filter_columns(item, columns, ctx)
        planned.append({"widget": widget, "item": item, "columns": columns,
                        "problem": _grid_problem(widget, columns, ctx)})
        if len(planned) == MAX_ITEMS:
            break
    note = _text(raw.get("note"), MAX_TEXT_CHARS) if isinstance(raw, dict) else ""
    return {"items": planned, "note": note}


def _configure(planned: dict, ctx: SheetContext) -> tuple[dict | None, list[str]]:
    """El formulario de un widget del plan: (formulario, []) o (None, [motivo]). Un 'filter'
    son sus columnas; el resto lo arma el asistente del widget con su pedido, su tipo fijo y
    sus columnas."""
    widget, item = planned["widget"], planned["item"]
    if planned["problem"]:
        return None, [planned["problem"]]
    if widget.key == "filter" and planned["columns"]:
        return {"fields": {"dimensions": planned["columns"]}, "style": {}}, []
    try:
        return generate_widget_form(_text(item.get("request"), MAX_TEXT_CHARS), widget.key, ctx,
                                    columns=planned["columns"]), []
    except SpecGenerationError as e:
        return None, [str(e)]
    except Exception:
        logger.exception("Falló la configuración de un widget del plan de tablero")
        return None, ["la IA no respondió correctamente"]


@dataclasses.dataclass
class _Checker:
    """Valida el formulario de cada widget del plan, en orden. Los campos calculados que define
    un widget válido quedan disponibles para los siguientes (como en la fuente al crearlos)."""
    ctx: SheetContext
    # Campos calculados que ya definió el plan: {name: formula}.
    defined: dict = dataclasses.field(default_factory=dict)

    def check(self, widget, item: dict) -> tuple[dict | None, list[str]]:
        """(entrada del plan, []) si el formulario es válido; si no, (None, errores)."""
        data = _resolve_columns(_normalize({**item, "widget_type": widget.key}), self.ctx)
        # El mismo campo que ya definió otro widget del plan: lo usa, no lo vuelve a crear.
        data["calculated_fields"] = [f for f in data["calculated_fields"]
                                     if self.defined.get(f["name"]) != f["formula"]]
        errors = form_errors(data, self.ctx, widget.key, require_title=False)
        if errors:
            return None, errors
        # El form validado se re-parsea por el dataclass: lo que guarda es lo que se ejecuta.
        WidgetForm.from_dict({"fields": data["fields"], "style": data["style"]})
        _omit_unchanged_scales(data, self.ctx)
        new = {f["name"]: f["formula"] for f in data["calculated_fields"]}
        if new:
            self.defined.update(new)
            self.ctx = dataclasses.replace(self.ctx, aggregated_fields=self.ctx.aggregated_fields | set(new))
        return {
            "widget_type": widget.key,
            "title": _text(item.get("title"), MAX_TITLE_CHARS) or widget.label,
            "description": _text(item.get("description"), MAX_TEXT_CHARS),
            "fields": data["fields"],
            "style": data["style"],
            "calculated_fields": data["calculated_fields"],
            "column_scales": data["column_scales"],
        }, []


def _dropped(title: str, errors: list[str]) -> str:
    detail = " ".join(errors[:MAX_DROPPED_ERRORS])
    return f"Se quitó «{title}»: no se pudo configurar ({_text(detail, MAX_TEXT_CHARS)})."


def propose_board(prompt: str, ctx: SheetContext, existing_types=(), existing_widgets=()) -> dict:
    """`{items: [{widget_type, title, description, fields, style, calculated_fields,
    column_scales}], note}` para el pedido: solo widgets ya configurados y válidos."""
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
    plan = clean_plan(raw, ctx, existing_types)
    planned = plan["items"]

    # Cada widget con el asistente, varios a la vez; después se validan en orden (los campos
    # calculados de uno quedan para los siguientes).
    forms = []
    if planned:
        with ThreadPoolExecutor(max_workers=min(BUILD_CONCURRENCY, len(planned))) as pool:
            forms = list(pool.map(lambda p: _configure(p, ctx), planned))
    checker = _Checker(ctx)
    # Los que no salieron: todos van en el error si no queda ninguno; en la nota, solo los que
    # el usuario pidió expresamente (los que la IA propuso por su cuenta no se echan de menos).
    items, dropped, dropped_requested = [], [], []
    for p, (form, errors) in zip(planned, forms):
        if form is not None:
            spec = {k: form[k] for k in SPEC_KEYS if k in form}
            entry, errors = checker.check(p["widget"], {**p["item"], **spec})
            if entry:
                items.append(entry)
        _audit(prompt, p["widget"].key, 1, "board_plan", p["item"], errors)
        if errors:
            message = _dropped(_text(p["item"].get("title"), MAX_TITLE_CHARS) or p["widget"].label, errors)
            dropped.append(message)
            if p["item"].get("requested") is True:
                dropped_requested.append(message)

    if plan["note"]:
        _audit(prompt, None, 1, "board_plan_note", {"note": plan["note"]}, [])
    if not items:
        raise SpecGenerationError("La IA no propuso widgets para ese pedido. Prueba a describir qué "
                                  "quieres ver con los nombres de las columnas."
                                  + (f" ({' '.join(dropped)})" if dropped else ""))
    return {"items": items, "note": " ".join(n for n in [plan["note"], *dropped_requested] if n)}
