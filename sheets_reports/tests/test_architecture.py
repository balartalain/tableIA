"""
Garantías del diseño: un widget es su clase registrada (datos, contrato de `style`, IA) más
su partial con el panel del editor, y las capas no se mezclan (utils, motor, widgets,
servicios, vistas).
"""
import ast
from pathlib import Path

from django.template.loader import get_template
from django.test import SimpleTestCase

from sheets_reports.models import Widget
from sheets_reports.services.ai_spec import WINDOW_TYPES, panel_options
from sheets_reports.tests.fixtures import errors_for, examples_ctx
from sheets_reports.views import _widget_manifest
from sheets_reports.widgets import WIDGETS

PACKAGE = Path(__file__).resolve().parents[1]

CAPABILITY_KEYS = {"dimensions", "pivots", "metrics", "sort", "limit", "filters", "windows"}
STYLE_TYPES = {"string", "number", "boolean", "choice"}
# Lo único que declara un control: datos. El layout del panel vive en el partial del widget.
STYLE_KEYS = {"key", "label", "type", "options", "options_from", "default"}
TEMPLATES = PACKAGE / "templates"


class LayerTests(SimpleTestCase):
    """utils/ y el motor no conocen widgets ni servicios; nadie importa la IA."""

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

    def test_utils_no_importa_widgets_ni_servicios(self):
        bad = [(p.name, m) for p, m in self.imports("utils")
               if m.startswith(("sheets_reports.widgets", "sheets_reports.services"))]
        self.assertEqual(bad, [])

    def test_motor_no_importa_widgets_ni_servicios(self):
        bad = [(p.name, m) for p, m in self.imports("engine")
               if m.startswith(("sheets_reports.widgets", "sheets_reports.services"))]
        self.assertEqual(bad, [])

    def test_utils_no_nombra_widgets_concretos(self):
        keys = set(WIDGETS.keys())
        found = []
        for path in (PACKAGE / "utils").rglob("*.py"):
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

    def test_no_queda_codigo_legacy(self):
        leftovers = ["dsl"] if (PACKAGE / "dsl").exists() else []
        leftovers += [name for name in ("executor.py", "results.py", "plans", "pipeline.py")
                      if (PACKAGE / "engine" / name).exists()]
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
                self.assertLessEqual(set(caps["windows"]), set(WINDOW_TYPES))
                if caps["metrics"][1] == 0:
                    self.assertEqual(caps["windows"], [])

    def test_style_schema_solo_declara_datos(self):
        for key, widget in WIDGETS.items():
            for control in widget.style_schema:
                with self.subTest(widget=key, control=control["key"]):
                    self.assertLessEqual(set(control), STYLE_KEYS)
                    self.assertIn(control.get("type"), STYLE_TYPES)
                    self.assertTrue(control.get("label"))
                    if control["type"] == "choice":
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
        kind = control["type"]
        if kind == "boolean":
            assert isinstance(default, bool), control
        elif kind == "number":
            assert isinstance(default, (int, float)) and not isinstance(default, bool), control
        elif kind == "string":
            assert isinstance(default, str), control
        else:
            assert default in [o["value"] for o in control.get("options", [])], control

    def test_style_defaults_cubre_solo_claves_del_schema(self):
        for key, widget in WIDGETS.items():
            with self.subTest(widget=key):
                self.assertEqual(set(widget.style_defaults()),
                                 {c["key"] for c in widget.style_schema if "default" in c})

    def test_el_manifiesto_refleja_cada_tipo(self):
        manifest = _widget_manifest()
        self.assertEqual(set(manifest), set(WIDGETS.keys()))
        for key, widget in WIDGETS.items():
            with self.subTest(widget=key):
                entry = manifest[key]
                self.assertEqual(entry["label"], widget.label)
                self.assertEqual(entry["style_schema"], widget.style_schema)
                self.assertEqual(entry["capabilities"], widget.capabilities)
                self.assertEqual(entry["max_per_dashboard"], widget.max_per_dashboard)
                self.assertEqual(entry["panel_options"], panel_options(widget))

    def test_el_panel_no_copia_las_reglas_del_servidor(self):
        """Las reglas de qué se puede elegir llegan en `panel_options`: si se copian en el JS
        del panel vuelven a desalinearse con la validación."""
        store = (PACKAGE / "static" / "sheets_reports" / "js" / "board_editor" / "dashboard-store.js").read_text()
        for name in ("PIVOT_WINDOWS", "ADDITIVE_WINDOWS", "ADDITIVE_AGGS", "NUMERIC_AGGS"):
            with self.subTest(constante=name):
                self.assertNotIn(name, store)

    def test_los_ejemplos_de_la_ia_son_forms_validos(self):
        for key, widget in WIDGETS.items():
            if not widget.ai_enabled:
                continue
            self.assertTrue(widget.ai_doc, key)
            for prompt, args in widget.ai_examples:
                with self.subTest(widget=key, prompt=prompt):
                    self.assertEqual(errors_for(args["widget_type"], args["fields"],
                                                args.get("style"), ctx=examples_ctx(),
                                                title=args.get("title"),
                                                calculated_fields=args.get("calculated_fields")), [])

    def test_cada_widget_tiene_sus_tests(self):
        """Un widget nuevo trae sus pruebas: los invariantes de lo que dibuja (los comprueba la
        matriz de configuraciones en cada combinación que acepta) y sus casos concretos."""
        from sheets_reports.tests.test_config_matrix import INVARIANTS

        for key in WIDGETS.keys():
            with self.subTest(widget=key):
                self.assertIn(key, INVARIANTS,
                              f"Falta INVARIANTS['{key}'] en tests/test_config_matrix.py")
                path = PACKAGE / "tests" / "widgets" / f"test_{key}.py"
                self.assertTrue(path.exists(), f"Falta tests/widgets/test_{key}.py")


class PanelPartialTests(SimpleTestCase):
    """El panel del editor de cada tipo es su partial: existe y `board_editor.html` lo incluye."""

    def test_cada_widget_tiene_su_partial(self):
        editor = (TEMPLATES / "board_editor.html").read_text()
        for key in WIDGETS.keys():
            name = f"sheets_reports/widgets/config/_{key}_config.html"
            with self.subTest(widget=key):
                get_template(name)
                self.assertIn(f"$store.dashboard.editingType === '{key}'", editor)
                self.assertIn(f'{{% include "{name}" %}}', editor)
