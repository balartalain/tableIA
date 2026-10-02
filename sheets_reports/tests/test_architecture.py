"""
Garantías del diseño: agregar piezas es registrar clases (sin tocar otros módulos) y las capas
no se mezclan.
"""
import ast
import sys
import tempfile
from unittest import mock
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

import pandas as pd
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from sheets_reports.dsl.metrics import METRICS, Metric
from sheets_reports.dsl.schema import REF
from sheets_reports.engine.plans import PLANS, PlanInput, PlanResult, ResultPlan
from sheets_reports import sdk
from sheets_reports.models import Dashboard, Widget, widget_type_choices
from sheets_reports.services import ai_spec
from sheets_reports.services.ai_spec import build_system_prompt, build_tool_parameters, generate_widget_spec
from sheets_reports.services.widget_service import WidgetService
from sheets_reports.tests.fixtures import errors_for, sales_ctx, sales_df, spec
from sheets_reports.dsl.parts import SPEC_PARTS, Dimensions, Filters, Metrics
from sheets_reports.dsl.spec import DataSpec
from sheets_reports.widgets import WIDGETS, WidgetType, ext

PACKAGE = Path(__file__).resolve().parents[1]


# --- Piezas de prueba: un tipo de métrica, una forma de datos y un widget nuevos ------------

@dataclass(frozen=True)
class ConstantMetric(Metric):
    """Un número fijo (ej. una meta): no depende de las filas."""
    key: ClassVar[str] = "constant"
    label: ClassVar[str] = "«constante»"

    alias: str
    value: float

    @classmethod
    def schema(cls, ctx, *, for_ai=False, nested=False):
        return {
            "type": "object", "additionalProperties": False, "required": ["type", "as", "value"],
            "properties": {"type": {"enum": [cls.key]}, "as": REF, "value": {"type": "number"}},
        }

    @classmethod
    def from_dict(cls, raw):
        return cls(alias=raw["as"], value=raw["value"])

    def to_dict(self):
        return {"type": self.key, "as": self.alias, "value": self.value}

    def scalar(self, ev):
        return self.value

    def flat(self, fc):
        return pd.Series(self.value, index=fc.keys, dtype="float64"), self.value


@dataclass(frozen=True)
class RawRowsResult(PlanResult):
    rows: list


class RawRowsPlan(ResultPlan[RawRowsResult]):
    """Filas crudas, sin agrupar: una forma de datos que no existía."""
    key = "raw_rows"
    aggregates = False

    def run(self, spec, data: PlanInput) -> RawRowsResult:
        return RawRowsResult(rows=data.df[spec.dimensions].head(3).to_dict(orient="records"))


class ListSpec(DataSpec):
    parts = (Dimensions(1, 2), Filters(), Metrics(1, 1, types={"agg", "constant"}))


class ListWidget(WidgetType[RawRowsResult, "ViewOptions"]):
    key = "list"
    label = "Lista"
    spec_cls = ListSpec
    plan_key = "raw_rows"

    def compile(self, result, options, spec):
        return {"items": result.rows}


class RegistryExtensionTests(TestCase):
    """Un widget, una métrica y un plan nuevos funcionan de punta a punta sin editar el
    validador, el motor, la IA ni las vistas."""

    def setUp(self):
        METRICS.register(ConstantMetric)
        PLANS.register(RawRowsPlan)
        WIDGETS.register(ListWidget)

    def tearDown(self):
        WIDGETS.unregister("list")
        PLANS.unregister("raw_rows")
        METRICS.unregister("constant")

    def list_spec(self, **overrides):
        return spec(dimensions=["categoria", "mes"], metrics=[{"type": "constant", "as": "meta", "value": 10}],
                    **overrides)

    def test_validacion_desde_las_capacidades(self):
        self.assertEqual(errors_for("list", self.list_spec()), [])
        self.assertIn("no admite pivote", errors_for("list", self.list_spec(pivots=["anio"]))[0])
        self.assertIn("«Lista» no se ordena", errors_for("list", self.list_spec(
            sort={"by": "categoria", "dir": "asc"}))[0])
        # Los demás widgets no admiten la métrica nueva: lo declara cada widget.
        self.assertIn("«constante» no están disponibles",
                      errors_for("bar", spec(metrics=[{"type": "constant", "as": "meta", "value": 1}]))[0])

    def test_metrica_nueva_en_un_widget_existente_que_la_admita(self):
        # El KPI no la declara: se puede habilitar sin tocar dsl/ (solo sus capacidades).
        self.assertTrue(errors_for("kpi", spec(dimensions=[], metrics=[{"type": "constant", "as": "m", "value": 1}])))

    def test_aparece_en_la_ia_y_en_el_modelo(self):
        params = build_tool_parameters(sales_ctx(), widget_type=None)
        self.assertIn("list", params["properties"]["widget_type"]["enum"])
        types = [b["properties"]["type"]["enum"][0]
                 for b in params["properties"]["data_spec"]["properties"]["metrics"]["items"]["anyOf"]]
        self.assertIn("constant", types)
        self.assertIn(("list", "Lista"), widget_type_choices())

    def test_aparece_en_el_manifiesto_del_editor(self):
        # El panel de edición arma su builder con esto: no hay flags que repetir en el frontend.
        user = get_user_model().objects.create(username="u")
        dashboard = Dashboard.objects.create(nombre="D", owner=user,
                                             sheet_url="https://docs.google.com/spreadsheets/d/abc/edit")
        manifest = self.client.get(reverse("board_editor", args=[dashboard.id])).context["widget_manifest"]
        self.assertEqual(manifest["list"]["data"]["dimensions"], [1, 2])
        self.assertEqual(manifest["list"]["data"]["metric_types"], ["agg", "constant"])
        self.assertFalse(manifest["list"]["data"]["sort"])
        self.assertEqual(manifest["list"]["view"], [])

    def test_crear_y_calcular_por_el_servicio(self):
        user = get_user_model().objects.create(username="u")
        dashboard = Dashboard.objects.create(nombre="D", owner=user,
                                             sheet_url="https://docs.google.com/spreadsheets/d/abc/edit")
        service = WidgetService(dashboard, sales_df())
        widget = service.create("list", self.list_spec())
        self.assertEqual(Widget.objects.get().type, "list")
        self.assertEqual(service.render(widget)["data"]["items"][0], {"categoria": "Hogar", "mes": "Ene"})


class LayerTests(SimpleTestCase):
    """dsl/ no conoce widgets ni el motor; el motor no conoce widgets."""

    def imports(self, folder: str) -> list[tuple[Path, str]]:
        found = []
        for path in (PACKAGE / folder).rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.ImportFrom) and node.module:
                    found.append((path, node.module))
                elif isinstance(node, ast.Import):
                    found += [(path, alias.name) for alias in node.names]
        return found

    def test_dsl_no_importa_widgets_ni_motor(self):
        bad = [(p.name, m) for p, m in self.imports("dsl")
               if m.startswith(("sheets_reports.widgets", "sheets_reports.engine", "sheets_reports.services"))]
        self.assertEqual(bad, [])

    def test_motor_no_importa_widgets(self):
        bad = [(p.name, m) for p, m in self.imports("engine")
               if m.startswith(("sheets_reports.widgets", "sheets_reports.services"))]
        self.assertEqual(bad, [])

    def test_dsl_no_nombra_widgets_concretos(self):
        keys = set(WIDGETS.keys())
        found = []
        for path in (PACKAGE / "dsl").rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Constant) and node.value in keys:
                    found.append((path.name, node.value))
        self.assertEqual(found, [])


# --- Widgets de extensión: un único archivo en widgets/ext/ ---------------------------------

HISTOGRAM_MODULE = '''
"""Histograma: cuántas filas caen en cada intervalo de una columna numérica. Trae su propio
dato en el data_spec (bins), su forma de datos y sus opciones: todo en este archivo."""
from dataclasses import dataclass

import numpy as np

from sheets_reports.sdk import (
    SPEC_PARTS, WIDGETS, DataSpec, Filters, PlanResult, ResultPlan, SpecPart, ViewOptions,
    WidgetType, column_message,
)


@SPEC_PARTS.register
class Bins(SpecPart):
    """Pieza nueva del data_spec: {"column": columna numérica, "n": cantidad de intervalos}."""
    key = "bins"

    def schema(self, ctx, *, for_ai=False):
        return {"type": "object", "additionalProperties": False, "required": ["column", "n"],
                "properties": {"column": {"enum": ctx.ordered_numeric_fields},
                               "n": {"type": "integer", "minimum": 2, "maximum": 30}}}

    def parse(self, raw):
        return {"column": raw["column"], "n": int(raw["n"])}

    def names(self, value):
        return [value["column"]] if value else []

    def readable(self, error, path, ctx):
        return column_message(error, path, ctx) if error.validator == "enum" else None

    def describe(self):
        return ["bins"]


class HistogramSpec(DataSpec):
    parts = (Bins(), Filters())


@dataclass(frozen=True)
class BinsResult(PlanResult):
    edges: list
    counts: list


class BinsPlan(ResultPlan[BinsResult]):
    """Forma de datos propia: no se registra en PLANS, la devuelve el widget."""
    aggregates = False

    def run(self, spec, data):
        counts, edges = np.histogram(data.df[spec.bins["column"]].dropna(), bins=spec.bins["n"])
        return BinsResult(edges=edges.tolist(), counts=counts.tolist())


@dataclass(frozen=True)
class HistogramOptions(ViewOptions):
    cumulative: bool = False
    ai_doc = "- cumulative: true si pide la distribución acumulada."

    @classmethod
    def ai_properties(cls):
        return {"cumulative": {"type": "boolean"}}

    @classmethod
    def _request_fields(cls, data, previous):
        fallback = previous.cumulative if previous is not None else False
        return {**super()._request_fields(data, previous), "cumulative": bool(data.get("cumulative", fallback))}

    @classmethod
    def _view_fields(cls, view):
        return {**super()._view_fields(view), "cumulative": bool(view.get("cumulative"))}

    def view_fields(self):
        return {"cumulative": self.cumulative}


@WIDGETS.register
class HistogramWidget(WidgetType[BinsResult, HistogramOptions]):
    key = "histogram"
    label = "Histograma"
    spec_cls = HistogramSpec
    options_cls = HistogramOptions
    ai_doc = 'cómo se distribuye una columna numérica ("distribución de las ventas").'

    def plan(self, spec):
        return BinsPlan()

    def default_title(self, spec, options):
        return f"Distribución de {spec.bins['column']}"

    def compile(self, result, options, spec):
        labels = [f"{a:g}-{b:g}" for a, b in zip(result.edges, result.edges[1:])]
        return {"categories": labels, "series": [{"name": "Filas", "data": result.counts}]}
'''


class ExtensionTests(TestCase):
    """Un widget nuevo es un único archivo en un paquete de extensiones: se carga solo y
    funciona de punta a punta (validación, IA, manifiesto, servicio) sin editar el core."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        package = Path(self.tmp.name) / "tableia_ext_demo"
        package.mkdir()
        (package / "__init__.py").write_text("")
        (package / "histogram.py").write_text(HISTOGRAM_MODULE)
        sys.path.insert(0, self.tmp.name)
        self.loaded = ext.load("tableia_ext_demo")

    def tearDown(self):
        WIDGETS.unregister("histogram")
        SPEC_PARTS.unregister("bins")
        sys.path.remove(self.tmp.name)
        for name in [m for m in sys.modules if m.startswith("tableia_ext_demo")]:
            del sys.modules[name]
        self.tmp.cleanup()

    def histogram_spec(self, **overrides):
        return {"source": "0", "filters": [], "bins": {"column": "ventas", "n": 4}, **overrides}

    def test_se_carga_y_registra_solo(self):
        self.assertEqual(self.loaded, ["histogram"])
        self.assertIn(("histogram", "Histograma"), widget_type_choices())
        self.assertIn("histogram", build_tool_parameters(sales_ctx(), widget_type=None)
                      ["properties"]["widget_type"]["enum"])

    def test_valida_con_su_pieza_propia(self):
        self.assertEqual(errors_for("histogram", self.histogram_spec()), [])
        self.assertEqual(errors_for("histogram", self.histogram_spec(bins={"column": "nada", "n": 4})),
                         ["bins.column: la columna 'nada' no existe en la hoja."])
        # Las piezas de los demás widgets no aplican; las del histograma, en los demás tampoco.
        self.assertIn("dimensions: este tipo de widget no admite dimensión.",
                      errors_for("histogram", self.histogram_spec(dimensions=["categoria"])))
        self.assertIn("bins: «Gráfico de Barras» no admite 'bins'.",
                      errors_for("bar", spec(bins={"column": "ventas", "n": 4})))
        # Lo que el builder manda vacío de otras piezas se ignora.
        self.assertEqual(errors_for("histogram", {**spec(dimensions=[], metrics=[]), "bins": {"column": "ventas", "n": 4}}), [])

    def test_manifiesto_con_sus_opciones(self):
        manifest = WIDGETS.get("histogram").manifest()
        self.assertEqual(manifest["data"]["dimensions"], [0, 0])
        self.assertEqual(manifest["view"], ["cumulative"])

    def test_la_ia_lo_conoce_y_llena_sus_opciones(self):
        prompt = build_system_prompt()
        self.assertIn('- histogram: cómo se distribuye una columna numérica ("distribución de las ventas"). '
                      'Admite: bins; sin having, sort, limit.', prompt)
        self.assertIn("- cumulative: true si pide la distribución acumulada. (solo histogram)", prompt)
        # Sin tipo fijado, la tool ofrece la clave nueva; la IA solo manda las del histograma.
        tool = build_tool_parameters(sales_ctx(), widget_type=None)
        self.assertIn("bins", tool["properties"]["data_spec"]["properties"])
        args = {"widget_type": "histogram", "title": "Distribución",
                "data_spec": {"bins": {"column": "ventas", "n": 3}},
                "view_options": {"labels": [], "cumulative": True}}
        with mock.patch.object(ai_spec, "_call_model", return_value=("create_widget", args)), \
                mock.patch.object(ai_spec, "_audit"):
            result = generate_widget_spec("distribución de las ventas", None, sales_ctx())
        self.assertEqual(result["data_spec"], {"source": "0", "bins": {"column": "ventas", "n": 3}, "filters": []})
        self.assertTrue(result["view_spec"]["cumulative"])

    def test_crear_y_calcular_con_su_propio_plan(self):
        self.assertNotIn("bins", PLANS)
        user = get_user_model().objects.create(username="u")
        dashboard = Dashboard.objects.create(nombre="D", owner=user,
                                             sheet_url="https://docs.google.com/spreadsheets/d/abc/edit")
        service = WidgetService(dashboard, sales_df())
        # El builder manda todas las claves que conoce; las de otras piezas van vacías.
        payload = {**spec(dimensions=[], metrics=[]), "bins": {"column": "ventas", "n": 4}, "cumulative": True}
        widget = service.create("histogram", payload)
        self.assertEqual(widget.data_spec, {"source": "0", "bins": {"column": "ventas", "n": 4}, "filters": []})
        self.assertEqual(widget.view_spec["title"], "Distribución de ventas")
        data = service.render(widget)["data"]
        self.assertEqual(len(data["categories"]), 4)
        self.assertEqual(sum(data["series"][0]["data"]), len(sales_df()))


class ExtensionImportTests(SimpleTestCase):
    """Las extensiones solo importan el sdk (la API estable del core) y nunca a otra
    extensión: lo que repitan queda a la vista para subirlo al core."""

    def test_extensiones_solo_importan_el_sdk(self):
        bad = []
        for path in (PACKAGE / "widgets" / "ext").glob("*.py"):
            if path.name == "__init__.py":
                continue
            for node in ast.walk(ast.parse(path.read_text())):
                modules = ([node.module or ""] if isinstance(node, ast.ImportFrom)
                           else [a.name for a in node.names] if isinstance(node, ast.Import) else [])
                if isinstance(node, ast.ImportFrom) and node.level:
                    bad.append((path.name, "." * node.level + (node.module or "")))
                bad += [(path.name, m) for m in modules
                        if m.startswith("sheets_reports") and m != "sheets_reports.sdk"]
        self.assertEqual(bad, [])

    def test_el_sdk_exporta_lo_que_declara(self):
        missing = [name for name in sdk.__all__ if not hasattr(sdk, name)]
        self.assertEqual(missing, [])

    def test_el_core_no_importa_extensiones(self):
        bad = []
        for path in PACKAGE.rglob("*.py"):
            if "ext" in path.relative_to(PACKAGE).parts or "tests" in path.parts:
                continue
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("sheets_reports.widgets.ext."):
                    bad.append((path.name, node.module))
        self.assertEqual(bad, [])
