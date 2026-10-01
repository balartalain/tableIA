"""
Garantías del diseño: agregar piezas es registrar clases (sin tocar otros módulos) y las capas
no se mezclan.
"""
import ast
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

import pandas as pd
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from sheets_reports.dsl.metrics import METRICS, Metric
from sheets_reports.dsl.schema import REF
from sheets_reports.engine.plans import PLANS, PlanInput, PlanResult, ResultPlan
from sheets_reports.models import Dashboard, Widget, widget_type_choices
from sheets_reports.services.ai_spec import build_tool_parameters
from sheets_reports.services.widget_service import WidgetService
from sheets_reports.tests.fixtures import errors_for, sales_ctx, sales_df, spec
from sheets_reports.widgets import WIDGETS, DataCapabilities, WidgetType

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


class ListWidget(WidgetType[RawRowsResult, "ViewOptions"]):
    key = "list"
    label = "Lista"
    capabilities = DataCapabilities(dimensions=(1, 2), pivots=(0, 0), max_metrics=1,
                                    metric_types=frozenset({"agg", "constant"}),
                                    having=False, sort=False, limit=False)
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
