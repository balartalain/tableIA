"""
Fórmula de un campo calculado a partir de un pedido en lenguaje natural (editor de la fuente,
«Generar con IA»). Un pedido → una fórmula: la IA la escribe con la gramática del motor
(`engine/formulas.py`, de donde salen también las funciones y operadores que se le muestran) y
el motor la valida contra las columnas; si no vale, se le devuelve el error una vez. Si con lo
que dio el usuario no se puede, la IA lo dice y el editor muestra su motivo.
"""
import json
import logging

import pandas as pd

from sheets_reports.engine.formulas import (
    BUILDER_CATALOG,
    FormulaError,
    compile_formula,
    formula_tree,
    rename_columns,
)
from sheets_reports.services.ai_spec import (BI_DATA_RULES, SUBMISSION_NOTE, THINKING_BUDGETS,
                                             SpecGenerationError, generate_json, samples_note,
                                             shared_samples)
from sheets_reports.services.sheets import get_field_samples
from sheets_reports.utils.data import date_fields

logger = logging.getLogger(__name__)

MAX_PROMPT_CHARS = 500
ATTEMPTS = 2

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "ok": {"type": "boolean"},
        "formula": {"type": "string"},
        "name": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["ok"],
}

FORMULA_PROMPT = """\
Escribe la fórmula de un campo calculado sobre una hoja de cálculo, a partir del pedido del
usuario.

{columns}

{data_rules}

Gramática:
- Columnas entre corchetes con su alias (ej. [col_1]) o su nombre exacto: [Gasto Real]. Textos entre comillas dobles:
  "Sí". Números con punto decimal: 1.5.
- Operadores: {arithmetic}, paréntesis; comparaciones {compare}; lógicos AND, OR, NOT.
- Funciones: {functions}.
- IF(condición, valor si se cumple, valor si no) va fila a fila; se pueden anidar.
- EMPTY deja la celda vacía y solo va como resultado de un IF: las sumas y promedios la
  ignoran y COUNT no la cuenta. Ej.: AVG(IF([sede] = "Norte", [nota], EMPTY)) promedia solo Norte.
- De texto, fila a fila: CONCAT(a, b, …) une textos (una parte vacía cuenta como texto vacío);
  LOWER, UPPER y TRIM dan vacío si la celda está vacía; CONTAINS(texto, parte) va en una
  condición y distingue mayúsculas, igual que «=»: para ignorarlas, LOWER en el texto.
  Ej.: IF(CONTAINS(LOWER([comentario]), "plataforma"), 1, 0).
- De fecha, fila a fila, sobre una columna de fechas (ISO o día primero; lo que no es una fecha
  da vacío): YEAR, MONTH (1-12), MONTH_NAME (Enero…Diciembre), QUARTER (1-4), DAY,
  WEEKDAY_NAME (Lunes…Domingo) y YEAR_MONTH ("2026-03", para agrupar por mes sin mezclar años).
  DATEDIFF(desde, hasta, "days"|"months"|"years") da los días, meses o años completos (negativo si
  «hasta» es anterior); TODAY() es la fecha de hoy. Los nombres de mes y día ya se ordenan solos.
- Sin agregaciones la fórmula es por fila (una columna nueva, ej. una categoría o una resta).
  Con agregaciones es un cálculo entre totales: cada columna va dentro de una agregación
  (SUM([a]) / [b] no vale) y una agregación no va dentro de otra.
- COUNT cuenta filas distintas (ID) con la columna no vacía; COUNT(1) cuenta
  todas las filas distintas: para contar las filas que cumplen algo usa
  SUM(IF(condición, 1, 0)). Un porcentaje se multiplica por 100.

Ejemplos:
- % de ejecución: SUM([Gasto_Real]) / SUM([Presupuesto_Asignado]) * 100
- Saldo por fila: [Presupuesto_Asignado] - [Gasto_Real]
- % de respuestas «Sí»: SUM(IF([Respuesta] = "Sí", 1, 0)) / COUNT([Respuesta]) * 100
- Participación de Hogar: SUM(IF([categoria] = "Hogar", [ventas], 0)) / SUM([ventas]) * 100
- Nivel por grado: IF([grado] <= 6, "A", IF([grado] <= 10, "B", "C"))
- Mes de la venta: YEAR_MONTH([Fecha])
- Días de entrega: DATEDIFF([Fecha pedido], [Fecha entrega], "days")
- Edad: DATEDIFF([Fecha de nacimiento], TODAY(), "years")

Responde en JSON:
- Si se puede: {{"ok": true, "formula": "…", "name": "nombre corto del campo"}}.
- Si con estas columnas y este pedido no se puede (falta una columna, el pedido es ambiguo o no
  es un cálculo): {{"ok": false, "reason": "por qué, en una frase para el usuario"}}.
  No inventes columnas.

Pedido del usuario:
{prompt}"""


class FormulaAIError(Exception):
    """La IA no pudo armar la fórmula (o no está disponible): el mensaje es para el usuario."""


def _columns_text(df: pd.DataFrame) -> str:
    numeric = set(df.select_dtypes(include="number").columns)
    dates = set(date_fields(df))
    samples = get_field_samples(df)
    refs, shared = shared_samples(samples, df.columns)
    attr_aliases = df.attrs.get("column_aliases") or {}
    lines = []
    for i, column in enumerate(df.columns):
        alias = attr_aliases.get(column) or f"col_{i + 1}"
        kind = "numérica" if column in numeric else "fecha" if column in dates else "texto"
        line = f"- {alias}: {json.dumps(str(column), ensure_ascii=False)} ({kind})"
        if column == df.attrs.get("submission_field"):
            line += f" — {SUBMISSION_NOTE}"
        line += samples_note(column, samples, refs)
        lines.append(line)
    return shared + "Columnas de la hoja:\n" + "\n".join(lines)


def build_prompt(prompt: str, df: pd.DataFrame) -> str:
    def symbols(ops):
        return " ".join(o["op"] for o in ops)
    return FORMULA_PROMPT.format(
        columns=_columns_text(df),
        data_rules=BI_DATA_RULES,
        arithmetic=symbols(BUILDER_CATALOG["arithmetic"]),
        compare=symbols(BUILDER_CATALOG["compare"]),
        functions=", ".join(f"{f['name']} ({f['title']})" for f in BUILDER_CATALOG["functions"]),
        prompt=prompt,
    )


def generate_formula(prompt: str, df: pd.DataFrame, aggregated=()) -> dict:
    """La fórmula para `prompt` sobre las columnas de `df` (con los campos por fila anteriores):
    {formula, tree, name, kind}. Lanza FormulaAIError con un mensaje para el usuario."""
    prompt = (prompt or "").strip()
    if not prompt:
        raise FormulaAIError("Describe el cálculo que quieres.")
    contents = build_prompt(prompt[:MAX_PROMPT_CHARS], df)
    for attempt in range(ATTEMPTS):
        try:
            answer = generate_json(contents, RESPONSE_SCHEMA, call="formula",
                                   thinking_budget=None if attempt else THINKING_BUDGETS["formula"])
        except SpecGenerationError as e:
            raise FormulaAIError(str(e)) from e
        except Exception as e:  # red, cuota, respuesta que no es JSON
            logger.warning("La IA no respondió la fórmula: %s", e)
            raise FormulaAIError("La IA no respondió. Intenta de nuevo en un momento.") from e
        if not isinstance(answer, dict) or not answer.get("ok"):
            reason = str((answer or {}).get("reason") or "").strip() if isinstance(answer, dict) else ""
            raise FormulaAIError(f"No pude armar la fórmula: {reason or 'el pedido no alcanza para un cálculo.'}")
        text = str(answer.get("formula") or "").strip()
        attr_aliases = df.attrs.get("column_aliases") or {}
        reverse_aliases = {alias: name for name, alias in attr_aliases.items()}
        text = rename_columns(text, reverse_aliases)
        try:
            formula = compile_formula(text, df.columns, aggregated)
        except FormulaError as e:
            # Una vez más, con el error del motor.
            contents += (f"\n\nTu respuesta anterior fue {json.dumps(text, ensure_ascii=False)} y no "
                         f"vale: {e} Corrígela.")
            continue
        return {"formula": formula.text, "tree": formula_tree(formula.text),
                "name": str(answer.get("name") or "").strip()[:120],
                "kind": "aggregated" if formula.aggregated else "row"}
    raise FormulaAIError("No pude armar una fórmula válida para ese pedido. Prueba a describirlo de otra forma.")
