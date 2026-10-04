"""
Garantías del diseño: agregar un widget es registrar una clase en un solo archivo (sin tocar
otros módulos) y las capas no se mezclan (dsl, motor, widgets, servicios, vistas).
"""
import ast
import sys
import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from sheets_reports import sdk
from sheets_reports.models import Dashboard, Widget, widget_type_choices
from sheets_reports.services.ai_spec import build_system_prompt, build_tool_parameters
from sheets_reports.services.widget_service import WidgetService
from sheets_reports.tests.fixtures import errors_for, examples_ctx, sales_ctx, sales_df
from sheets_reports.views import _widget_manifest
from sheets_reports.widgets import WIDGETS, ext

PACKAGE = Path(__file__).resolve().parents[1]

CAPABILITY_KEYS = {"dimensions", "pivots", "metrics", "sort", "limit", "filters"}
STYLE_UI = {"text", "select", "checkbox", "number"}


class LayerTests(SimpleTestCase):
    """dsl/ no conoce widgets ni el motor; el motor no conoce widgets; nadie importa la IA."""

    def imports(self, folder: str) -> list[tuple[Path, str]]:
        """Imports del paquete en ejecución: se ignoran los que solo existen para el tipado
        (`if TYPE_CHECKING:`), porque no cambia de qué depende el módulo."""
        found = []
        for path in (PACKAGE / folder).rglob("*.py"):
            tree = ast.parse(path.read_text())
            skip: set[int] = set()
            for node in ast.walk(tree):
                if (isinstance(node, ast.If) and isinstance(node.test, ast.Name)
                        and node.test.id == "TYPE_CHECKING"):
                    skip |= {id(child) for child in ast.walk(node)}
            for node in ast.walk(tree):
                if id(node) in skip:
                    continue
                if isinstance(node, ast.ImportFrom) and node.module:
                    found.append((path, node.module))
                elif isinstance(node, ast.Import):
                    found += [(path, alias.name) for alias in node.names]
        return found

    def test_dsl_no_importa_widgets_ni_motor(self):
        bad = [(p.name, m) for p, m in self.imports("dsl")
               if m.startswith(("sheets_reports.widgets", "sheets_reports.engine", "sheets_reports.services"))]
        self.assertEqual(bad, [])

    def test_motor_no_importa_widgets_ni_servicios(self):
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

    def test_nadie_importa_gemini(self):
        """La IA vive en un único servicio: nada más depende del SDK de Gemini."""
        bad = []
        for path in PACKAGE.rglob("*.py"):
            if "tests" in path.parts or "migrations" in path.parts:
                continue
            if path.name == "ai_spec.py" and path.parent.name == "services":
                continue
            for node in ast.walk(ast.parse(path.read_text())):
                modules = ([node.module or ""] if isinstance(node, ast.ImportFrom)
                           else [a.name for a in node.names] if isinstance(node, ast.Import) else [])
                bad += [(path.name, m) for m in modules if m.startswith("google.genai")]
        self.assertEqual(bad, [])

    def test_no_queda_el_dsl_legacy(self):
        gone = ["metrics.py", "parts.py", "spec.py", "rules.py", "aggregations.py",
                "calc_ops.py", "groups.py", "views.py.bak"]
        leftovers = [name for name in gone if (PACKAGE / "dsl" / name).exists()]
        leftovers += [name for name in ("executor.py", "results.py", "plans") if (PACKAGE / "engine" / name).exists()]
        leftovers += [name for name in ("chart.py", "view.py") if (PACKAGE / "widgets" / name).exists()]
        self.assertEqual(leftovers, [])

    def test_no_quedan_simbolos_del_contrato_viejo(self):
        """Nada del código (ni nombres ni atributos) apunta al DSL que se eliminó."""
        banned = {"data_spec", "view_spec", "generate_widget_spec", "widget_type_from_spec",
                  "SPEC_PARTS", "SpecPart", "ResultPlan", "PlanInput", "PlanResult",
                  "ViewOptions", "DataSpec", "WidgetType", "METRICS", "PLANS"}
        found = []
        for path in PACKAGE.rglob("*.py"):
            if "tests" in path.parts or "migrations" in path.parts:
                continue
            for node in ast.walk(ast.parse(path.read_text())):
                name = None
                if isinstance(node, ast.Name):
                    name = node.id
                elif isinstance(node, ast.Attribute):
                    name = node.attr
                elif isinstance(node, ast.ImportFrom):
                    name = ",".join(a.name for a in node.names)
                if name and name in banned:
                    found.append(f"{path.name}:{getattr(node, 'lineno', '?')}: {name}")
        self.assertEqual(found, [])


class WidgetContractTests(SimpleTestCase):
    """Todo widget registrado cumple el contrato de las tres capas."""

    def test_claves_de_registro_coherentes(self):
        for key, widget in WIDGETS.items():
            with self.subTest(widget=key):
                self.assertEqual((key, widget.type_key), (widget.key, widget.key))
                self.assertTrue(widget.label)
                self.assertLessEqual(len(key), Widget._meta.get_field("type").max_length)

    def test_capacidades_planas_y_validas(self):
        for key, widget in WIDGETS.items():
            with self.subTest(widget=key):
                caps = widget.capabilities
                self.assertTrue(CAPABILITY_KEYS <= set(caps), set(caps) ^ CAPABILITY_KEYS)
                for name in ("dimensions", "pivots", "metrics"):
                    low, high = caps[name]
                    self.assertLessEqual(0, low)
                    self.assertLessEqual(low, high, f"{key}.{name} = {caps[name]}")
                for name in ("sort", "limit", "filters"):
                    self.assertIsInstance(caps[name], bool)

    def test_style_schema_con_solo_ui_validos(self):
        for key, widget in WIDGETS.items():
            for control in widget.style_schema:
                with self.subTest(widget=key, control=control["key"]):
                    self.assertIn(control.get("ui"), STYLE_UI)
                    self.assertTrue(control.get("label"))
                    if control["ui"] == "select":
                        options = control.get("options") or []
                        self.assertTrue(options)
                        self.assertEqual({o["value"] for o in options}, {o["value"] for o in options})
                        for option in options:
                            self.assertTrue({"value", "label"} <= set(option))
                    if "default" in control:
                        self._assert_default_matches(control)

    @staticmethod
    def _assert_default_matches(control):
        default = control["default"]
        ui = control["ui"]
        if ui == "checkbox":
            assert isinstance(default, bool), control
        elif ui == "number":
            assert isinstance(default, (int, float)) and not isinstance(default, bool), control
        elif ui == "text":
            assert isinstance(default, str), control
        else:
            assert default in [o["value"] for o in control.get("options", [])], control

    def test_style_defaults_cubre_solo_claves_del_schema(self):
        for key, widget in WIDGETS.items():
            with self.subTest(widget=key):
                self.assertEqual(set(widget.style_defaults()),
                                 {c["key"] for c in widget.style_schema if "default" in c})

    def test_lo_que_pinta_el_editor_es_el_manifiesto(self):
        manifest = _widget_manifest()
        self.assertEqual(set(manifest), set(WIDGETS.keys()))
        for key, widget in WIDGETS.items():
            with self.subTest(widget=key):
                entry = manifest[key]
                self.assertEqual(entry["label"], widget.label)
                self.assertEqual(entry["style_schema"], widget.style_schema)
                self.assertEqual(entry["capabilities"], widget.capabilities)
                self.assertEqual(entry["max_per_dashboard"], widget.max_per_dashboard)

    def test_los_ejemplos_de_la_ia_son_forms_validos(self):
        for key, widget in WIDGETS.items():
            if not widget.ai_enabled:
                continue
            self.assertTrue(widget.ai_doc, key)
            for prompt, args in widget.ai_examples:
                with self.subTest(widget=key, prompt=prompt):
                    self.assertEqual(errors_for(args["widget_type"], args["fields"],
                                                args.get("style"), ctx=examples_ctx(),
                                                title=args.get("title")), [])


# --- Extensión: un widget nuevo es UN archivo en widgets/ext/ --------------------------------

LIST_MODULE = '''
"""Lista: las filas de la hoja tal cual. Una extensión completa en un solo archivo."""
from sheets_reports.sdk import BaseWidget, WIDGET_REGISTRY


@WIDGET_REGISTRY.register
class ListWidget(BaseWidget):
    key = "list"
    type_key = "list"
    label = "Lista"
    ai_doc = "las filas de la hoja tal cual, para revisar los datos crudos."
    ai_examples = [
        ("muéstrame las filas de la hoja", {
            "widget_type": "list", "title": "Filas de la hoja",
            "fields": {"dimensions": [], "metrics": []}, "style": {},
        }),
    ]
    capabilities = {
        "dimensions": [0, 0], "pivots": [0, 0], "metrics": [0, 0],
        "sort": False, "limit": False, "filters": True,
    }
    style_schema = [
        {"key": "title", "label": "Título", "ui": "text", "default": "Lista"},
        {"key": "maxRows", "label": "Filas a mostrar", "ui": "number", "min": 1, "default": 3},
    ]

    def compile(self, result, style, fields=None, metadata=None):
        rows = result.rows.head(int(style.to_dict().get("maxRows") or 3))
        return {"type": "rows", "rows": rows.to_dict(orient="records")}
'''


class ExtensionTests(TestCase):
    """Un widget nuevo funciona de punta a punta (registro, validación, IA, manifiesto,
    servicio) sin editar el core."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        package = Path(self.tmp.name) / "tableia_ext_demo"
        package.mkdir()
        (package / "__init__.py").write_text("")
        (package / "list.py").write_text(LIST_MODULE)
        sys.path.insert(0, self.tmp.name)
        self.loaded = ext.load("tableia_ext_demo")

    def tearDown(self):
        WIDGETS.unregister("list")
        sys.path.remove(self.tmp.name)
        for name in [m for m in sys.modules if m.startswith("tableia_ext_demo")]:
            del sys.modules[name]
        self.tmp.cleanup()

    def test_se_carga_y_registra_solo(self):
        self.assertEqual(self.loaded, ["list"])
        self.assertIn(("list", "Lista"), widget_type_choices())
        self.assertIn("list", build_tool_parameters(sales_ctx(), widget_type=None)
                      ["properties"]["widget_type"]["enum"])

    def test_valida_con_sus_propias_capacidades(self):
        valid = {"dimensions": [], "pivots": [], "metrics": [], "filters": []}
        self.assertEqual(errors_for("list", valid), [])
        self.assertIn("dimensiones: exactamente 0",
                      errors_for("list", {**valid, "dimensions": ["categoria"]})[0])
        self.assertIn("no admite orden",
                      errors_for("list", {**valid, "sort_by": "categoria"})[0])
        # Las capacidades del resto no cambian: cada tipo habla por sí mismo.
        self.assertIn("métricas",
                      errors_for("bar", {"dimensions": ["categoria"], "metrics": [], "pivots": []})[0])

    def test_aparece_en_el_prompt_de_la_ia(self):
        prompt = build_system_prompt()
        self.assertIn("- list: las filas de la hoja tal cual, para revisar los datos crudos. "
                      "Admite: sin dimensiones, sin pivotes, sin métricas, sin orden, sin límite, "
                      "filtros.", prompt)
        self.assertIn("- style.maxRows: number (Filas a mostrar)", prompt)

    def test_aparece_en_el_manifiesto_del_editor(self):
        user = get_user_model().objects.create(username="u")
        dashboard = Dashboard.objects.create(nombre="D", owner=user,
                                             sheet_url="https://docs.google.com/spreadsheets/d/abc/edit")
        manifest = self.client.get(reverse("board_editor", args=[dashboard.id])).context["widget_manifest"]
        self.assertEqual(manifest["list"]["capabilities"]["dimensions"], [0, 0])
        self.assertEqual(manifest["list"]["style_schema"], WIDGETS.get("list").style_schema)

    def test_crear_y_calcular_por_el_servicio(self):
        user = get_user_model().objects.create(username="u")
        dashboard = Dashboard.objects.create(nombre="D", owner=user,
                                             sheet_url="https://docs.google.com/spreadsheets/d/abc/edit")
        service = WidgetService(dashboard, sales_df())
        widget = service.create("list", {
            "title": "Primeras filas",
            "fields": {"dimensions": [], "pivots": [], "metrics": [], "filters": []},
            "style": {"maxRows": 2},
        })
        self.assertEqual(Widget.objects.get().type, "list")
        self.assertEqual(widget.style, {"maxRows": 2})
        data = service.render(widget)["data"]
        self.assertEqual(len(data["rows"]), 2)
        self.assertEqual(data["rows"][0]["categoria"], "Hogar")


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

    def test_el_sdk_expone_el_contrato_plano(self):
        from sheets_reports.widgets.schemas import WidgetFields, WidgetForm, WidgetStyle
        self.assertIs(sdk.WidgetFields, WidgetFields)
        self.assertIs(sdk.WidgetStyle, WidgetStyle)
        self.assertIs(sdk.WidgetForm, WidgetForm)
        self.assertTrue(issubclass(sdk.BaseWidget, object))
        self.assertEqual(sdk.WIDGET_REGISTRY, WIDGETS)

    def test_el_core_no_importa_extensiones(self):
        bad = []
        for path in PACKAGE.rglob("*.py"):
            if "ext" in path.relative_to(PACKAGE).parts or "tests" in path.parts:
                continue
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("sheets_reports.widgets.ext."):
                    bad.append((path.name, node.module))
        self.assertEqual(bad, [])
