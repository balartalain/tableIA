from django.test import SimpleTestCase

from sheets_reports.widgets import WIDGETS

ALLOWED_UI = {"text", "select", "checkbox", "number"}


class StyleSchemaTests(SimpleTestCase):
    def test_todos_los_widgets_tienen_style_schema(self):
        for key, widget in WIDGETS.items():
            schema = widget.style_schema
            self.assertIsInstance(schema, list, f"{key} debe declarar style_schema")
            self.assertTrue(schema, f"{key} no tiene controles de estilo")

    def test_los_controles_solo_usan_uis_que_la_ui_sabe_dibujar(self):
        for key, widget in WIDGETS.items():
            for control in widget.style_schema:
                with self.subTest(widget=key, control=control.get("key")):
                    self.assertIn("key", control)
                    self.assertIn("label", control)
                    self.assertIn("ui", control)
                    self.assertIn(control["ui"], ALLOWED_UI)

    def test_select_con_opciones_value_label(self):
        for key, widget in WIDGETS.items():
            for control in widget.style_schema:
                if control["ui"] != "select":
                    continue
                with self.subTest(widget=key, control=control["key"]):
                    options = control.get("options")
                    self.assertTrue(options, "un select necesita options")
                    for option in options:
                        self.assertIn("value", option)
                        self.assertIn("label", option)

    def test_los_defaults_coinciden_con_el_tipo_del_control(self):
        for key, widget in WIDGETS.items():
            for control in widget.style_schema:
                if "default" not in control:
                    continue
                with self.subTest(widget=key, control=control["key"]):
                    value = control["default"]
                    if control["ui"] == "checkbox":
                        self.assertIsInstance(value, bool)
                    elif control["ui"] == "number":
                        self.assertIsInstance(value, (int, float))
                        self.assertNotIsInstance(value, bool)
                    elif control["ui"] == "text":
                        self.assertIsInstance(value, str)
                    elif control["ui"] == "select":
                        allowed = [o["value"] for o in control["options"]]
                        self.assertIn(value, allowed)

    def test_titulo_en_todos_los_widgets(self):
        for key, widget in WIDGETS.items():
            keys = [c["key"] for c in widget.style_schema]
            self.assertIn("title", keys, key)

    def test_defaults_completos_para_el_form(self):
        for key, widget in WIDGETS.items():
            defaults = widget.style_defaults()
            title = next(c for c in widget.style_schema if c["key"] == "title")
            self.assertEqual(defaults["title"], title["default"], key)
            with_control_default = [c["key"] for c in widget.style_schema if "default" in c]
            self.assertEqual(sorted(defaults), sorted(with_control_default), key)

    def test_estilos_fuera_del_schema_no_llegan_al_form(self):
        from sheets_reports.services.widget_service import WidgetService
        service = WidgetService.__new__(WidgetService)
        cleaned = service._clean_style("bar", {"stacked": True, "etiqueta": "x"})
        self.assertEqual(cleaned, {"stacked": True})


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
            # Solo la tabla muestra columnas sueltas: los demás agrupan con dimensions.
            self.assertEqual("columns" in caps, key == "table", f"{key}: ¿columns en capabilities?")

    def test_los_widgets_que_no_agrupan_no_admiten_pivotes(self):
        for key in ("kpi", "donut", "table", "filter"):
            self.assertEqual(WIDGETS.get(key).capabilities["pivots"], [0, 0], key)


class TotalsByLevelTests(SimpleTestCase):
    """«Mostrar totales» por nivel de la tabla dinámica: un control oculto por fila de
    dimensiones/pivotes, en «Configurar» y no en «Personalizar»."""

    LEVEL_KEYS = ["showTotals", "rowSubtotal1", "rowSubtotal2",
                  "showColumnTotals", "columnSubtotal1"]

    def test_un_control_checkbox_oculto_por_nivel(self):
        schema = {c["key"]: c for c in WIDGETS.get("dynamic_table").style_schema}
        for key in self.LEVEL_KEYS:
            with self.subTest(key=key):
                self.assertIn(key, schema)
                self.assertEqual(schema[key]["ui"], "checkbox")
                self.assertTrue(schema[key]["hidden"], f"{key} debe salir de «Personalizar»")

    def test_solo_el_total_general_empieza_activado(self):
        defaults = WIDGETS.get("dynamic_table").style_defaults()
        self.assertTrue(defaults["showTotals"])
        for key in ("rowSubtotal1", "rowSubtotal2", "showColumnTotals", "columnSubtotal1"):
            with self.subTest(key=key):
                self.assertFalse(defaults[key])

    def test_los_controles_de_totales_si_llegan_al_form(self):
        from sheets_reports.services.widget_service import WidgetService
        service = WidgetService.__new__(WidgetService)
        cleaned = service._clean_style(
            "dynamic_table",
            {"showTotals": False, "rowSubtotal1": True, "columnSubtotal1": True, "inventada": 1},
        )
        self.assertEqual(cleaned,
                         {"showTotals": False, "rowSubtotal1": True, "columnSubtotal1": True})

    def test_max_dimensiones_y_pivotes_cubren_los_niveles(self):
        caps = WIDGETS.get("dynamic_table").capabilities
        self.assertGreaterEqual(caps["dimensions"][1], 3)
        self.assertGreaterEqual(caps["pivots"][1], 2)

