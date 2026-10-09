"""El `style_schema` es el contrato de `style`: claves, tipo de dato, opciones y defaults.
No dice cómo se dibuja: eso es el partial del widget (tests de `test_architecture`)."""
from django.test import SimpleTestCase

from sheets_reports.services.widget_service import WidgetService
from sheets_reports.widgets import WIDGETS

TYPES = {"string", "number", "boolean", "choice", "list"}
DATA_KEYS = {"key", "label", "type", "options", "options_from", "default"}


def clean_style(widget_type: str, style: dict) -> dict:
    return WidgetService.__new__(WidgetService)._clean_style(widget_type, style)


class StyleSchemaTests(SimpleTestCase):
    def test_todos_los_widgets_tienen_style_schema(self):
        for key, widget in WIDGETS.items():
            schema = widget.style_schema
            self.assertIsInstance(schema, list, f"{key} debe declarar style_schema")
            self.assertTrue(schema, f"{key} no tiene claves de estilo")

    def test_cada_clave_declara_solo_datos(self):
        """Sin metadatos de maquetación (`group`, `section`, `inline_with`, `enabled_when`…)."""
        for key, widget in WIDGETS.items():
            for control in widget.style_schema:
                with self.subTest(widget=key, control=control.get("key")):
                    self.assertLessEqual(set(control), DATA_KEYS)
                    self.assertTrue(control.get("key"))
                    self.assertTrue(control.get("label"))
                    self.assertIn(control.get("type"), TYPES)

    def test_las_claves_no_se_repiten(self):
        for key, widget in WIDGETS.items():
            keys = [c["key"] for c in widget.style_schema]
            self.assertEqual(len(keys), len(set(keys)), key)

    def test_choice_con_opciones_value_label(self):
        for key, widget in WIDGETS.items():
            for control in widget.style_schema:
                if control["type"] != "choice":
                    continue
                with self.subTest(widget=key, control=control["key"]):
                    options = control.get("options")
                    self.assertTrue(options, "un choice necesita options")
                    for option in options:
                        self.assertEqual(set(option), {"value", "label"})

    def test_los_defaults_coinciden_con_el_tipo(self):
        for key, widget in WIDGETS.items():
            for control in widget.style_schema:
                if "default" not in control:
                    continue
                with self.subTest(widget=key, control=control["key"]):
                    value = control["default"]
                    if control["type"] == "boolean":
                        self.assertIsInstance(value, bool)
                    elif control["type"] == "number":
                        self.assertIsInstance(value, (int, float))
                        self.assertNotIsInstance(value, bool)
                    elif control["type"] == "string":
                        self.assertIsInstance(value, str)
                    elif control["type"] == "list":
                        self.assertIsInstance(value, list)
                    else:
                        self.assertIn(value, [o["value"] for o in control["options"]])

    def test_titulo_en_todos_los_widgets(self):
        for key, widget in WIDGETS.items():
            keys = [c["key"] for c in widget.style_schema]
            self.assertIn("title", keys, key)

    def test_defaults_completos_para_el_form(self):
        for key, widget in WIDGETS.items():
            defaults = widget.style_defaults()
            title = next(c for c in widget.style_schema if c["key"] == "title")
            self.assertEqual(defaults["title"], title["default"], key)
            with_default = [c["key"] for c in widget.style_schema if "default" in c]
            self.assertEqual(sorted(defaults), sorted(with_default), key)

    def test_estilos_fuera_del_schema_no_llegan_al_form(self):
        self.assertEqual(clean_style("bar", {"stacked": True, "etiqueta": "x"}), {"stacked": True})

    def test_el_orden_de_la_leyenda_llega_al_form(self):
        self.assertEqual(clean_style("bar", {"seriesOrder": ["B", "A"], "etiqueta": "x"}),
                         {"seriesOrder": ["B", "A"]})


class CapabilitiesTests(SimpleTestCase):
    def test_rangos_bien_formados(self):
        for key, widget in WIDGETS.items():
            caps = widget.capabilities
            for name in ("dimensions", "pivots", "metrics", "columns"):
                if name not in caps:
                    continue
                with self.subTest(widget=key, capability=name):
                    low, high = caps[name]
                    self.assertIsInstance(low, int)
                    self.assertIsInstance(high, int)
                    self.assertLessEqual(low, high)
            for name in ("sort", "limit", "filters"):
                if name in caps:
                    self.assertIsInstance(caps[name], bool, f"{key}.{name}")
            self.assertNotIn("window", caps, f"{key}: la ventana va dentro de la métrica")
            # Columnas sueltas (tabla, correlación, dispersión) no agregan: sin métricas ni
            # pivotes. Una dimensión ahí no agrupa (en la dispersión, colorea los puntos).
            if "columns" in caps:
                self.assertEqual((caps["pivots"], caps["metrics"]), ([0, 0], [0, 0]),
                                 f"{key}: con columns no agrega")

    def test_los_widgets_que_no_agrupan_no_admiten_pivotes(self):
        for key in ("kpi", "donut", "table", "filter"):
            self.assertEqual(WIDGETS.get(key).capabilities["pivots"], [0, 0], key)


class KpiStyleTests(SimpleTestCase):
    """Las claves del KPI que edita su panel: roles del número, meta y semáforo."""

    def test_declara_los_roles_la_meta_y_el_semaforo(self):
        keys = {c["key"] for c in WIDGETS.get("kpi").style_schema}
        self.assertLessEqual({"primary", "compare", "compareMode", "target", "targetMetric",
                              "targetLabel", "statusBasis", "status_good", "status_warn",
                              "higher_is_better"}, keys)

    def test_los_roles_eligen_una_metrica_del_widget(self):
        schema = {c["key"]: c for c in WIDGETS.get("kpi").style_schema}
        for key in ("primary", "compare", "targetMetric"):
            with self.subTest(key=key):
                self.assertEqual(schema[key]["type"], "choice")
                self.assertEqual(schema[key].get("options_from"), "metrics")
                self.assertIn("", [o["value"] for o in schema[key]["options"]])
        self.assertIn("fixed", [o["value"] for o in schema["targetMetric"]["options"]])

    def test_defaults_de_los_roles(self):
        defaults = WIDGETS.get("kpi").style_defaults()
        for key in ("primary", "compare", "targetMetric", "statusBasis"):
            with self.subTest(key=key):
                self.assertEqual(defaults[key], "")

    def test_el_backend_conserva_todas_las_claves_de_la_tarjeta(self):
        """La limpieza de la meta (sin «Valor fijo» no hay `target`) la hace el panel."""
        style = {"primary": "plan", "compare": "actual", "compareMode": "abs",
                 "targetMetric": "plan", "target": 100, "targetLabel": "Plan",
                 "statusBasis": "target_pct", "status_good": 90, "status_warn": 60,
                 "higher_is_better": False}
        self.assertEqual(clean_style("kpi", {**style, "inventada": 1}), style)


class TotalsByLevelTests(SimpleTestCase):
    """«Mostrar totales» por nivel de la tabla dinámica (el panel los pinta junto a cada fila
    de dimensiones y de pivotes)."""

    LEVEL_KEYS = ["showTotals", "rowSubtotal1", "rowSubtotal2",
                  "showColumnTotals", "columnSubtotal1"]

    def test_un_booleano_por_nivel(self):
        schema = {c["key"]: c for c in WIDGETS.get("dynamic_table").style_schema}
        for key in self.LEVEL_KEYS:
            with self.subTest(key=key):
                self.assertEqual(schema[key]["type"], "boolean")

    def test_solo_el_total_general_empieza_activado(self):
        defaults = WIDGETS.get("dynamic_table").style_defaults()
        self.assertTrue(defaults["showTotals"])
        for key in ("rowSubtotal1", "rowSubtotal2", "showColumnTotals", "columnSubtotal1"):
            with self.subTest(key=key):
                self.assertFalse(defaults[key])

    def test_los_totales_llegan_al_form(self):
        style = {"showTotals": False, "rowSubtotal1": True, "columnSubtotal1": True}
        self.assertEqual(clean_style("dynamic_table", {**style, "inventada": 1}), style)

    def test_max_dimensiones_y_pivotes_cubren_los_niveles(self):
        caps = WIDGETS.get("dynamic_table").capabilities
        self.assertGreaterEqual(caps["dimensions"][1], 3)
        self.assertGreaterEqual(caps["pivots"][1], 2)
