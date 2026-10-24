"""
Generación del `WidgetForm` (fields + style) a partir de un prompt en lenguaje natural, vía
Gemini con function calling forzado. La IA NUNCA calcula números ni toca los datos: solo decide
QUÉ calcular, devolviendo un JSON validado contra un schema construido en cada llamada desde
las columnas reales de la hoja y el `style_schema` del widget. Nada de lo que devuelve se
ejecuta tal cual.
"""
import base64
import copy
import dataclasses
import functools
import json
import logging
import re
import struct
import time
import unicodedata
import zlib
from typing import Any

from django.conf import settings
from google import genai
from google.genai import types

from sheets_reports.engine.steps.filter import condition_errors
from sheets_reports.engine.steps.grid import query_names
from sheets_reports.engine.context import SheetContext
from sheets_reports.engine.formulas import FORMATS, FormulaError, compile_formula, formula_tree, rename_columns
from sheets_reports.services.source_columns import map_columns
from sheets_reports.utils.data import is_id_column
from sheets_reports.utils.scales import (DEFAULT_IGNORED_VALUES, SCALES, describe as describe_scale, dummy_count,
                                         normalize_text, parse_ignored_values, scale_errors)
from sheets_reports.utils.validation import MAX_IN_VALUES
from sheets_reports.engine import AGGREGATIONS
from sheets_reports.engine.steps.aggregation import METRIC_FORMATS
from sheets_reports.widgets import WIDGETS
from sheets_reports.widgets.presentation import AGG_LABELS
from sheets_reports.widgets.schemas import WidgetForm

logger = logging.getLogger(__name__)
audit_logger = logging.getLogger("tableia.ai_audit")

GEMINI_TIMEOUT_MS = 120_000
# Tokens de razonamiento del modelo por tipo de llamada: 0 = responde sin razonar; None = sin
# límite (lo que decida el modelo). El reintento de una propuesta inválida razona sin límite.
THINKING_BUDGETS = {"suggestions": 0, "widget": 0, "formula": 256, "board_plan": 512, "scales": 256, "column_labels": 256, "data_chat": 0}
CREATE_TOOL = "create_widget"
REJECT_TOOL = "reject_request"

# Claves de JSON Schema que se quitan de la declaración de la tool: condicionales y límites que
# Gemini no soporta en tools (o que la gramática de decodificación rechaza con "too many states").
# Las reglas que expresan se siguen aplicando: la validación real es la nuestra, sobre el JSON
# completo. Los `enum` de columnas SÍ se conservan: son los que impiden que la IA invente columnas.
_GEMINI_UNSUPPORTED_KEYS = {
    "allOf", "if", "then", "not", "$comment", "$schema", "maxItems", "minItems",
    "pattern", "maxLength", "minimum", "maximum", "default",
}

# Métricas calculadas sobre columnas ya agregadas.
WINDOW_TYPES = ("percent_of_total", "percent_of_row", "running_total", "pct_change")
AGG_LABELS_DESC = ", ".join(f"{k}={v.lower()}" for k, v in sorted(AGG_LABELS.items()))
NUMERIC_AGGS = {"sum", "avg", "median", "min", "max", "std"}
ALIAS_RE = re.compile(r"^[a-z][a-z0-9_]{0,40}$")

MAX_LIMIT = 500
# Campos calculados nuevos que la IA puede proponer en una respuesta.
MAX_NEW_CALCULATED = 10


class SpecGenerationError(Exception):
    """Error legible para el usuario: la IA no produjo un WidgetForm válido."""


BI_DATA_RULES = """\
## Reglas de datos y agregación (comunes al plan y a cada widget)
- Usa el tipo real de la columna: suma, promedio, mediana, mínimo, máximo y desviación estándar
  solo sobre columnas numéricas. Una escala ordinal de texto NO convierte sus respuestas en
  números. No propongas promedios de texto ni inventes puntuaciones a partir de su orden;
  una conversión solo se usa si el usuario definió la equivalencia en su pedido o ya existe un
  campo calculado que la implementa. Que el enunciado de la pregunta describa una escala
  («del 1 al 5», «de 0 a 10») o que las respuestas empiecen con un número («1 Me es muy POCO
  interesante», «2», …) NO la vuelve numérica: sigue siendo texto y no se promedia.
- Sobre texto, incluidas las escalas ordinales, puedes contar, contar distintos, mostrar la
  distribución por respuesta y calcular porcentajes por condición. Para un tablero de
  satisfacción o de interés, propone estos análisis en lugar de un promedio de las etiquetas
  (ej. «% que respondió 4 o 5 por área» en vez de «interés promedio por área»).
- Si el pedido nombra una columna de texto como medida («X como métrica», «la satisfacción por
  región») sin decir cómo agregarla, la lectura por defecto es contarla: agg "count" con esa
  columna (las respuestas no vacías). Si pide su distribución o reparto, la columna va como
  dimensión o pivote con agg "count". Nunca es motivo para rechazar el pedido.
- «No aplica», «N/A», «No utilizo este medio», «Sin respuesta» y «Empty» son respuestas de texto
  válidas, no valores nulos. No las excluyas, recodifiques ni filtres por iniciativa propia:
  solo el usuario decide excluirlas mediante filtros explícitos. Solo los nulos reales son
  ausencia de respuesta.
- Un porcentaje de respuestas a una pregunta usa como denominador COUNT([Pregunta]): excluye
  nulos reales pero incluye TODAS las categorías textuales. Si el usuario pide sobre el total
  de registros, usa COUNT([ID]). Explicita la base en la descripción del plan o el nombre de
  la medida; no cambies el denominador entre el plan y la configuración del widget.
- Para «% de insatisfechos», usa las categorías reales correspondientes (por ejemplo,
  «Insatisfecho» y «Completamente insatisfecho») y explicita cuáles incluyes. La condición
  selecciona el numerador, NO filtra el widget completo ni su denominador. Ejemplo de campo
  calculado: SUM(IF([Pregunta] = "Insatisfecho" OR [Pregunta] = "Completamente insatisfecho",
  1, 0)) / COUNT([Pregunta]) * 100. El plan describe ese cálculo en lenguaje natural.
- Los filtros del usuario y el agrupamiento se aplican tanto al numerador como al denominador.
  Un denominador cero produce un resultado vacío, no 0 %. No uses AVG(IF(condición, 1, 0))
  para excluir nulos de una pregunta: ese IF los transforma en ceros y cambia la base.

"""


CORE_PROMPT = """\
Eres el asistente de un constructor de tableros de reportes sobre una hoja de cálculo. El
usuario describe en español un widget y tú respondes SIEMPRE llamando a una función:
- `create_widget` con el formulario del widget, o
- `reject_request` con un motivo breve y amable solo si el pedido no tiene ninguna lectura
  posible con las columnas de la hoja (ej. pide una columna que no existe, o algo que no es un
  widget). Si es ambiguo, elige la lectura más razonable según las reglas de datos en vez de
  rechazarlo o pedir aclaración. Una columna de texto pedida como métrica NO es motivo de
  rechazo: cuéntala (agg "count" con esa columna).

Tú NO calculas nada ni ves los datos: solo describes QUÉ calcular. El backend ejecuta la
consulta.

""" + BI_DATA_RULES + """\
## fields (los datos)
- Cada columna de la hoja tiene un alias asignado (col_1, col_2, ...). En dimensions, pivots,
  columns, metrics.field, filters y trend_by usa SIEMPRE su alias (ej. "col_1", "col_2") en vez
  del nombre largo de la columna para evitar errores con nombres largos o corchetes.
- dimensions: alias de las columnas por las que agrupar (categorías del eje X / filas de la tabla). Si el
  widget exige dimensión y el usuario no dice cómo agrupar, usa una columna de texto con pocos
  valores distintos (ej. departamento, campus); nunca una fecha suelta ni un identificador.
- columns: columnas de la hoja a mostrar (tabla) o analizar (correlación y otros tipos que las
  admitan), en orden, cada una
  como {"field": "col_1", "label": "Nombre a mostrar"}; `label` es opcional (nombre a mostrar en la cabecera,
  sin él se muestra la columna). La tabla NO agrupa ni resume: no lleva dimensions ni metrics.
- pivots: alias de las columnas para desagregar además de la dimensión (columnas de una tabla dinámica,
  series de un gráfico). [] si no aplica.
- filters: condiciones sobre las FILAS que entran al widget (usando el alias en "field"). [] si no hay.
- trend_by: alias de la columna de TIEMPO (año, mes, fecha) de la mini tendencia (sparkline) bajo el
  número del KPI (ej. "col_1"); nunca una categoría. Solo en widgets con tendencia. Omitir si no
  aplica.
- En una hoja de respuestas de formulario, la columna marcada como «fecha y hora en que se envió
  la respuesta» solo dice cuándo se respondió: no es una fecha de los datos. NO la uses como
  dimensión, trend_by ni en filtros de periodo ("este año", "el año anterior", "el último mes"),
  salvo que el usuario pida expresamente algo sobre cuándo se respondió (ej. "respuestas por
  mes"). Si el pedido compara periodos y no hay otra columna de tiempo, recházalo con
  reject_request explicando que la hoja no tiene una fecha de los datos.
- En una hoja de respuestas de formulario, una columna «Pregunta_Opción» con valores "Sí"/"No"
  es una opción de una pregunta de selección múltiple ("Sí" = la marcó). Cuántos la marcaron:
  agg "count" con filters [{"field": esa columna, "op": "eq", "value": "Sí"}]. Reparto entre
  Sí y No: esa columna como dimensión y agg "count". Comparar las opciones de una pregunta: una
  métrica "count" por opción, cada una con su filtro "Sí", sin dimensión. Porcentaje que la
  marcó en un solo número: SUM(IF([col_N] = "Sí", 1, 0)) / COUNT([col_N]) * 100.
- La hoja siempre tiene una columna ID (una por fila, de solo lectura): NO la uses como
  dimensión, pivote, columna ni field de una métrica. El Conteo (agg "count" sin field) ya
  cuenta las filas distintas; para contar filas en una fórmula de campo calculado agregado
  usa COUNT([ID]) (o COUNT([columna]) para las filas con esa columna no vacía).
- metrics: lista de métricas, en orden. Cada una lleva un `alias` único en snake_case que es el
  nombre de la columna con la que se calcula (ej. "total_ventas") y, si hace falta un texto más
  claro para la persona que mira el widget, un `label` (nombre a mostrar, ej. "Costos totales";
  sin label se muestra el agg en español con la columna, ej. «Promedio Ventas»):
  - {"agg", "field", "alias"}: resume una columna. `field` es el alias de la columna a resumir
    (ej. "col_2"), salvo agg "count" sin field, o agg "auto" que usa el nombre del campo calculado agregado.
    - agg "count": "cuántos", "cantidad de". Cuenta FILAS DISTINTAS (sobre la
      columna de sistema ID): sin field, todas las filas; con field, las filas
      distintas con esa columna no vacía. En respuestas de formulario, sin
      field son respuestas únicas.
    - agg "count_distinct": "cuántos distintos" de una columna (cualquier tipo).
    - agg "sum"/"avg"/"median"/"min"/"max"/"std": SOLO sobre columnas numéricas.
    - agg "auto": SOLO con un campo calculado agregado: uno de los que lista el mensaje o uno
      que propongas en `calculated_fields` (ver abajo). Ya traen su agregación.
    - filters (opcional): condiciones SOLO para esa métrica. Sirven para poner en el mismo
      widget "ventas 2026" y "ventas 2025", o "ventas de Hogar" junto al total. Solo en
      widgets con "condiciones por métrica" (los que admiten más de una métrica); con una
      sola métrica, usa fields.filters.
    - window (opcional): {"type": "percent_of_total" | "percent_of_row" | "running_total" |
      "pct_change"} sobre esa métrica ya agregada. "participación", "qué % representa cada...",
      "acumulado", "variación respecto al anterior". Solo las que el widget lista en
      «ventanas». Con pivotes solo valen "percent_of_total" (cada celda sobre el total de su
      valor del pivote) y "percent_of_row" (cada celda sobre el total de su fila: "de cada X,
      qué % es de cada Y"), que además los necesita. Los dos % y "running_total" suman los
      grupos: solo con agg "sum" o "count" (un promedio, un máximo o un campo calculado no se
      suman); "pct_change" vale con cualquiera. Un widget de un solo
      número no lleva ventanas: la participación es un campo calculado agregado (ej.
      SUM(IF([categoria] = "Hogar", [ventas], 0)) / SUM([ventas]) * 100), y la variación frente
      al periodo anterior son dos métricas filtradas con relative "latest" y "previous" sobre la
      columna de tiempo (o "current_month" y "previous_month" sobre una columna de fecha) y
      style.compare con el alias de la anterior.
- sort_by: columna u alias por el que ordenar, con "-" delante para descendente (ej.
  "-total_ventas"). Null si no importa.

## calculated_fields (campos calculados nuevos, opcional)
Cuando el pedido necesita un cálculo entre totales (una diferencia, un cociente, un %, un
margen, una participación, un promedio de una condición) y no hay un campo calculado que ya lo
haga, propón el campo en `calculated_fields` y úsalo como métrica con agg "auto" y `field` igual
a su `name`. Se crea en la fuente al aplicar la propuesta y queda para todo el tablero.
- Antes de proponer uno, revisa las fórmulas de los campos calculados que ya existen (el mensaje
  las muestra): si alguno calcula lo pedido, aunque su nombre no lo diga, úsalo (agg "auto" si
  es agregado; como una columna más si es por fila). No propongas uno con la misma fórmula.
- {"name", "formula", "format"}: `name` corto y claro (ej. "% Ejecución", "Ganancia"), distinto
  de las columnas; `format` "percent" si el resultado es un porcentaje (la fórmula multiplica
  por 100), si no "number". Para porcentajes de respuestas, respeta el denominador de las
  reglas de datos: COUNT([Pregunta]) o COUNT([ID]) según el pedido.
- `formula` SIEMPRE agregada: cada columna dentro de SUM, AVG, COUNT, COUNT_DISTINCT, MIN o MAX.
  Columnas entre corchetes ([Ventas]), textos entre comillas, + - * / ( ), comparaciones
  (= != > >= < <=), AND OR NOT e IF(condición, sí, no) dentro de las agregaciones. Para que
  una fila no cuente, el resultado del IF es EMPTY (no existe NULL): AVG(IF(cond, [x], EMPTY)).
  Con fechas, dentro de la agregación: DATEDIFF(desde, hasta, "days"|"months"|"years") (lo que
  va de una a otra), TODAY() (hoy) y YEAR, MONTH, QUARTER, DAY de una fecha. Ej.: días promedio
  de entrega AVG(DATEDIFF([Fecha pedido], [Fecha entrega], "days")); pedidos de 2026
  SUM(IF(YEAR([Fecha]) = 2026, 1, 0)).
  Ej.: SUM([Ventas]) - SUM([Costo]); SUM([Gasto]) / SUM([Presupuesto]) * 100;
  SUM(IF([Respuesta] = "Sí", 1, 0)) / COUNT([Respuesta]) * 100;
  SUM(IF([categoria] = "Hogar", [ventas], 0)) / SUM([ventas]) * 100.
- No combines columnas sueltas con agregaciones (SUM([a]) / [b] no vale).
- Dentro de una agregación cada columna es el valor de la fila, no un total. Para contar filas
  que cumplen una condición usa SUM(IF(condición, 1, 0)): COUNT cuenta filas distintas (ID),
  no selecciones.
- limit: máximo de filas/grupos a mostrar ("top 5" → 5). Null si no aplica.

## style (la apariencia)
Diccionario plano con SOLO las claves del widget. Cada clave tiene un tipo fijo:
`string` (texto), `number` (número), `boolean` (true/false), `choice` (uno de sus `options`).
Los select marcados como «alias de una de las métricas» (los roles del KPI: `primary`,
`compare`, `targetMetric`) llevan el `alias` de una de las métricas del propio widget, o ""
para la opción por defecto. `targetMetric` además acepta "fixed" para una meta de valor
fijo (el número va en `target`).
No inventes claves: las que no están en el schema no existen. Puedes omitir `style` si no hace
falta cambiar nada (usa los valores por defecto).

## Condiciones (filters)
{"field", "op", "value"} o {"field", "op", "relative"}:
- op "eq"/"ne": igual/distinto de un valor. "lt"/"lte"/"gt"/"gte": comparación numérica
  (solo columnas numéricas). "in"/"not_in": lista de valores. "between": [desde, hasta]
  numérico. "contains": el texto contiene "value". "is_empty"/"not_empty": sin valor.
- relative (en vez de value) para valores que dependen de la fecha o de los datos:
  "current_year", "previous_year", "current_month", "previous_month" (del reloj); "latest" (el periodo más
  reciente de la columna), "previous" (el anterior a ese) y "earliest" (el más antiguo), solo
  en columnas de tiempo (años, meses, fechas). Úsalo para "este año", "el último mes" en vez
  de fijar un número.
- Los del reloj valen en una columna con el año o el mes suelto (2026, 10, "Octubre"), en una
  columna (fecha) y en una de año-mes ("2026-10"): en las dos últimas comparan el año, o el año
  y el mes, de cada fila (ej. {"field": "col_1", "op": "eq", "relative": "current_month"} deja
  las filas de este mes de este año; "previous_month" es el mes anterior del calendario, en
  enero diciembre del año pasado). En una columna (fecha), "latest" y "previous" son una fecha
  exacta, no un año ni un mes: para comparar años o meses usa los del reloj.
Para comparar periodos ("variación anual", "frente al año anterior") sin que el usuario diga
cuáles, usa el año del reloj: una métrica con "current_year" y otra con "previous_year". Pon en
el `label` de cada una su periodo ("Este año", "Año anterior") para que se lea qué se compara.
Usa los valores de ejemplo de las columnas para escribir el valor exacto.
"""

PIVOT_PROMPT = """\
Con pivots, los gráficos permiten UNA métrica; si el usuario pide varias y un cruce en un
gráfico, incluye todo tal como lo pidió: el sistema te pedirá que corrijas la propuesta."""

CORRELATION_PROMPT = """\
## Variables y escalas de la matriz de correlación
- Respeta los límites de columnas y de filas de la matriz declarados por el widget. Cada
  categoría nominal genera una fila (una sola si es dicotómica): no confundas el número de
  indicadores pedidos con el número de columnas seleccionadas.
- Conserva por defecto la escala efectiva de cada columna que indica el contexto, sobre todo
  si está guardada en la fuente. Para conservarla, OMITE column_scales; no repitas todas las
  escalas. Una corrección solo se propone si es necesaria y está justificada por el pedido
  o por los valores reales de ESA columna.
- Si propones una ordinal, order debe incluir TODOS los valores de esa columna, escritos
  exactamente como aparecen, en un orden justificable. No copies el orden de otra pregunta:
  acuerdo, satisfacción y aprendizaje son escalas distintas aunque sus nombres se parezcan.
- No inventes posiciones para «No aplica», «No utilizo este medio» u otras respuestas fuera
  del orden conocido. Consérvalas como categorías de una nominal, o usa el orden que haya
  definido explícitamente el usuario. No las excluyas ni las conviertas en nulos.
- Si una propuesta falla, revisa la propuesta COMPLETA, incluidas las escalas de las columnas
  que conserves o sustituyas. No reintroduzcas errores corregidos en intentos anteriores.
"""

# El título de la tarjeta lo pone el usuario y el orden de los pivotes, las flechas del editor:
# la IA no los genera ni los ve en `style`.
NOT_AI_STYLE_KEYS = {"title", "pivotOrder"}

# Con el estado actual del widget, la IA no crea desde cero: ajusta lo que ya hay.
MODIFY_PROMPT = """\
El widget ya existe: su estado actual está en `current_widget`. El usuario pide un cambio
sobre él. Devuelve con create_widget el formulario COMPLETO ya ajustado: conserva todo lo que
el usuario no pidió cambiar (dimensiones, métricas con sus alias, filtros, orden, límite y
style) y quita o reemplaza algo solo si lo pide. Si la conversación previa aclara a
qué se refiere ("eso", "ahora por mes"), úsala."""

# Mensajes previos del hilo que se le pasan a la IA (los más recientes).
MAX_HISTORY_MESSAGES = 10


def capabilities_text(widget) -> str:
    """Qué admite un widget, en una línea, desde su `capabilities` declarativo."""
    caps = widget.capabilities or {}
    parts = []
    columns = caps.get("columns")
    if columns:
        low, high = columns
        if low == high:
            parts.append(f"columnas {low}")
        else:
            parts.append(f"columnas de {low} a {high}")
        if caps.get("columns_numeric"):
            parts.append("solo columnas numéricas")
        if caps.get("columns_scaled"):
            parts.append("columnas numéricas, ordinales (con su orden) o nominales (cada categoría "
                         f"de una nominal es una fila 0/1, hasta {caps.get('nominal_max_categories')} "
                         "por variable); en column_scales puedes corregir la escala de una columna")
    if caps.get("ungrouped_min_metrics"):
        parts.append(f"sin dimensión: al menos {caps['ungrouped_min_metrics']} métricas "
                     "(una barra por total)")
    if caps.get("pivot_max_metrics"):
        parts.append(f"con pivotes: {caps['pivot_max_metrics']} métrica como máximo")
    for key, label in (("dimensions", "dimensiones"), ("pivots", "pivotes"), ("metrics", "métricas")):
        rng = caps.get(key)
        if not rng:
            parts.append(f"sin {label}")
        else:
            low, high = rng
            if low == 0 and high == 0:
                parts.append(f"sin {label}")
            elif low == high:
                parts.append(f"{label} {low}")
            else:
                parts.append(f"{label} de {low} a {high}")
    for key, label in (("sort", "orden"), ("limit", "límite"), ("filters", "filtros")):
        if caps.get(key):
            parts.append(label)
        else:
            parts.append(f"sin {label}")
    if caps.get("trend"):
        parts.append("tendencia")
    if admits_metric_filters(widget):
        parts.append("condiciones por métrica")
    if (caps.get("metrics") or (0, 0))[1] > 0:
        windows = window_types_for(widget)
        parts.append("ventanas: " + ", ".join(WINDOW_LABELS[w] for w in windows) if windows
                     else "sin ventanas")
    return ", ".join(parts)


WINDOW_LABELS = {"percent_of_total": "% del total (participación)",
                 "percent_of_row": "% del total de la fila",
                 "running_total": "acumulado", "pct_change": "variación"}


# Ventanas que se calculan por celda y por eso valen con pivotes (engine/steps/window.py).
PIVOT_WINDOWS = ("percent_of_total", "percent_of_row")
# Ventanas que suman los valores de los grupos (el % de un total, el acumulado): solo valen
# con agregaciones aditivas. Un promedio, un máximo o un cociente no se suman.
ADDITIVE_WINDOWS = ("percent_of_total", "percent_of_row", "running_total")
ADDITIVE_AGGS = ("sum", "count")


def window_types_for(widget) -> tuple:
    """Ventanas que el widget dibuja bien (`capabilities["windows"]`)."""
    return tuple((widget.capabilities or {}).get("windows") or ())


def _window_error(path: str, w_type: str, widget, pivots: list, agg: str | None = None) -> str | None:
    """Una ventana válida en el motor pero que en este widget se ignoraría o daría números
    falsos. El mensaje guía a la IA en el reintento."""
    caps = widget.capabilities or {}
    allowed = window_types_for(widget)
    if w_type not in allowed:
        if caps.get("dimensions", (0, 0))[1] == 0 and caps.get("metrics", (0, 0))[1] > 0:
            # Un solo número: no hay otras filas (ni total de grupos, ni anterior, ni acumulado).
            return (f"{path}: este widget da un solo número y no admite 'window'. Para una "
                    f"participación propón un campo calculado agregado (ej. SUM(IF([cat] = \"X\", "
                    f"[valor], 0)) / SUM([valor]) * 100) y úsalo con agg 'auto'. Para comparar con el "
                    f"periodo anterior usa dos métricas con filters relative 'latest' y 'previous' "
                    f"sobre la columna de tiempo y style.compare con el alias de la anterior.")
        if not allowed:
            return f"{path}: este widget no admite 'window'; quítalo."
        return f"{path}: '{w_type}' no está disponible en este widget; usa {', '.join(allowed)} o quítalo."
    if pivots and w_type not in PIVOT_WINDOWS:
        # Con pivote cada valor es una celda del cruce: solo los porcentajes se calculan por celda.
        usable = [w for w in allowed if w in PIVOT_WINDOWS]
        hint = f"usa {', '.join(usable)}, " if usable else ""
        return f"{path}: con pivotes '{w_type}' no se aplica; {hint}quita los pivotes o la ventana."
    if w_type == "percent_of_row" and not pivots:
        return (f"{path}: 'percent_of_row' reparte cada fila entre las columnas cruzadas y "
                f"necesita pivotes; sin ellos usa 'percent_of_total'.")
    if w_type in ADDITIVE_WINDOWS and agg not in ADDITIVE_AGGS:
        return (f"{path}: '{w_type}' suma los valores de los grupos y solo vale con agg "
                f"{' o '.join(ADDITIVE_AGGS)}; con '{agg}' el total no significa nada. Quita la "
                f"ventana o usa 'pct_change'.")
    return None


def panel_options(widget) -> dict:
    """Lo que el panel puede ofrecer en las métricas de `widget`, calculado con las mismas
    reglas que valida `form_errors` (así el panel nunca ofrece algo que al guardar se rechaza):
    - `windows`: «Mostrar como» por agregación, sin pivote (`flat`) y con pivote (`pivot`);
    - `numeric_aggs`: agregaciones que solo valen sobre columnas numéricas;
    - `metric_formats`: formatos que puede elegir una métrica."""
    aggs = [agg for agg in AGGREGATIONS if agg != "mean"]
    windows = {
        mode: {agg: [w for w in WINDOW_TYPES if _window_error("", w, widget, pivots, agg) is None]
               for agg in aggs}
        for mode, pivots in (("flat", []), ("pivot", ["pivote"]))
    }
    return {"windows": windows, "numeric_aggs": sorted(NUMERIC_AGGS),
            "metric_formats": list(METRIC_FORMATS)}


def admits_metric_filters(widget) -> bool:
    """Condiciones propias de una métrica: solo junto a otras métricas (Electrónica vs total);
    con una sola métrica equivalen a los filtros del widget."""
    return (widget.capabilities or {}).get("metrics", (0, 0))[1] > 1


def _style_schema_docs(widget) -> list[str]:
    """Una línea por control de estilo: clave, tipo y opciones."""
    lines = []
    for control in widget.style_schema or []:
        if control["key"] in NOT_AI_STYLE_KEYS:
            continue
        kind = control.get("type")
        if control.get("options_from") == "metrics":
            detail = (f"select con el alias de una de las métricas del widget "
                      f"(o vacío: {control.get('label', control['key'])})")
        elif kind == "choice":
            options = ", ".join(f'"{o["value"]}"' for o in control.get("options", []))
            detail = f"select de {options}"
        else:
            detail = f"{kind} ({control.get('label', control['key'])})"
        lines.append(f"- style.{control['key']}: {detail}")
    return lines


def _widgets_for(widget_type: str | None) -> list:
    if widget_type:
        return [WIDGETS.get(widget_type)]
    return [w for w in WIDGETS if w.ai_enabled]


def _without_title(form: dict) -> dict:
    """Un WidgetForm sin título ni `style.title`: así lo ve (y lo devuelve) la IA."""
    form = {k: v for k, v in form.items() if k != "title"}
    if isinstance(form.get("style"), dict):
        form["style"] = {k: v for k, v in form["style"].items() if k not in NOT_AI_STYLE_KEYS}
    return form


def _current_for_ai(current: dict, widget_type: str | None) -> dict:
    """El widget actual tal como lo ve la IA: sin título, sin claves vacías de `fields` y sin las
    que su tipo no admite. El panel manda todas las claves (`pivots: []`, `limit: null`...) y,
    como se le pide conservarlas, Gemini se cuelga (504) intentando devolver lo que su schema no
    tiene."""
    form = _without_title(current)
    widget = WIDGETS.get(widget_type) if widget_type else None
    if isinstance(form.get("fields"), dict):
        form["fields"] = {k: v for k, v in form["fields"].items()
                          if v not in (None, "", [], {}) and (widget is None or _admits_field(widget, k))}
    return form


def build_system_prompt(widgets=None) -> str:
    """El prompt de la IA: lo del core más lo que declara cada widget que la IA puede proponer
    (cuándo usarlo, qué admite, sus controles de estilo y sus ejemplos)."""
    widgets = list(widgets) if widgets is not None else [w for w in WIDGETS if w.ai_enabled]
    types_section = [
        f"- {w.key}: {w.ai_doc + ' ' if w.ai_doc else ''}Admite: {capabilities_text(w)}." for w in widgets
    ]
    style_docs = []
    for widget in widgets:
        if widget.style_schema:
            style_docs.append(f"- {widget.key}:")
            style_docs += [f"  {line}" for line in _style_schema_docs(widget)]
    examples = [
        f'Prompt: "{prompt}"\ncreate_widget({json.dumps(_without_title(args), ensure_ascii=False)})'
        for w in widgets for prompt, args in w.ai_examples
    ]
    return "\n".join([
        CORE_PROMPT,
        "## Tipos de widget (si no viene fijado)",
        *types_section,
        PIVOT_PROMPT,
        CORRELATION_PROMPT if any(w.capabilities.get("columns_scaled") for w in widgets) else "",
        "",
        "## style de cada tipo",
        *style_docs,
        "",
        "## Ejemplos (columnas ilustrativas; usa los alias col_1, col_2 o las columnas reales de la hoja)",
        "",
        "\n\n".join(examples),
    ]) + "\n"


@functools.lru_cache(maxsize=None)
def system_prompt_for(widget_type: str | None) -> str:
    """El prompt de sistema de una llamada: con el tipo fijado, solo lo de ese widget (su
    documentación, sus controles de estilo y sus ejemplos), no lo de todos."""
    return build_system_prompt([w for w in _widgets_for(widget_type) if w])


_cached_client: genai.Client | None = None
_cached_api_key: str | None = None


def gemini_client(api_key: str) -> genai.Client:
    global _cached_client, _cached_api_key
    if _cached_client is not None and _cached_api_key == api_key:
        return _cached_client
    _cached_client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(
            timeout=GEMINI_TIMEOUT_MS,
            retry_options=types.HttpRetryOptions(attempts=2),
        ),
    )
    _cached_api_key = api_key
    return _cached_client


def _log_usage(call: str, response, started: float) -> None:
    """Tokens y tiempo de una llamada a la IA, en el log de auditoría: lo que se envió
    (`prompt`, y cuánto de eso salió de la caché de Gemini), lo que razonó y lo que respondió."""
    usage = getattr(response, "usage_metadata", None)
    audit_logger.info(json.dumps({
        "usage": call,
        "ms": round((time.monotonic() - started) * 1000),
        "prompt_tokens": getattr(usage, "prompt_token_count", None),
        "cached_tokens": getattr(usage, "cached_content_token_count", None),
        "thinking_tokens": getattr(usage, "thoughts_token_count", None),
        "output_tokens": getattr(usage, "candidates_token_count", None),
    }))


def _thinking_config(budget: int | None):
    if budget is None or not settings.GEMINI_LIMIT_THINKING:
        return None
    return types.ThinkingConfig(thinking_budget=budget)


def generate_json(contents: str, schema: dict, temperature: float = 0, call: str = "json",
                  thinking_budget: int | None = None) -> object:
    """Respuesta JSON libre (sin tools) que cumple `schema`: para pedidos auxiliares a la IA,
    como las sugerencias del chat (`call` la nombra en el log de consumo y `thinking_budget`
    limita cuánto razona). Lanza si no hay API key o la respuesta no es JSON."""
    api_key = settings.GEMINI_API_KEY
    if not api_key:
        raise SpecGenerationError("GEMINI_API_KEY no está configurado.")
    client = gemini_client(api_key)
    started = time.monotonic()
    response = client.models.generate_content(
        model=settings.GEMINI_MODEL,
        contents=contents,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_json_schema=schema,
            temperature=temperature,
            thinking_config=_thinking_config(thinking_budget),
        ),
    )
    _log_usage(call, response, started)
    return json.loads(response.text or "null")


def _is_blank_image(data_uri: str) -> bool:
    """Detecta si una imagen en formato data URI PNG es un lienzo en blanco / sin contenido."""
    if not data_uri.startswith("data:image/png;base64,"):
        return False
    try:
        b64_str = data_uri.split(",", 1)[1]
        png_bytes = base64.b64decode(b64_str)
        if not png_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
            return False
        idat_data = bytearray()
        offset = 8
        while offset < len(png_bytes):
            if offset + 8 > len(png_bytes):
                break
            length, chunk_type = struct.unpack(">I4s", png_bytes[offset : offset + 8])
            offset += 8
            if chunk_type == b"IDAT":
                idat_data.extend(png_bytes[offset : offset + length])
            offset += length + 4
        if not idat_data:
            return False
        decompressed = zlib.decompress(bytes(idat_data))
        # Un lienzo completamente en blanco/sólido tiene <= 4 valores de byte únicos en su stream
        return len(set(decompressed)) <= 4
    except Exception:
        return False


WIDGET_INSIGHT_SYSTEM_PROMPT = """\
Eres un consultor analítico de inteligencia de negocios experto.
Tu objetivo es analizar los datos calculados de un gráfico o indicador de un tablero y redactar una interpretación ejecutiva en español para la toma de decisiones.

Instrucciones:
1. Formato: Escribe en Markdown estructurado (con subtítulos cortos ### si amerita, negritas para cifras y porcentajes clave, y viñetas).
2. Estilo: Sé directo, profesional y conciso. NO uses saludos ni frases introductorias tipo "A continuación presento...". Empieza de inmediato con los hallazgos principales.
3. Contenido:
   - Resalta los valores más altos, más bajos y las proporciones o variaciones más significativas.
   - Identifica tendencias, concentraciones o anomalías observables en las cifras.
   - Concluye con 1 o 2 observaciones clave o recomendaciones prácticas de negocio.
"""


def generate_widget_insight(widget_title: str, widget_type: str, data_text: str) -> str:
    """Genera un análisis interpretativo en Markdown a partir de los datos calculados de un widget."""
    api_key = settings.GEMINI_API_KEY
    if not api_key:
        raise SpecGenerationError("GEMINI_API_KEY no está configurado.")
    client = gemini_client(api_key)
    prompt = (
        f"Widget: '{widget_title}' (Tipo de gráfico: {widget_type})\n\n"
        f"Datos calculados del widget:\n{data_text}\n\n"
        "Redacta el análisis interpretativo en Markdown:"
    )
    started = time.monotonic()
    response = client.models.generate_content(
        model=settings.GEMINI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=WIDGET_INSIGHT_SYSTEM_PROMPT,
            temperature=0.2,
            thinking_config=_thinking_config(0),
        ),
    )
    _log_usage(f"widget_insight:{widget_type}", response, started)
    return (response.text or "No se pudo generar el análisis.").strip()


def gemini_upload_file(file_path: str, display_name: str | None = None) -> Any:
    """Sube un archivo a Gemini Files API."""
    api_key = settings.GEMINI_API_KEY
    if not api_key:
        raise SpecGenerationError("GEMINI_API_KEY no está configurado.")
    client = gemini_client(api_key)
    config = types.UploadFileConfig(display_name=display_name) if display_name else None
    return client.files.upload(file=file_path, config=config)


def gemini_get_file(file_id: str) -> Any:
    """Obtiene un archivo de Gemini Files API o None si no existe o expiró."""
    api_key = settings.GEMINI_API_KEY
    if not api_key or not file_id:
        return None
    client = gemini_client(api_key)
    try:
        f = client.files.get(name=file_id)
        state = getattr(f, "state", None)
        state_str = str(getattr(state, "name", state) or "").upper()
        if "ACTIVE" in state_str or state_str in ("", "STATE_UNSPECIFIED"):
            return f
        return None
    except Exception:
        return None


def gemini_delete_file(file_id: str) -> bool:
    """Elimina un archivo de Gemini Files API si existe."""
    api_key = settings.GEMINI_API_KEY
    if not api_key or not file_id:
        return False
    client = gemini_client(api_key)
    try:
        client.files.delete(name=file_id)
        return True
    except Exception as e:
        logger.info("No se pudo eliminar el archivo %s de Gemini: %s", file_id, e)
        return False


NO_ANSWER = "No se pudo generar una respuesta. Intenta reformular o acotar la pregunta."


def generate_with_code_execution(
    file_path: str | None = None,
    prompt: str = "",
    system_instruction: str = "",
    file_ref: Any = None,
    client: genai.Client | None = None,
) -> dict:
    """Ejecuta una consulta analítica en Gemini subiendo un archivo o reutilizando file_ref, y activando la tool de code_execution."""
    api_key = settings.GEMINI_API_KEY
    if not api_key:
        raise SpecGenerationError("GEMINI_API_KEY no está configurado.")
    if client is None:
        client = gemini_client(api_key)
    if file_ref is None:
        if not file_path:
            raise SpecGenerationError("Se requiere file_path o file_ref.")
        file_ref = client.files.upload(file=file_path)
    contents = [file_ref, prompt]

    config = types.GenerateContentConfig(
        tools=[types.Tool(code_execution=types.ToolCodeExecution())],
        temperature=0.2,
        # Con razonamiento libre, alguna consulta tarda minutos en vez de segundos.
        thinking_config=_thinking_config(THINKING_BUDGETS["data_chat"]),
    )
    if system_instruction:
        config.system_instruction = system_instruction

    started = time.monotonic()
    response = client.models.generate_content(
        model=settings.GEMINI_MODEL,
        contents=contents,
        config=config,
    )
    _log_usage("code_execution", response, started)

    texts, raw_images, codes = [], [], []
    if response.candidates and response.candidates[0].content:
        # Sin partes cuando Gemini corta antes de responder (MAX_TOKENS, SAFETY, límite de
        # ejecución de código...): se responde el texto de reserva en vez de fallar.
        parts = response.candidates[0].content.parts or []
        if not parts:
            logger.warning("La IA respondió sin contenido (finish_reason=%s)",
                           getattr(response.candidates[0], "finish_reason", None))
        last_code_idx = -1
        for i, part in enumerate(parts):
            if getattr(part, "executable_code", None) or getattr(part, "code_execution_result", None):
                last_code_idx = i

        for i, part in enumerate(parts):
            if getattr(part, "text", None):
                # Omitir fragmentos de texto preliminares que ocurren antes de la ejecución de código
                if last_code_idx != -1 and i < last_code_idx:
                    continue
                texts.append(part.text)
            if getattr(part, "inline_data", None) and part.inline_data.data:
                b64 = base64.b64encode(part.inline_data.data).decode("ascii")
                img_url = f"data:{part.inline_data.mime_type or 'image/png'};base64,{b64}"
                if img_url not in raw_images:
                    raw_images.append(img_url)
            if getattr(part, "executable_code", None) and part.executable_code.code:
                codes.append(part.executable_code.code)

        if not texts and parts:
            for part in parts:
                if getattr(part, "text", None):
                    texts.append(part.text)

    # Filtrar imágenes vacías o lienzos en blanco
    images = [img for img in raw_images if not _is_blank_image(img)]
    if not images and raw_images:
        images = raw_images

    full_text = "\n\n".join(t.strip() for t in texts if t.strip()) or NO_ANSWER
    # Limpiar sintaxis de imágenes markdown o HTML para evitar renderizarlas duplicadas (ya se devuelven en images)
    full_text = re.sub(r"!\[.*?\]\([^)]*\)", "", full_text)
    full_text = re.sub(r"<img[^>]*>", "", full_text, flags=re.IGNORECASE)
    # Limpiar líneas de preámbulo residuales ("Para X, ejecutaré el siguiente código...", etc.)
    full_text = re.sub(
        r"(?mi)^.*(?:ejecutar[ée]|cargar[ée]|proceder[ée]|escribir[ée]|utilizar[ée]|correr[ée]).*$\n*",
        "",
        full_text,
    ).strip()
    return {
        "text": full_text or NO_ANSWER,
        "images": images,
        "code": codes,
    }


def _to_gemini_schema(node):
    """Adapta el JSON Schema de validación al subconjunto que acepta la declaración de tools."""
    if isinstance(node, list):
        return [_to_gemini_schema(n) for n in node]
    if not isinstance(node, dict):
        return node
    out = {}
    for key, value in node.items():
        if key in _GEMINI_UNSUPPORTED_KEYS:
            continue
        if key == "properties":
            out[key] = {
                name: (_scalar_or_list_schema() if sub is True else _to_gemini_schema(sub))
                for name, sub in value.items()
            }
        elif key == "type" and isinstance(value, list):
            return {"anyOf": [{"type": t} for t in value]}
        else:
            out[key] = _to_gemini_schema(value)
    return out


def _scalar_or_list_schema():
    scalar = [{"type": "string"}, {"type": "number"}, {"type": "boolean"}]
    return {"anyOf": [*scalar, {"type": "array", "items": {"anyOf": scalar}}]}


def _style_schema(style_schema: list[dict]) -> tuple[dict, list[str]]:
    """Schema de `style` desde los controles declarados, más sus claves obligatorias."""
    properties: dict = {}
    for control in style_schema or []:
        if control["key"] in NOT_AI_STYLE_KEYS:
            continue
        kind = control.get("type")
        if control.get("options_from") == "metrics":
            # El valor es un alias que la propia propuesta define: no puede vivir en un enum.
            properties[control["key"]] = {
                "type": "string",
                "description": (f"{control.get('label', control['key'])}: alias de una de las "
                                f"métricas del widget (vacío = la opción por defecto)."),
            }
        elif kind == "choice":
            properties[control["key"]] = {
                "enum": [o["value"] for o in control.get("options", [])],
                "description": control.get("label", control["key"]),
            }
        elif kind == "number":
            properties[control["key"]] = {"type": "number", "description": control.get("label", control["key"])}
        elif kind == "boolean":
            properties[control["key"]] = {"type": "boolean", "description": control.get("label", control["key"])}
        elif kind == "list":
            properties[control["key"]] = {"type": "array", "items": {"type": "string"},
                                          "description": control.get("label", control["key"])}
        else:
            properties[control["key"]] = {"type": "string", "description": control.get("label", control["key"])}
    return properties, []


def _admits_field(widget, key: str) -> bool:
    """¿El tipo admite la clave `key` de `fields`, según sus capabilities?"""
    caps = widget.capabilities or {}
    if key in ("dimensions", "pivots", "metrics", "columns"):
        return (caps.get(key) or [0, 0])[1] > 0
    if key == "trend_by":
        return bool(caps.get("trend"))
    if key == "sort_by":
        return bool(caps.get("sort"))
    if key in ("limit", "filters"):
        return bool(caps.get(key))
    return True


def build_tool_parameters(ctx: SheetContext, widget_type: str | None) -> dict:
    """Schema de parámetros de `create_widget`, construido en cada llamada desde los registros
    de widgets (capabilities + style_schema) y el motor (agregaciones y ventanas disponibles).
    Las columnas van como texto libre, no como enum: con muchas columnas o nombres largos
    (preguntas de un formulario) el enum repetido en cada campo supera el límite de estados
    de Gemini. Los nombres exactos van en el mensaje (`columns_context`) y `form_errors`
    rechaza los que no existen, con reintento."""
    widgets = [w for w in _widgets_for(widget_type) if w]
    metric_properties = {
        "agg": {"enum": sorted(set(AGGREGATIONS) - {"mean"}),
                "description": "Agregación: " + AGG_LABELS_DESC},
        "field": _column_schema("Columna de la hoja a agregar. Omitir solo en agg=count."),
        "alias": {"type": "string",
                  "description": "Nombre único en snake_case de la columna resultante (ej. total_ventas)."},
        "label": {"type": "string", "maxLength": 80,
                  "description": "Nombre a mostrar de la métrica en el widget (ej. 'Costos totales'); "
                                 "vacío o ausente = el alias en texto."},
        "filters": {**condition_schema_for(ctx),
                    "description": "Condiciones SOLO para esta métrica."},
        "window": {
            "type": "object",
            "additionalProperties": False,
            "required": ["type"],
            "properties": {"type": {"enum": list(WINDOW_TYPES),
                                    "description": "Cálculo sobre la métrica ya agregada."}},
            "description": "Transformación sobre la métrica (porcentaje del total, acumulado...).",
        },
    }
    if widget_type and widgets and not admits_metric_filters(widgets[0]):
        # Con una sola métrica sus condiciones serían las del widget: se ofrece fields.filters.
        del metric_properties["filters"]
    if widget_type and widgets:
        windows = list(window_types_for(widgets[0]))
        if windows:
            metric_properties["window"]["properties"]["type"]["enum"] = windows
        else:
            del metric_properties["window"]
    fields_properties = {
        "dimensions": {"type": "array", "items": _column_schema(),
                       "description": "Columnas para agrupar / mostrar como filas."},
        "pivots": {"type": "array", "items": _column_schema(),
                   "description": "Columnas para desagregar (series / columnas cruzadas)."},
        "columns": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                               "required": ["field"],
                                               "properties": {
                                                   "field": _column_schema("Columna de la hoja a mostrar."),
                                                   "label": {"type": "string",
                                                             "description": "Nombre a mostrar en la cabecera; "
                                                                            "vacío o ausente = la columna."},
                                               }},
                    "description": "Columnas a mostrar tal cual, en orden (tabla de datos); "
                                   "no agrupa ni resume."},
        "metrics": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                               "required": ["agg", "alias"],
                                               "properties": metric_properties},
                    "description": "Métricas, en el orden en que se muestran."},
        "filters": {**condition_schema_for(ctx),
                    "description": "Condiciones sobre las filas del widget."},
        "trend_by": _column_schema("Columna de TIEMPO (año, mes, fecha) de la mini tendencia "
                                   "(sparkline) del número; solo los widgets con tendencia. "
                                   "Omitir si no aplica."),
        "sort_by": {"type": "string",
                    "description": "Columna u alias de orden, con '-' delante para descendente."},
        "limit": {"type": "integer", "description": f"Máximo de filas/grupos (1 a {MAX_LIMIT})."},
    }
    if widget_type and widgets:
        # Con el tipo fijado, la IA solo ve los campos que ese tipo admite: no puede proponer,
        # por ejemplo, una tendencia en un gráfico de dona (form_errors la rechazaría).
        fields_properties = {key: schema for key, schema in fields_properties.items()
                             if _admits_field(widgets[0], key)}

    # Sin tipo fijado el estilo depende del widget elegido: se acepta como objeto abierto y se
    # valida contra su style_schema después.
    style = {"type": "object", "description": "Apariencia del widget. Opcional: sin estilo se "
                                              "usan los valores por defecto."}
    if widget_type and widgets:
        style_properties, _ = _style_schema(widgets[0].style_schema)
        if style_properties:
            style = {"type": "object", "additionalProperties": False, "properties": style_properties}

    return _to_gemini_schema({
        "type": "object",
        "additionalProperties": False,
        "required": ["widget_type", "fields"],
        "properties": {
            "widget_type": {"enum": [w.key for w in widgets]},
            "calculated_fields": {
                "type": "array", "maxItems": MAX_NEW_CALCULATED,
                "description": "Campos calculados agregados nuevos que usan las métricas (agg 'auto').",
                "items": {"type": "object", "additionalProperties": False,
                          "required": ["name", "formula", "format"],
                          "properties": {
                              "name": {"type": "string", "description": "Nombre del campo."},
                              "formula": {"type": "string",
                                          "description": "Fórmula agregada, ej. SUM([Ventas]) - SUM([Costo])."},
                              "format": {"enum": list(FORMATS)},
                          }},
            },
            "fields": {"type": "object", "additionalProperties": False,
                       "properties": fields_properties},
            "style": style,
            **({"column_scales": {
                "type": "array",
                "description": "Solo para corregir la escala de una columna de `columns` cuando la "
                               "indicada en la lista de columnas no es la correcta (ej. una nominal "
                               "cuyos valores tienen un orden natural: ordinal). Omitir para conservar "
                               "las escalas efectivas; no repetir las ya detectadas o guardadas.",
                "items": {"type": "object", "additionalProperties": False,
                          "required": ["field", "scale"],
                          "properties": {
                              "field": _column_schema("Columna cuya escala se corrige."),
                              "scale": {"enum": [s for s in SCALES if s != "none"]},
                              "order": {"type": "array", "items": {"type": "string"},
                                         "description": "Solo en ordinal: TODOS sus valores, de "
                                                        "menor a mayor, escritos como en ESA columna "
                                                        "de la hoja. No copiar escalas de otra columna "
                                                        "ni inventar posiciones para categorías sin orden."},
                          }},
            }} if any((w.capabilities or {}).get("columns_scaled") for w in widgets) else {}),
        },
    })


def _column_schema(description: str = "") -> dict:
    """Una columna de la hoja en el schema de la tool: alias (col_1) o texto."""
    hint = "Alias de la columna (ej. 'col_1', 'col_2') o su nombre exacto."
    return {"type": "string", "description": f"{description} {hint}".strip()}


def condition_schema_for(ctx: SheetContext) -> dict:
    """Las condiciones del motor para la tool: igual que las que valida, pero con la columna
    como texto libre."""
    from sheets_reports.engine.steps.filter import conditions_schema
    schema = conditions_schema(ctx, MAX_IN_VALUES)
    items = schema["items"]
    return {**schema, "items": {**items, "properties": {**items["properties"],
                                                        "field": _column_schema()}}}


def _tools(ctx: SheetContext, widget_type: str | None) -> list[types.Tool]:
    return [types.Tool(function_declarations=[
        types.FunctionDeclaration(
            name=CREATE_TOOL,
            description="Crea el formulario (fields + style) de un widget del tablero.",
            parameters_json_schema=build_tool_parameters(ctx, widget_type),
        ),
        types.FunctionDeclaration(
            name=REJECT_TOOL,
            description=("Rechaza el pedido solo cuando no tiene ninguna lectura posible como widget con las "
                         "columnas disponibles. No lo uses porque una columna de texto se pida como métrica: "
                         "cuéntala con create_widget."),
            parameters_json_schema={
                "type": "object",
                "required": ["reason"],
                "properties": {"reason": {"type": "string", "description": "Motivo breve, en español, dirigido al usuario."}},
            },
        ),
    ])]


FORMAT_LABELS = {"percent": "porcentaje", "currency": "moneda", "progress": "porcentaje"}

# Cómo se le presenta a la IA la columna de envío de un formulario (`submission_field`).
SUBMISSION_NOTE = "fecha y hora en que se envió la respuesta del formulario: no es una fecha de los datos"


def shared_samples(samples: dict, fields) -> tuple[dict, str]:
    """Las listas de valores de ejemplo que se repiten en varias columnas (las escalas de un
    formulario: «Satisfecho», «Insatisfecho»...) se escriben una sola vez, con nombre.
    → ({columna: nombre de su lista}, el bloque de texto que las define; "" si no hay)."""
    groups: dict[tuple, list] = {}
    for field in fields:
        values = samples.get(field)
        if values:
            groups.setdefault(tuple(sorted(map(str, values))), []).append(field)
    refs, lines = {}, []
    for columns in groups.values():
        if len(columns) < 2:
            continue
        name = f"V{len(lines) + 1}"
        lines.append(f"- {name}: {json.dumps(samples[columns[0]], ensure_ascii=False, default=str)}")
        refs.update(dict.fromkeys(columns, name))
    block = "Listas de valores que comparten varias columnas:\n" + "\n".join(lines) + "\n\n" if lines else ""
    return refs, block


def samples_note(field, samples: dict, refs: dict) -> str:
    """Los valores de ejemplo de una columna, o la lista compartida donde están."""
    if field in refs:
        return f" — valores de ejemplo: los de {refs[field]}"
    if samples.get(field):
        return f" — valores de ejemplo: {json.dumps(samples[field], ensure_ascii=False, default=str)}"
    return ""


GRID_NOTE = """Cuadrículas y preguntas de casillas del formulario: las columnas virtuales «<pregunta> · Aspecto» (en una de casillas, «· Opción») y «<pregunta> · Respuesta» son la pregunta en formato largo (una fila por respuesta y aspecto u opción). En una de casillas, Respuesta es Sí/No.
Se puede:
- Aspecto (u Opción) como dimensión y Respuesta como pivote, con Conteo y «% de la fila»: la distribución de respuestas de cada aspecto (en casillas, el % que marcó cada opción).
- Agrupar por una columna real y ver un solo aspecto: la columna real como dimensión, Respuesta como pivote y el filtro Aspecto = «…» (ej. Recinto × Respuesta de «Servicios generales», con «% de la fila»).
- Filtrar Aspecto o Respuesta (eq, in); contar con el filtro Respuesta in [...] (ej. cuántos respondieron «Satisfecho» o «Muy satisfecho»); AVG de Respuesta si es numérica.
No se puede (no lo propongas ni lo configures; propón la alternativa y dilo en "note"):
- Un campo calculado (fórmula) con una virtual: ningún ratio ni % que junte varias respuestas, como «% de satisfechos o completamente satisfechos». Alternativa: la distribución con «% de la fila» (Respuesta como pivote, se ve el % de cada respuesta) o el conteo con el filtro Respuesta in [...].
- Las virtuales de dos cuadrículas en un mismo widget. Alternativa: un widget por cuadrícula.
- Las virtuales en dispersión, matriz de correlación, V de Cramér o filtros. Alternativa: tabla dinámica o barras."""


def columns_context(ctx: SheetContext, only=None, virtual: bool = True) -> str:
    """Las columnas con su alias, tipo y ejemplos, y los campos calculados con su fórmula: así la IA
    sabe qué calcula cada uno aunque su nombre no lo diga. Con `only` (las columnas que usa un
    widget ya planificado) lista solo esas y la ID: en una hoja ancha es casi todo el mensaje.
    Con `virtual=False` omite las columnas virtuales de las cuadrículas: el chatbot recibe el
    CSV de la fuente, que solo tiene las reales."""
    only = set(only or ())
    candidates = ctx.fields if virtual else ctx.sheet_fields
    fields = [f for f in candidates if not only or f in only or is_id_column(f)]
    members = {column: grid["question"] for grid in ctx.grid_fields.values() for column in grid["columns"]}
    refs, shared = shared_samples(ctx.samples, fields)
    lines = []
    for field in fields:
        alias = ctx.alias_for(field)
        kind = "numérica" if ctx.is_numeric(field) else "fecha" if ctx.is_date(field) else "texto"
        profile = ctx.scales.get(field)
        if profile and not is_id_column(field) and field != ctx.submission_field and not ctx.is_date(field):
            # La escala de medida y un perfil corto (rango, orden, texto libre): lo que la IA ve
            # de los valores de una columna además de los ejemplos.
            kind += f", {describe_scale(field, profile, ctx.is_numeric(field))}"
        line = f"- {alias}: {json.dumps(field, ensure_ascii=False)} ({kind})"
        if profile and profile.get("saved"):
            line += " — escala guardada en la fuente: conservar salvo corrección explícita"
        if is_id_column(field):
            line += " — columna de sistema: una por fila, de solo lectura"
        if field == ctx.submission_field:
            line += f" — {SUBMISSION_NOTE}"
        calculated = ctx.calculated.get(field)
        if calculated and calculated["kind"] == "row":
            line += f" — campo calculado por fila: {calculated['formula']}"
        grid = ctx.grid_fields.get(field)
        if grid and grid.get("kind") == "CHECKBOX":
            role = "la opción" if grid["role"] == "aspect" else "si la marcó (Sí/No)"
            line += (f" — columna virtual de la pregunta de casillas «{grid['question']}»: {role}, "
                     f"una fila por respuesta y opción")
        elif grid:
            role = "el aspecto" if grid["role"] == "aspect" else "la respuesta"
            line += (f" — columna virtual de la cuadrícula «{grid['question']}»: {role} de cada "
                     f"respuesta, una fila por aspecto")
        elif field in members:
            line += f" — una columna de «{members[field]}», en ancho"
        line += samples_note(field, ctx.samples, refs)
        lines.append(line)
    title = ("Columnas de la hoja para este widget (la hoja tiene más: usa solo estas)" if only
             else "Columnas de la hoja")
    text = shared + f"{title}:\n" + "\n".join(lines)
    if any(f in ctx.grid_fields for f in fields):
        text += "\n\n" + GRID_NOTE
    if ctx.aggregated_fields:
        def describe(name):
            info = ctx.calculated.get(name) or {}
            fmt = FORMAT_LABELS.get(info.get("format"))
            line = f"- {json.dumps(name, ensure_ascii=False)}{f' ({fmt})' if fmt else ''}"
            return f"{line}: {info['formula']}" if info.get("formula") else line
        text += ("\n\nCampos calculados agregados (solo como métrica, con agg \"auto\"), con su fórmula:\n"
                 + "\n".join(describe(name) for name in sorted(ctx.aggregated_fields)))
    return text


def _history_text(history: list[dict] | None) -> str:
    """Los mensajes previos del hilo: pedidos del usuario y formularios que propuso la IA."""
    lines = []
    for message in (history or [])[-MAX_HISTORY_MESSAGES:]:
        if not isinstance(message, dict):
            continue
        if message.get("role") == "user" and str(message.get("text") or "").strip():
            lines.append(f"- Usuario: {str(message['text']).strip()}")
        elif message.get("role") == "assistant" and isinstance(message.get("proposal"), dict):
            lines.append(f"- Tú propusiste: {json.dumps(_without_title(message['proposal']), ensure_ascii=False)}")
    return "Conversación previa:\n" + "\n".join(lines) if lines else ""


def _user_message(prompt: str, widget_type: str | None, ctx: SheetContext,
                  current: dict | None = None, history: list[dict] | None = None,
                  columns=None) -> str:
    parts = [columns_context(ctx, only=columns)]
    if widget_type:
        parts.append(f"El tipo de widget está fijado en: {widget_type}.")
    if current:
        parts.append(MODIFY_PROMPT)
        parts.append("current_widget:\n" + json.dumps(_current_for_ai(current, widget_type), ensure_ascii=False))
    previous = _history_text(history)
    if previous:
        parts.append(previous)
    parts.append(f"Pedido del usuario:\n{prompt}")
    return "\n\n".join(parts)


def _call_model(contents: str, ctx: SheetContext, widget_type: str | None,
                thinking_budget: int | None = None) -> tuple[str, dict]:
    api_key = settings.GEMINI_API_KEY
    if not api_key:
        raise SpecGenerationError("GEMINI_API_KEY no está configurado.")
    # Mantener la referencia al cliente: si se recolecta, cierra su conexión HTTP a mitad de la llamada.
    client = gemini_client(api_key)
    started = time.monotonic()
    response = client.models.generate_content(
        model=settings.GEMINI_MODEL,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=system_prompt_for(widget_type),
            tools=_tools(ctx, widget_type),
            tool_config=types.ToolConfig(function_calling_config=types.FunctionCallingConfig(
                mode=types.FunctionCallingConfigMode.ANY,
                allowed_function_names=[CREATE_TOOL, REJECT_TOOL],
            )),
            temperature=0,
            thinking_config=_thinking_config(thinking_budget),
        ),
    )
    _log_usage(f"widget:{widget_type or 'auto'}", response, started)
    calls = response.function_calls or []
    if not calls:
        raise SpecGenerationError("La IA no devolvió una especificación. Intenta reformular el pedido.")
    return calls[0].name, dict(calls[0].args or {})


def _audit(prompt, widget_type, attempt, call_name, args, errors):
    audit_logger.info(json.dumps({
        "prompt": prompt,
        "widget_type": widget_type,
        "attempt": attempt,
        "tool": call_name,
        "args": args,
        "valid": not errors,
        "errors": errors,
    }, ensure_ascii=False, default=str))


# ---------------------------------------------------------------- validación
def _range_label(name: str, rng) -> str:
    low, high = rng
    if low == high:
        return f"{name}: exactamente {low}"
    return f"{name}: entre {low} y {high}"


def _metric_errors(metric, index: int, ctx, seen_aliases) -> list[str]:
    path = f"metrics[{index}]"
    if not isinstance(metric, dict):
        return [f"{path}: debe ser un objeto."]
    errors = []
    agg = metric.get("agg")
    alias = metric.get("alias")

    if ctx.is_aggregated(metric.get("field")) or agg == "auto":
        # Campo calculado agregado: la agregación ya viene en su fórmula.
        if not ctx.is_aggregated(metric.get("field")):
            errors.append(f"{path}: agg 'auto' solo se usa con un campo calculado agregado.")
        elif agg != "auto":
            errors.append(f"{path}: '{metric['field']}' es un campo calculado agregado: "
                          f"usa agg 'auto' (la agregación ya está en su fórmula).")
        if metric.get("window"):
            w = metric["window"].get("type") if isinstance(metric["window"], dict) else None
            if w not in WINDOW_TYPES:
                errors.append(f"{path}: window.type '{w}' no existe; usa uno de {', '.join(WINDOW_TYPES)}.")
    else:
        if agg not in AGGREGATIONS:
            allowed = ", ".join(sorted(set(AGGREGATIONS) - {"auto"}))
            errors.append(f"{path}: agg '{agg}' no existe; usa uno de {allowed}.")
        if agg == "count" and not metric.get("field"):
            pass  # count sin campo: filas distintas (ID)
        elif is_id_column(metric.get("field")):
            errors.append(f"{path}: «ID» es la columna de sistema (una por fila, de solo "
                          f"lectura): el Conteo sin columna cuenta las filas; para contarlas "
                          f"en una fórmula usa COUNT([ID]).")
        elif not metric.get("field"):
            errors.append(f"{path}: falta 'field'.")
        elif metric["field"] not in ctx.fields:
            errors.append(f"{path}: la columna '{metric['field']}' no existe en la hoja.")
        elif agg in NUMERIC_AGGS and not ctx.is_numeric(metric["field"]):
            errors.append(f"{path}: '{agg}' solo se aplica a columnas numéricas; "
                          f"'{metric['field']}' es de texto. No conviertas sus respuestas en números: "
                          f"cuéntalas (agg 'count', con la columna como dimensión o pivote) o "
                          f"calcula el % de una o varias respuestas con un campo calculado.")
        if metric.get("window"):
            w = metric["window"].get("type") if isinstance(metric["window"], dict) else None
            if w not in WINDOW_TYPES:
                errors.append(f"{path}: window.type '{w}' no existe; usa uno de {', '.join(WINDOW_TYPES)}.")

    if not alias:
        errors.append(f"{path}: falta 'alias' (snake_case único, ej. total_ventas).")
    elif not ALIAS_RE.match(str(alias)):
        errors.append(f"{path}: alias '{alias}' debe ser minúsculas, sin espacios y empezar por "
                      f"una letra (ej. total_ventas).")
    elif alias in seen_aliases:
        errors.append(f"{path}: alias '{alias}' repetido.")
    elif alias in ctx.fields:
        errors.append(f"{path}: alias '{alias}' ya es el nombre de una columna de la hoja; usa otro.")

    label = metric.get("label")
    if label is not None and label != "" and (not isinstance(label, str) or len(label) > 80):
        errors.append(f"{path}: 'label' (nombre a mostrar) debe ser texto de hasta 80 caracteres.")
    if metric.get("format") not in (None, "", *METRIC_FORMATS):
        errors.append(f"{path}: format '{metric['format']}' no existe; usa uno de {', '.join(METRIC_FORMATS)}.")

    if metric.get("filters"):
        errors += condition_errors(metric["filters"], ctx, path=f"{path}.filters",
                                   max_in_values=MAX_IN_VALUES)
    return errors


def _column_errors(column, index: int, ctx, seen_columns) -> list[str]:
    """Una columna de `fields.columns`: "mes" o {"field": "mes", "label": "Mes"}."""
    path = f"columns[{index}]"
    if isinstance(column, str):
        column = {"field": column}
    if not isinstance(column, dict):
        return [f"{path}: debe ser el nombre de una columna o un objeto {{field, label}}."]
    errors = []
    unknown = sorted(set(column) - {"field", "label"})
    if unknown:
        errors.append(f"{path}: claves desconocidas: {', '.join(unknown)}.")
    field = str(column.get("field") or "").strip()
    if not field:
        errors.append(f"{path}: falta 'field'.")
        return errors
    if field not in ctx.fields:
        errors.append(f"{path}: la columna '{field}' no existe en la hoja.")
    elif field in seen_columns:
        errors.append(f"{path}: la columna '{field}' está repetida.")
    else:
        seen_columns.append(field)
    label = column.get("label")
    if label is not None and label != "" and (not isinstance(label, str) or len(label) > 80):
        errors.append(f"{path}: 'label' (nombre a mostrar) debe ser texto de hasta 80 caracteres.")
    return errors


def _column_scales_errors(items, ctx: SheetContext, caps: dict, style: dict | None = None) -> tuple[dict, list[str]]:
    """Las escalas que propone la IA (`column_scales`: [{field, scale, order?}]).
    → ({columna: {scale, order?}} válidas, errores)."""
    if not items:
        return {}, []
    if not isinstance(items, list):
        return {}, ["column_scales debe ser una lista."]
    if not caps.get("columns_scaled"):
        return {}, ["column_scales: este widget no usa la escala de las columnas."]

    style = style or {}
    ignore_na = bool(style.get("ignoreNonApplicable"))
    ignored_raw = style.get("ignoredValues") if ignore_na else None
    if ignored_raw is None and ignore_na:
        ignored_raw = DEFAULT_IGNORED_VALUES
    ignored_set = parse_ignored_values(ignored_raw) if ignore_na else set()

    valid, errors = {}, []
    for i, item in enumerate(items):
        path = f"column_scales[{i}]"
        if not isinstance(item, dict):
            errors.append(f"{path}: debe ser un objeto {{field, scale, order?}}.")
            continue
        name, scale, order = item.get("field"), item.get("scale"), item.get("order") or None
        if name not in ctx.fields:
            errors.append(f"{path}: la columna '{name}' no existe en la hoja.")
            continue
        found = scale_errors(name, scale, order)
        if not found and scale == "ordinal":
            values = (ctx.scales.get(name) or {}).get("stats", {}).get("values")
            if not order:
                found = [f"'{name}': una ordinal necesita `order`, sus valores de menor a mayor"]
            elif values:
                known = {normalize_text(v) for v in order}
                missing = [v for v in values if normalize_text(v) not in known and normalize_text(v) not in ignored_set]
                if missing:
                    found = [f"el orden de '{name}' no incluye: {', '.join(missing[:5])}"]
        if found:
            errors += [f"{path}: {e}" for e in found]
            continue
        valid[name] = {"scale": scale, **({"order": order} if scale == "ordinal" else {})}
    return valid, errors


def _same_formula(text: str, ctx: SheetContext) -> str | None:
    """El campo calculado de la fuente con la misma fórmula (mismo árbol: da igual cómo esté
    escrita), o None."""
    tree = formula_tree(text)
    for name, info in ctx.calculated.items():
        if tree is not None and formula_tree(info["formula"]) == tree:
            return name
    return None


def _calculated_fields_errors(items, ctx: SheetContext) -> tuple[list[str], list[str]]:
    """Los `calculated_fields` de una propuesta: [{name, formula, format}], siempre agregados.
    → (nombres válidos, errores)."""
    if items in (None, []):
        return [], []
    if not isinstance(items, list):
        return [], ["calculated_fields debe ser una lista."]
    if len(items) > MAX_NEW_CALCULATED:
        return [], [f"calculated_fields: como mucho {MAX_NEW_CALCULATED}."]
    names, errors = [], []
    for i, item in enumerate(items):
        path = f"calculated_fields[{i}]"
        if not isinstance(item, dict):
            errors.append(f"{path}: debe ser un objeto {{name, formula, format}}.")
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            errors.append(f"{path}: falta 'name'.")
            continue
        if name in ctx.fields or name in ctx.aggregated_fields or name in names:
            errors.append(f"{path}: ya existe una columna o un campo llamado '{name}'; usa otro nombre.")
            continue
        if item.get("format", "number") not in FORMATS:
            errors.append(f"{path}: format debe ser uno de {', '.join(FORMATS)}.")
        try:
            formula = compile_formula(str(item.get("formula") or ""), ctx.sheet_fields, ctx.aggregated_fields)
        except FormulaError as e:
            errors.append(f"{path}: {e}")
            continue
        if not formula.aggregated:
            errors.append(f"{path}: la fórmula debe ser agregada (cada columna dentro de SUM, AVG, "
                          f"COUNT, COUNT_DISTINCT, MIN o MAX).")
            continue
        same = _same_formula(formula.text, ctx)
        if same:
            errors.append(f"{path}: ya existe el campo '{same}' con esa fórmula: no lo propongas, "
                          f"úsalo con agg 'auto' y field '{same}'.")
            continue
        names.append(name)
    return names, errors


def form_errors(data: dict, ctx: SheetContext, widget_type: str | None,
                require_title: bool = True) -> list[str]:
    """Valida un WidgetForm propuesto contra la hoja y las capacidades del widget. La propuesta
    de la IA no trae título (`require_title=False`): lo pone el usuario."""
    data = _resolve_columns(data, ctx)
    resolved = data.get("widget_type")
    if not resolved or resolved not in WIDGETS:
        allowed = ", ".join(w.key for w in WIDGETS if w.ai_enabled)
        return [f"widget_type: '{resolved}' no es válido; usa uno de {allowed}."]
    if widget_type and resolved != widget_type:
        return [f"widget_type: debe ser '{widget_type}'."]
    if not WIDGETS.get(resolved).ai_enabled:
        return [f"widget_type: '{resolved}' no lo propone la IA; usa uno de "
                f"{', '.join(w.key for w in WIDGETS if w.ai_enabled)}."]

    definition = WIDGETS.get(resolved)
    caps = definition.capabilities or {}
    fields = data.get("fields") if isinstance(data.get("fields"), dict) else {}
    errors: list[str] = []

    # Los campos calculados que propone la IA: válidos contra la hoja, y desde aquí el resto
    # del form los puede usar como métricas «auto».
    new_fields, calc_errors = _calculated_fields_errors(data.get("calculated_fields"), ctx)
    errors += calc_errors
    # Un campo propuesto con la fórmula mal ya tiene su error: las métricas que lo usan no
    # suman otro («agg 'auto' solo con un campo agregado»), que esconde el motivo real.
    items = data.get("calculated_fields") if isinstance(data.get("calculated_fields"), list) else []
    proposed_names = {str(item.get("name") or "").strip() for item in items if isinstance(item, dict)}
    proposed_names -= set(ctx.fields) | {""}
    if new_fields or proposed_names:
        ctx = dataclasses.replace(ctx, aggregated_fields=ctx.aggregated_fields | set(new_fields) | proposed_names)

    # Las escalas que propone la IA para las variables: válidas contra la hoja, y desde aquí
    # la validación de las columnas las usa en vez de las detectadas.
    proposed, scale_errs = _column_scales_errors(data.get("column_scales"), ctx, caps, style=data.get("style"))
    errors += scale_errs
    if proposed:
        ctx = dataclasses.replace(ctx, scales={**ctx.scales, **{
            name: {**ctx.scales.get(name, {}), **item} for name, item in proposed.items()}})

    for key in fields:
        if key not in {"dimensions", "pivots", "metrics", "columns", "filters",
                       "sort_by", "limit", "trend_by"}:
            errors.append(f"fields: '{key}' no es un campo de datos válido.")

    dimensions = fields.get("dimensions") or []
    if not isinstance(dimensions, list):
        errors.append("fields.dimensions debe ser una lista.")
        dimensions = []
    if "dimensions" in caps:
        if not caps["dimensions"][0] <= len(dimensions) <= caps["dimensions"][1]:
            errors.append(f"fields.dimensions: {_range_label('dimensiones', caps['dimensions'])}.")
    for dim in dimensions:
        if dim not in ctx.fields:
            errors.append(f"fields.dimensions: la columna '{dim}' no existe en la hoja.")

    trend_by = fields.get("trend_by")
    if trend_by:
        if not caps.get("trend"):
            errors.append(f"fields.trend_by: «{definition.label}» no muestra tendencia.")
        elif trend_by not in ctx.fields:
            errors.append(f"fields.trend_by: la columna '{trend_by}' no existe en la hoja.")
        elif trend_by not in ctx.time_fields:
            errors.append(f"fields.trend_by: '{trend_by}' no es una columna de tiempo (año, mes, "
                          f"fecha): la tendencia es la evolución en el tiempo. Usa una de tiempo u "
                          f"omite trend_by.")
        elif trend_by in dimensions:
            errors.append("fields.trend_by: ya está en las dimensiones.")

    pivots = fields.get("pivots") or []
    if not isinstance(pivots, list):
        errors.append("fields.pivots debe ser una lista.")
        pivots = []
    if "pivots" in caps:
        if not caps["pivots"][0] <= len(pivots) <= caps["pivots"][1]:
            errors.append(f"fields.pivots: {_range_label('pivotes', caps['pivots'])}.")
    for pivot in pivots:
        if pivot not in ctx.fields:
            errors.append(f"fields.pivots: la columna '{pivot}' no existe en la hoja.")
    if pivots and not caps.get("pivots"):
        errors.append("fields.pivots: este widget no admite pivotes.")
    overlap = sorted({str(p) for p in pivots} & {str(d) for d in dimensions})
    if overlap:
        errors.append(f"fields.pivots: {', '.join(overlap)} no puede estar también en las dimensiones.")

    columns = fields.get("columns") or []
    if not isinstance(columns, list):
        errors.append("fields.columns debe ser una lista.")
        columns = []
    if columns and not caps.get("columns"):
        errors.append("fields.columns: este widget no admite columnas.")
    if "columns" in caps and not caps["columns"][0] <= len(columns) <= caps["columns"][1]:
        errors.append(f"fields.columns: {_range_label('columnas', caps['columns'])}.")
    seen_columns: list[str] = []
    matrix_rows = 0
    for i, column in enumerate(columns):
        errors += _column_errors(column, i, ctx, seen_columns)
        # Widgets que calculan con las columnas (correlación): solo numéricas.
        name = column.get("field") if isinstance(column, dict) else column
        if caps.get("columns_numeric") and name in ctx.fields and not ctx.is_numeric(name):
            errors.append(f"fields.columns[{i}]: '{name}' no es numérica: este widget solo usa "
                          "columnas con números.")
        # Widgets que miden la asociación según la escala (matriz de correlación).
        if caps.get("columns_scaled") and name in ctx.fields:
            if ctx.scale_of(name) == "none":
                errors.append(f"fields.columns[{i}]: '{name}' no se puede relacionar (es un "
                              "identificador, una fecha o texto libre).")
            elif ctx.scale_of(name) == "ordinal" and not ctx.order_of(name):
                errors.append(f"fields.columns[{i}]: '{name}' es ordinal y le falta el orden de "
                              "sus valores.")
            # Una nominal es una fila 0/1 por categoría (una sola si es Sí/No).
            dummies = dummy_count(ctx.scales.get(name)) if ctx.scale_of(name) == "nominal" else 1
            limit = caps.get("nominal_max_categories")
            if limit and dummies > limit:
                errors.append(f"fields.columns[{i}]: '{name}' tiene {dummies} categorías y la matriz "
                              f"admite hasta {limit} por variable: usa la V de Cramér o agrúpalas "
                              "con un campo calculado.")
            matrix_rows += dummies
    if caps.get("matrix_max_rows") and matrix_rows > caps["matrix_max_rows"]:
        errors.append(f"fields.columns: la matriz tendría {matrix_rows} filas (cada categoría de una "
                      f"nominal es una fila); el máximo es {caps['matrix_max_rows']}.")

    metrics = fields.get("metrics") or []
    if not isinstance(metrics, list):
        errors.append("fields.metrics debe ser una lista.")
        metrics = []
    low, high = caps.get("metrics", (0, 0))
    if not low <= len(metrics) <= high:
        errors.append(f"fields.metrics: {_range_label('métricas', caps.get('metrics', (0, 0)))}.")
    aliases: list[str] = []
    metric_filters_ok = admits_metric_filters(definition)
    for i, metric in enumerate(metrics):
        errors += _metric_errors(metric, i, ctx, aliases)
        if isinstance(metric, dict) and metric.get("filters") and not metric_filters_ok:
            errors.append(f"metrics[{i}].filters: con una sola métrica usa fields.filters.")
        window = metric.get("window") if isinstance(metric, dict) else None
        if isinstance(window, dict) and window.get("type") in WINDOW_TYPES:
            error = _window_error(f"metrics[{i}].window", window["type"], definition, pivots,
                                  metric.get("agg"))
            if error:
                errors.append(error)
            elif not dimensions and not pivots:
                errors.append(f"metrics[{i}].window: sin agrupar hay un solo valor por métrica y no "
                              "hay con qué compararlo; quítalo o agrupa por una columna.")
        if isinstance(metric, dict) and metric.get("alias"):
            aliases.append(metric["alias"])

    # Sin agrupar, un widget que lo admite (barras) compara totales: una barra por métrica.
    ungrouped_min = caps.get("ungrouped_min_metrics")
    if ungrouped_min and not dimensions:
        if len(metrics) < ungrouped_min:
            errors.append(f"fields.metrics: sin dimensión el gráfico compara totales: elige al menos "
                          f"{ungrouped_min} métricas o agrupa por una columna.")
        if pivots:
            errors.append("fields.pivots: sin dimensión no hay grupos que cruzar; agrega una "
                          "dimensión o quita el pivote.")

    # Gráficos: con pivote cada serie es un valor del pivote, así que solo cabe una métrica.
    pivot_max = caps.get("pivot_max_metrics")
    if pivot_max and pivots and len(aliases) > pivot_max:
        count = "UNA métrica" if pivot_max == 1 else f"{pivot_max} métricas"
        errors.append(f"fields: con pivotes este gráfico admite {count}; quita las demás "
                      "o quita los pivotes.")

    if fields.get("filters"):
        if not caps.get("filters"):
            errors.append("fields.filters: este widget no admite filtros propios.")
        errors += condition_errors(fields["filters"], ctx, path="fields.filters",
                                   max_in_values=MAX_IN_VALUES)

    if fields.get("sort_by"):
        if not caps.get("sort"):
            errors.append("fields.sort_by: este widget no admite orden.")
        column = str(fields["sort_by"]).lstrip("-")
        if column not in ctx.fields and column not in aliases:
            errors.append(f"fields.sort_by: '{column}' no es una columna ni un alias de métrica.")
    if fields.get("limit") is not None:
        if not caps.get("limit"):
            errors.append("fields.limit: este widget no admite límite.")
        elif not isinstance(fields["limit"], int) or not 1 <= fields["limit"] <= MAX_LIMIT:
            errors.append(f"fields.limit: debe ser un número entre 1 y {MAX_LIMIT}.")

    style = data.get("style") if isinstance(data.get("style"), dict) else {}
    known = {c["key"]: c for c in definition.style_schema or []}
    for key, value in style.items():
        if key not in known:
            errors.append(f"style: '{key}' no es un control de este widget.")
            continue
        control = known[key]
        if control.get("type") == "choice":
            if control.get("options_from") == "metrics":
                # Roles del KPI: el valor es un alias de las métricas propuestas, una
                # opción estática del schema (ej. «Valor fijo») o vacío.
                allowed = [""] + [o["value"] for o in control.get("options", []) if o["value"]] + aliases
                if value not in allowed:
                    errors.append(f"style.{key}: '{value}' no es una métrica de este widget; "
                                  f"usa uno de sus alias ({', '.join(a for a in aliases) or 'ninguno'}).")
            else:
                allowed = [o["value"] for o in control.get("options", [])]
                if value not in allowed:
                    errors.append(f"style.{key}: '{value}' no es válido; usa uno de "
                                  f"{', '.join(str(v) for v in allowed)}.")
        elif control.get("type") == "number" and not isinstance(value, (int, float)):
            errors.append(f"style.{key}: debe ser un número.")
        elif control.get("type") == "boolean" and not isinstance(value, bool):
            errors.append(f"style.{key}: debe ser true o false.")
        elif control.get("type") == "string" and not isinstance(value, str):
            errors.append(f"style.{key}: debe ser un texto.")
        elif control.get("type") == "list" and not (
                isinstance(value, list) and all(isinstance(v, str) for v in value)):
            errors.append(f"style.{key}: debe ser una lista de textos.")
        elif control.get("type") == "object" and not isinstance(value, dict):
            errors.append(f"style.{key}: debe ser un objeto.")

    if style.get("statusBasis") == "target_pct" and not _has_target(style, aliases):
        errors.append("style.statusBasis: «% de la meta» necesita una meta (target o targetMetric).")

    if require_title and not str(data.get("title") or "").strip():
        errors.append("title: el widget necesita un título.")

    errors += _grid_errors(fields, ctx, definition)
    return errors


def _grid_errors(fields: dict, ctx: SheetContext, definition) -> list[str]:
    """Las columnas virtuales de una cuadrícula (ver engine/steps/grid.py): de una sola por
    widget y solo en los que desdoblan la cuadrícula al consultar."""
    used = {}
    for name in query_names(fields):
        if name in ctx.grid_fields:
            used.setdefault(ctx.grid_fields[name]["question"], name)
    if not used:
        return []
    if not definition.grid_fields:
        return [f"fields: «{definition.label}» no usa columnas de cuadrícula ('{next(iter(used.values()))}'); "
                f"usa las columnas de cada aspecto."]
    if len(used) > 1:
        return ["fields: un widget usa las columnas de una sola cuadrícula; "
                f"{' y '.join(repr(q) for q in used)} no se pueden combinar."]
    return []



def _has_target(style: dict, aliases: list[str]) -> bool:
    """¿El style define una meta: por métrica elegida o por valor fijo?"""
    metric = style.get("targetMetric")
    if metric == "fixed":
        return style.get("target") not in (None, "", 0)
    if metric:
        return metric in aliases
    return False


def _normalize(args: dict) -> dict:
    """La respuesta de la tool, en el formato del request del builder."""
    data = copy.deepcopy(args) if isinstance(args, dict) else {}
    fields = data.get("fields") if isinstance(data.get("fields"), dict) else {}
    style = data.get("style") if isinstance(data.get("style"), dict) else {}
    calculated = data.get("calculated_fields") if isinstance(data.get("calculated_fields"), list) else []
    return {
        "widget_type": data.get("widget_type"),
        "title": "",
        "calculated_fields": [
            {"name": str(f.get("name") or "").strip(), "formula": str(f.get("formula") or "").strip(),
             "format": f.get("format") or "number"}
            for f in calculated if isinstance(f, dict)
        ],
        "fields": {k: v for k, v in fields.items() if v is not None},
        "style": {k: v for k, v in style.items() if k not in NOT_AI_STYLE_KEYS},
        "column_scales": [
            {"field": str(c.get("field") or "").strip(), "scale": c.get("scale"),
             **({"order": [str(v).strip() for v in c["order"]]}
                if c.get("scale") == "ordinal" and isinstance(c.get("order"), list) else {})}
            for c in (data.get("column_scales") if isinstance(data.get("column_scales"), list) else [])
            if isinstance(c, dict)
        ],
    }


def _column_key(name) -> str:
    """Clave para comparar nombres de columna sin importar espacios, mayúsculas, acentos ni puntuación final."""
    text = str(name).strip()
    text = re.sub(r"[.,;:!]+\s*\]", "]", text)
    text = text.rstrip(".,;:}]")
    text = unicodedata.normalize("NFD", text).encode("ASCII", "ignore").decode("ASCII")
    return " ".join(text.split()).casefold()


def _resolve_columns(data: dict, ctx: SheetContext) -> dict:
    """Cambia las columnas de la propuesta por el nombre real de la hoja:
    - Traduce alias de columnas (col_1, col_2, ...) a sus nombres reales.
    - Corrige nombres que solo difieren en espacios, mayúsculas, acentos o puntuación final.
    - Traduce alias dentro de las fórmulas de calculated_fields si las hay.
    Lo que no coincide queda igual y lo rechaza la validación."""
    known = (*ctx.fields, *sorted(ctx.aggregated_fields))
    matches: dict[str, list[str]] = {}
    for field in known:
        matches.setdefault(_column_key(field), []).append(field)
    real = {key: names[0] for key, names in matches.items() if len(names) == 1}

    def resolve(value: str) -> str:
        if not value:
            return value
        val_clean = str(value).strip()
        val_unbracket = val_clean.strip("[]")
        if val_unbracket.lower() in ctx.aliases:
            return ctx.aliases[val_unbracket.lower()]
        if val_clean in known:
            return val_clean
        if val_unbracket in known:
            return val_unbracket
        return real.get(_column_key(val_clean), val_clean)

    fields, style = map_columns(data.get("fields"), data.get("style"), resolve)
    data["fields"] = fields
    if "style" in data:
        data["style"] = style

    for item in data.get("column_scales") or []:
        if isinstance(item, dict) and isinstance(item.get("field"), str):
            item["field"] = resolve(item["field"])

    if "calculated_fields" in data and isinstance(data["calculated_fields"], list):
        alias_map = dict(ctx.aliases)
        for k, v in list(ctx.aliases.items()):
            alias_map[k.upper()] = v
            alias_map[k.capitalize()] = v
        for calc in data["calculated_fields"]:
            if isinstance(calc, dict) and "formula" in calc and isinstance(calc["formula"], str):
                calc["formula"] = rename_columns(calc["formula"], alias_map)

    return data


def _omit_unchanged_scales(data: dict, ctx: SheetContext) -> None:
    """Una escala idéntica a la efectiva no debe volver a guardarse como corrección de la IA.
    Solo se llama después de validar: una propuesta inválida nunca se descarta en silencio."""
    changed = []
    for item in data.get("column_scales") or []:
        current = ctx.scales.get(item["field"]) or {}
        same = item["scale"] == current.get("scale")
        if same and item["scale"] == "ordinal":
            same = ([normalize_text(v) for v in item["order"]]
                    == [normalize_text(v) for v in current.get("order") or []])
        if not same:
            changed.append(item)
    data["column_scales"] = changed


def _correlation_retry_context(data: dict, ctx: SheetContext, errors: list[str]) -> str:
    """Perfiles de las columnas con escalas inválidas, sin mezclar sus valores ni sus alias."""
    items = data.get("column_scales") or []
    profiles = {}
    for error in errors:
        match = re.match(r"column_scales\[(\d+)\]:", error)
        if not match or int(match[1]) >= len(items):
            continue
        field = items[int(match[1])].get("field")
        if field not in ctx.scales:
            continue
        profile = ctx.scales[field]
        profiles[ctx.alias_for(field)] = {
            "field": field, "scale": profile["scale"], "saved": profile.get("saved", False),
            "order": profile.get("order"), "values": profile.get("stats", {}).get("values"),
        }
    text = ("\n\nCorrección de la matriz: conserva la selección válida, el método y los filtros. "
            "Verifica todas sus columnas y sus escalas, no solo el número de variables. "
            "Omite column_scales para conservar las escalas efectivas; no inventes órdenes "
            "para sortear los límites de filas ni copies los de otra columna.")
    if profiles:
        text += ("\nEscalas efectivas y valores reales de las columnas con errores "
                 "(cada lista pertenece únicamente a su columna):\n"
                 + json.dumps(profiles, ensure_ascii=False, default=str))
    return text


def generate_widget_form(prompt: str, widget_type: str | None, ctx: SheetContext,
                         current: dict | None = None, history: list[dict] | None = None,
                         columns: list[str] | None = None) -> dict:
    """
    Genera `{widget_type, title, fields, style}` para `prompt` sobre la hoja de `ctx` (con
    `samples` para que la IA escriba los valores exactos). `title` va siempre vacío: el título
    de la tarjeta lo pone el usuario, la IA no lo genera.

    Con `current` (`{title, fields, style}` del widget que se edita) la IA ajusta ese estado en
    vez de crear desde cero; `history` son los mensajes previos del hilo
    (`{"role": "user", "text"}` / `{"role": "assistant", "proposal"}`).

    `columns` (las que el plan de tablero asignó al widget) acota las columnas que ve la IA en
    el primer intento; el reintento ve la hoja completa, por si el plan omitió alguna.

    Si la primera propuesta no pasa la validación, reintenta una vez con los errores. Solo
    en correlación, si ese reintento introduce errores distintos, permite una última reparación
    con los valores reales de las columnas afectadas. Nunca devuelve un form inválido ni
    guarda correcciones de escala idénticas a las efectivas.
    """
    if widget_type is not None and widget_type not in WIDGETS:
        raise SpecGenerationError(f"Tipo de widget desconocido: {widget_type}.")
    if not ctx.fields:
        raise SpecGenerationError("La hoja no tiene columnas.")

    columns = [c for c in columns or [] if c in ctx.fields]
    contents = _user_message(prompt, widget_type, ctx, current, history, columns=columns)
    errors: list[str] = []
    previous_errors: list[str] = []
    for attempt in (1, 2, 3):
        call_name, args = _call_model(contents, ctx, widget_type,
                                      THINKING_BUDGETS["widget"] if attempt == 1 else None)

        if call_name == REJECT_TOOL:
            _audit(prompt, widget_type, attempt, call_name, args, [])
            raise SpecGenerationError(args.get("reason") or "La IA no pudo interpretar el pedido.")

        data = _resolve_columns(_normalize(args), ctx)
        errors = form_errors(data, ctx, widget_type, require_title=False)
        _audit(prompt, widget_type, attempt, call_name, args, errors)

        if not errors:
            # El form validado se re-parsea por el dataclass: lo que guarda es lo que se ejecuta.
            WidgetForm.from_dict({"fields": data["fields"], "style": data["style"]})
            _omit_unchanged_scales(data, ctx)
            return data

        correlation = (widget_type or data["widget_type"]) == "correlation"
        if attempt >= 2 and (attempt == 3 or not correlation or not set(errors).difference(previous_errors)):
            break

        contents = (
            f"{_user_message(prompt, widget_type, ctx, current, history)}\n\n"
            f"Tu respuesta anterior fue:\n{json.dumps(args, ensure_ascii=False)}\n\n"
            f"No es válida por estos errores:\n- " + "\n- ".join(errors) +
            "\n\nCorrígela y vuelve a llamar a create_widget."
        )
        if correlation:
            contents += _correlation_retry_context(data, ctx, errors)
            if previous_errors:
                contents += ("\nErrores de la propuesta anterior que no debes reintroducir:\n- "
                             + "\n- ".join(previous_errors))
        previous_errors = errors

    raise SpecGenerationError(
        "No se pudo generar un widget válido para ese pedido. Detalle: " + " ".join(errors)
    )
