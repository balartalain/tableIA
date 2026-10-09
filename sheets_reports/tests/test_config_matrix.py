"""
Matriz de configuraciones: todo lo que el panel permite armar, generado a partir de las
`capabilities` de cada widget (un widget nuevo queda cubierto sin escribir nada).

Para cada combinación:
- si la validación (`form_errors`, la misma del panel y de la IA) la rechaza, los mensajes son
  texto;
- si `debe_rechazarse` dice que no tiene sentido, la validación tiene que rechazarla;
- si se acepta, el widget se dibuja sin error, el JSON no trae NaN ni infinitos y cumple los
  invariantes de su tipo (`INVARIANTS`: los % suman 100, el acumulado no baja, los formatos
  cubren todas las columnas…).

Widget nuevo: agregar su entrada en `INVARIANTS` (lo exige test_architecture).
"""
import itertools
import json

from django.test import SimpleTestCase

from sheets_reports.engine import AGGREGATIONS
from sheets_reports.engine.context import SheetContext
from sheets_reports.engine.formulas import apply_calculated_fields
from sheets_reports.services.ai_spec import (ADDITIVE_AGGS, ADDITIVE_WINDOWS, WINDOW_TYPES, form_errors,
                                             panel_options)
from sheets_reports.tests.fixtures import sales_df
from sheets_reports.widgets import WIDGETS

CALCULATED = "Participación Hogar"
CALCULATED_FIELDS = [{
    "name": CALCULATED, "format": "percent",
    "formula": 'SUM(IF([categoria] = "Hogar", [ventas], 0)) / SUM([ventas]) * 100',
}]
# Columnas por las que agrupar, en este orden: texto, periodo por nombre, año numérico.
GROUP_COLUMNS = ("categoria", "mes", "anio")
TIME_COLUMNS = {"mes", "anio"}
TEXT_COLUMNS = {"categoria", "mes"}

# Una métrica por agregación (y una inválida: suma de una columna de texto).
METRICS = [
    ("sum", "ventas"), ("avg", "ventas"), ("median", "ventas"), ("min", "ventas"),
    ("max", "ventas"), ("std", "ventas"), ("count", None), ("count_distinct", "categoria"),
    ("auto", CALCULATED), ("sum", "categoria"),
]
# Lo que se agrega a la métrica principal: nada, un conteo al lado, o la misma con condición.
COMPANIONS = ("sola", "con_conteo", "con_condicion")


def matrix_df():
    return apply_calculated_fields(sales_df(), CALCULATED_FIELDS)


def window_of(metric) -> str | None:
    return ((metric or {}).get("window") or {}).get("type")


# ------------------------------------------------------------------ reglas semánticas
def debe_rechazarse(fields: dict) -> list[str]:
    """Configuraciones que se pueden armar pero no significan nada: la validación tiene que
    rechazarlas. Una regla por línea; el motivo es lo que se informa si se acepta."""
    reasons = []
    for metric in fields.get("metrics") or []:
        agg, field, window = metric.get("agg"), metric.get("field"), window_of(metric)
        if window in ADDITIVE_WINDOWS and agg not in ADDITIVE_AGGS:
            reasons.append(f"'{window}' suma los grupos y '{agg}' no se suma")
        if agg in ("sum", "avg", "median", "min", "max", "std") and field in TEXT_COLUMNS:
            reasons.append(f"'{agg}' sobre la columna de texto '{field}'")
        if window == "percent_of_row" and not fields.get("pivots"):
            reasons.append("'percent_of_row' sin pivote da siempre 100 %")
        if window and not fields.get("dimensions") and not fields.get("pivots"):
            reasons.append(f"'{window}' sin agrupar: un solo valor, nada con qué compararlo")
    trend = fields.get("trend_by")
    if trend and trend not in TIME_COLUMNS:
        reasons.append(f"tendencia por '{trend}', que no es de tiempo")
    return reasons


# ------------------------------------------------------------------ generación
def _metric(agg, field, alias, window=None, **extra):
    metric = {"agg": agg, "alias": alias, **extra}
    if field:
        metric["field"] = field
    if window:
        metric["window"] = {"type": window}
    return metric


def _metric_configs(caps: dict):
    """Dimensiones × pivotes × métrica × ventana × acompañante (× tendencia), dentro de los
    límites del widget."""
    d_low, d_high = caps.get("dimensions", (0, 0))
    p_low, p_high = caps.get("pivots", (0, 0))
    m_high = caps.get("metrics", (0, 0))[1]
    trends = (None, "mes", "categoria") if caps.get("trend") else (None,)
    for n_dims in range(d_low, min(d_high, len(GROUP_COLUMNS)) + 1):
        dims = list(GROUP_COLUMNS[:n_dims])
        for n_piv in range(p_low, min(p_high, len(GROUP_COLUMNS) - n_dims) + 1):
            pivots = list(GROUP_COLUMNS[n_dims:n_dims + n_piv])
            for (agg, field), window, companion, trend in itertools.product(
                    METRICS, (None, *WINDOW_TYPES), COMPANIONS, trends):
                if companion != "sola" and m_high < 2:
                    continue
                if trend in dims:
                    continue
                metrics = [_metric(agg, field, "principal", window)]
                if companion == "con_conteo":
                    metrics.append(_metric("count", None, "cantidad"))
                elif companion == "con_condicion":
                    metrics.append(_metric("sum", "ventas", "ventas_2026",
                                           filters=[{"field": "anio", "op": "eq", "value": 2026}]))
                yield {"dimensions": dims, "pivots": pivots, "metrics": metrics, "filters": [],
                       "trend_by": trend, "sort_by": None, "limit": None}


def _column_configs(caps: dict):
    """Widgets de columnas sueltas: columnas × orden × filtro × (si la admiten) dimensión."""
    dimension_options = ([], ["categoria"]) if caps.get("dimensions", (0, 0))[1] else ([],)
    for columns, sort_by, filters, dimensions in itertools.product(
            (["categoria"], ["categoria", "ventas"], ["ventas", "mes", "anio"], ["ventas", "anio"]),
            (None, "-ventas"),
            ([], [{"field": "anio", "op": "eq", "value": 2026}]),
            dimension_options):
        yield {"dimensions": dimensions, "pivots": [], "metrics": [], "filters": filters,
               "columns": [{"field": c} for c in columns],
               "sort_by": sort_by if sort_by and sort_by.lstrip("-") in columns else None,
               "limit": None}


def _dimension_configs(caps: dict):
    """Widgets sin métricas que solo eligen columnas (los filtros del tablero)."""
    low, high = caps.get("dimensions", (0, 0))
    for n in range(low, min(high, len(GROUP_COLUMNS)) + 1):
        yield {"dimensions": list(GROUP_COLUMNS[:n]), "pivots": [], "metrics": [], "filters": []}


def configs_for(widget):
    caps = widget.capabilities or {}
    if caps.get("metrics", (0, 0))[1] > 0:
        return _metric_configs(caps)
    if caps.get("columns", (0, 0))[1] > 0:
        return _column_configs(caps)
    return _dimension_configs(caps)


# ------------------------------------------------------------------ invariantes
def _close(total, expected=100.0, items=1) -> bool:
    # Cada valor viene redondeado a 2 decimales: el error crece con la cantidad.
    return abs(total - expected) <= 0.006 * max(items, 1) + 0.01


def _sums_100(values) -> bool:
    values = [v for v in values if v is not None]
    return not values or not any(values) or _close(sum(values), items=len(values))


def _chart(data, fields) -> list[str]:
    problems = []
    categories, series = data["categories"], data["series"]
    for s in series:
        if len(s["data"]) != len(categories):
            problems.append(f"la serie «{s['name']}» no tiene un valor por categoría")
    metrics, pivots = fields["metrics"], fields.get("pivots") or []
    if pivots:
        window = window_of(metrics[0])
        if window == "percent_of_total" and not all(_sums_100(s["data"]) for s in series):
            problems.append("con pivote, «% del total» no suma 100 en cada serie")
        if window == "percent_of_row" and not all(
                _sums_100([s["data"][i] for s in series]) for i in range(len(categories))):
            problems.append("«% del total de la fila» no suma 100 en cada categoría")
    else:
        for metric, s in zip(metrics, series):
            window = window_of(metric)
            if window == "percent_of_total" and not _sums_100(s["data"]):
                problems.append(f"«% del total» de {metric['alias']} no suma 100")
            values = [v for v in s["data"] if v is not None]
            if window == "running_total" and values != sorted(values):
                problems.append(f"el acumulado de {metric['alias']} baja")
    return problems


def _donut(data, fields) -> list[str]:
    if len(data["series"]) != len(data["labels"]):
        return ["la dona no tiene un valor por etiqueta"]
    return []


def _kpi(data, fields) -> list[str]:
    problems = []
    if not isinstance(data.get("value"), (int, float)):
        problems.append("el KPI no tiene un número")
    trend = data.get("trend")
    if trend and len(trend.get("data") or []) != len(trend.get("categories") or []):
        problems.append("la tendencia no tiene un valor por periodo")
    return problems


def _leaves(columns, inherited=None):
    """Columnas hoja de la tabla dinámica, con si son de total o subtotal."""
    for col in columns or []:
        flags = {**(inherited or {}), **{k: True for k in ("total", "subtotal") if col.get(k)}}
        if col.get("children"):
            yield from _leaves(col["children"], flags)
        else:
            yield {**col, **flags}


def _belongs(field: str, alias: str) -> bool:
    return field == alias or field.endswith("." + alias)


def _dynamic_table(data, fields) -> list[str]:
    problems = []
    row_fields = set(data.get("rowFields") or [])
    values = [c for c in _leaves(data["columns"]) if c["field"] not in row_fields]
    rows = [r for r in data["rows"] if not r.get("__subtotal")]
    percent, formats = set(data.get("percent") or []), data.get("formats") or {}
    flat = len(fields.get("dimensions") or []) <= 1      # sin filas de subtotal
    for metric in fields["metrics"]:
        alias, window = metric["alias"], window_of(metric)
        own = [c for c in values if _belongs(c["field"], alias)]
        if not own:
            problems.append(f"la métrica {alias} no tiene columnas")
            continue
        if window and window.startswith("percent_"):
            missing = [c["field"] for c in own if c["field"] not in percent]
            if missing:
                problems.append(f"columnas de {alias} sin formato %: {missing}")
        if metric.get("format") not in (None, "number"):
            missing = [c["field"] for c in own if formats.get(c["field"]) != metric["format"]]
            if missing:
                problems.append(f"columnas de {alias} sin su formato {metric['format']}: {missing}")
        if window == "percent_of_total" and flat:
            for c in own:
                if not c.get("subtotal") and not _sums_100([r.get(c["field"]) for r in rows]):
                    problems.append(f"«% del total» de la columna {c['field']} no suma 100")
        if window == "percent_of_row":
            cells = [c for c in own if not c.get("total") and not c.get("subtotal")]
            for r in rows:
                if not _sums_100([r.get(c["field"]) for c in cells]):
                    problems.append(f"«% del total de la fila» no suma 100 en {r}")
                    break
        if not window and metric["agg"] in ADDITIVE_AGGS and flat and data.get("totals"):
            for c in own:
                if c.get("subtotal"):
                    continue
                expected = sum(r.get(c["field"]) or 0 for r in rows)
                shown = data["totals"].get(c["field"])
                if shown is not None and abs(shown - expected) > 0.01:
                    problems.append(f"el total de {c['field']} ({shown}) no es la suma de sus filas ({expected})")
    return problems


def _table(data, fields) -> list[str]:
    wanted = [c["field"] for c in fields["columns"]]
    shown = [c["field"] for c in data["columns"]]
    problems = [] if shown == wanted else [f"columnas {shown} en vez de {wanted}"]
    if fields.get("limit") and len(data["rows"]) > fields["limit"]:
        problems.append("el límite no se respeta")
    return problems


def _filter(data, fields) -> list[str]:
    # Sin columnas elegidas, la caja ofrece todas las de la hoja.
    shown = [f["field"] for f in data.get("filters") or []]
    wanted = fields["dimensions"] or data.get("columns") or []
    return [] if shown == wanted else [f"filtros {shown} en vez de {wanted}"]


def _ranking(data, fields) -> list[str]:
    problems = []
    items = data["items"]
    if len(items) > (fields.get("limit") or 10):
        problems.append("el ranking muestra más que el límite")
    ranks = [i["rank"] for i in items]
    if ranks != sorted(ranks):
        problems.append(f"las posiciones bajan: {ranks}")
    values = [i["value"] for i in items]
    descending = not fields.get("sort_by") or fields["sort_by"].startswith("-")
    if values != sorted(values, reverse=descending):
        problems.append(f"los valores no van en orden: {values}")
    shares = [i["share"] for i in items]
    if all(s is not None for s in shares) and shares:
        rest = (data.get("rest") or {}).get("share") or 0
        if not _close(sum(shares) + rest, items=len(shares) + 1):
            problems.append("el % del total del top y del resto no suma 100")
    return problems


def _correlation(data, fields) -> list[str]:
    problems = []
    matrix, size = data["matrix"], len(fields["columns"])
    if len(matrix) != size or any(len(row) != size for row in matrix):
        return [f"la matriz no es de {size}×{size}"]
    for i in range(size):
        if matrix[i][i] not in (1.0, None):
            problems.append(f"la diagonal {i} vale {matrix[i][i]}")
        for j in range(size):
            r = matrix[i][j]
            if r is not None and not -1 <= r <= 1:
                problems.append(f"r fuera de [-1, 1]: {r}")
            if r != matrix[j][i]:
                problems.append(f"no es simétrica en ({i}, {j})")
    return problems


def _scatter(data, fields) -> list[str]:
    from sheets_reports.widgets.scatter import MAX_GROUPS

    problems = []
    if [data["x"]["field"], data["y"]["field"]] != [c["field"] for c in fields["columns"]]:
        problems.append("los ejes no siguen el orden de las columnas")
    points = [p for g in data["groups"] for p in g["points"]]
    if len(points) != data["shown"] or data["shown"] > data["rows"]:
        problems.append(f"{len(points)} puntos, shown={data['shown']}, rows={data['rows']}")
    if sum(g["rows"] for g in data["groups"]) != data["rows"]:
        problems.append("las filas de los grupos no suman el total")
    if len(data["groups"]) > MAX_GROUPS:
        problems.append(f"{len(data['groups'])} grupos (máx. {MAX_GROUPS})")
    if bool(fields.get("dimensions")) != bool(data["color"]):
        problems.append("color sin dimensión o dimensión sin color")
    if any(len(p) != 2 for p in points):
        problems.append("un punto no es [x, y]")
    for trend in [data["trend"]] + [g["trend"] for g in data["groups"]]:
        if trend and trend["r"] is not None and not -1 <= trend["r"] <= 1:
            problems.append(f"r fuera de [-1, 1]: {trend['r']}")
    return problems


INVARIANTS = {
    "bar": _chart,
    "line": _chart,
    "donut": _donut,
    "kpi": _kpi,
    "dynamic_table": _dynamic_table,
    "table": _table,
    "filter": _filter,
    "ranking": _ranking,
    "correlation": _correlation,
    "scatter": _scatter,
}


# ------------------------------------------------------------------ tests
class ConfigMatrixTests(SimpleTestCase):
    maxDiff = None

    def test_las_metricas_cubren_todas_las_agregaciones(self):
        self.assertEqual({agg for agg, _ in METRICS}, set(AGGREGATIONS) - {"mean"})

    def test_cada_configuracion_se_rechaza_o_se_dibuja_bien(self):
        df = matrix_df()
        ctx = SheetContext.from_dataframe(df, "0")
        failures, checked = [], 0
        for key in WIDGETS.keys():
            widget = WIDGETS.get(key)
            for fields in configs_for(widget):
                checked += 1
                failures += [f"{key} {json.dumps(fields, ensure_ascii=False)}\n    → {p}"
                             for p in self._check(key, widget, fields, df, ctx)]
        self.assertGreater(checked, 1000)
        self.assertEqual(failures[:25], [], f"{len(failures)} configuraciones con problemas")

    def _check(self, key, widget, fields, df, ctx) -> list[str]:
        data = {"widget_type": key, "title": "Matriz", "fields": fields, "style": {}}
        try:
            errors = form_errors(data, ctx, key)
        except Exception as exc:  # noqa: BLE001 - la validación nunca debe romperse
            return [f"la validación lanzó {exc!r}"]
        if errors:
            return [] if all(isinstance(e, str) and e for e in errors) else [f"errores ilegibles: {errors}"]
        reasons = debe_rechazarse(fields)
        if reasons:
            return [f"se aceptó y no tiene sentido: {', '.join(reasons)}"]
        out = widget.render(df, {"fields": fields, "style": {}})
        if "error" in out:
            return [f"se aceptó y al dibujarse falla: {out['error']}"]
        try:
            json.dumps(out["render_data"], allow_nan=False, default=str)
        except ValueError:
            return ["el JSON trae NaN o infinito"]
        return INVARIANTS[key](out["render_data"], fields)


class PanelParityTests(SimpleTestCase):
    """Lo que el panel ofrece (`panel_options`, en el manifiesto) es exactamente lo que el
    servidor acepta al guardar: ni ofrece algo que se rechaza ni esconde algo que vale."""

    FIELD_FOR = {"count": None, "count_distinct": "categoria", "auto": CALCULATED}

    def test_mostrar_como_ofrecido_es_el_aceptado(self):
        ctx = SheetContext.from_dataframe(matrix_df(), "0")
        for key in WIDGETS.keys():
            widget = WIDGETS.get(key)
            caps = widget.capabilities or {}
            if caps.get("metrics", (0, 0))[1] == 0:
                continue
            options = panel_options(widget)["windows"]
            dims = list(GROUP_COLUMNS[:max(caps["dimensions"][0], min(caps["dimensions"][1], 1))])
            modes = [("flat", [])] + ([("pivot", ["anio"])] if caps["pivots"][1] else [])
            for (mode, pivots), agg in itertools.product(modes, options["flat"]):
                for window in WINDOW_TYPES:
                    metric = _metric(agg, self.FIELD_FOR.get(agg, "ventas"), "m", window)
                    fields = {"dimensions": dims, "pivots": pivots, "metrics": [metric], "filters": []}
                    errors = form_errors({"widget_type": key, "title": "x", "fields": fields, "style": {}},
                                         ctx, key)
                    with self.subTest(widget=key, mode=mode, agg=agg, window=window):
                        self.assertEqual(window in options[mode][agg], not errors, errors)

    def test_agregaciones_numericas_rechazadas_sobre_texto(self):
        ctx = SheetContext.from_dataframe(matrix_df(), "0")
        numeric = set(panel_options(WIDGETS.get("bar"))["numeric_aggs"])
        for agg in (a for a, _ in METRICS if a != "auto"):
            fields = {"dimensions": ["mes"], "pivots": [], "filters": [],
                      "metrics": [_metric(agg, None if agg == "count" else "categoria", "m")]}
            errors = form_errors({"widget_type": "bar", "title": "x", "fields": fields, "style": {}}, ctx, "bar")
            with self.subTest(agg=agg):
                self.assertEqual(agg in numeric, bool(errors), errors)

