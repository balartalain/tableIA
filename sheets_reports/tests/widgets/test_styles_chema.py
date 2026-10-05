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

    def test_las_dependencias_entre_controles_apuntan_a_controles_del_mismo_widget(self):
        """`enabled_when` e `inline_with` nombran claves del propio schema, y
        `clear_when_disabled` solo tiene sentido con `enabled_when`."""
        for key, widget in WIDGETS.items():
            keys = {c["key"] for c in widget.style_schema}
            for control in widget.style_schema:
                with self.subTest(widget=key, control=control["key"]):
                    for other in control.get("enabled_when") or {}:
                        self.assertIn(other, keys)
                        self.assertNotEqual(other, control["key"])
                    if "inline_with" in control:
                        self.assertIn(control["inline_with"], keys)
                        self.assertIn(control["ui"], {"text", "number"})
                    if control.get("clear_when_disabled"):
                        self.assertTrue(control.get("enabled_when"))

    def test_cada_seccion_es_un_tramo_seguido_del_schema(self):
        """Los controles de una `section` van juntos: si no, su plegable se partiría en dos."""
        for key, widget in WIDGETS.items():
            seen, previous = set(), None
            for control in widget.style_schema:
                section = control.get("section")
                with self.subTest(widget=key, control=control["key"]):
                    if section is not None:
                        self.assertIsInstance(section, str)
                        self.assertTrue(section.strip())
                        if section != previous:
                            self.assertNotIn(section, seen, f"la sección «{section}» está partida")
                            seen.add(section)
                previous = section

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


class KpiCardBlockTests(SimpleTestCase):
    """El bloque «Tarjeta KPI» de «Configurar»: los roles del número, ocultos en
    «Personalizar» y con las opciones de las métricas resueltas desde el propio widget."""

    ROLE_KEYS = ["primary", "compare", "compareMode", "target", "targetMetric",
                 "targetLabel", "statusBasis", "status_good", "status_warn", "higher_is_better"]

    def test_los_roles_viven_en_el_grupo_card_y_fuera_de_personalizar(self):
        schema = {c["key"]: c for c in WIDGETS.get("kpi").style_schema}
        for key in self.ROLE_KEYS:
            with self.subTest(key=key):
                self.assertIn(key, schema)
                self.assertEqual(schema[key].get("group"), "card", key)
                self.assertTrue(schema[key].get("hidden"), f"{key} debe salir de «Personalizar»")

    def test_solo_el_kpi_tiene_bloque_de_tarjeta(self):
        for key, widget in WIDGETS.items():
            groups = {c.get("group") for c in widget.style_schema}
            self.assertEqual("card" in groups, key == "kpi", f"{key}: ¿group card?")

    def test_los_selects_de_rol_toman_sus_opciones_de_las_metricas(self):
        schema = {c["key"]: c for c in WIDGETS.get("kpi").style_schema}
        for key in ("primary", "compare", "targetMetric"):
            with self.subTest(key=key):
                control = schema[key]
                self.assertEqual(control["ui"], "select")
                self.assertEqual(control.get("options_from"), "metrics")
                # El control sigue declarando options (la vacía) para el contrato del schema.
                self.assertIn("", [o["value"] for o in control["options"]])

    def test_semaforo_con_base_y_meta_en_el_bloque(self):
        schema = {c["key"]: c for c in WIDGETS.get("kpi").style_schema}
        self.assertEqual(schema["statusBasis"]["ui"], "select")
        self.assertIn("", [o["value"] for o in schema["statusBasis"]["options"]])
        defaults = WIDGETS.get("kpi").style_defaults()
        self.assertEqual(defaults["statusBasis"], "")
        self.assertEqual(defaults["primary"], "")
        self.assertEqual(defaults["compare"], "")
        self.assertEqual(defaults["targetMetric"], "")

    def test_los_controles_de_la_tarjeta_si_llegan_al_form(self):
        from sheets_reports.services.widget_service import WidgetService
        service = WidgetService.__new__(WidgetService)
        cleaned = service._clean_style("kpi", {
            "primary": "plan", "compare": "actual", "targetMetric": "fixed",
            "statusBasis": "target_pct", "target": 100, "inventada": 1,
        })
        self.assertEqual(cleaned, {"primary": "plan", "compare": "actual",
                                   "targetMetric": "fixed", "statusBasis": "target_pct",
                                   "target": 100})


    def test_la_meta_y_lo_que_depende_de_ella_van_en_la_seccion_meta(self):
        sections = {c["key"]: c.get("section") for c in WIDGETS.get("kpi").style_schema}
        for key in ("target", "targetMetric", "targetLabel", "statusBasis",
                    "status_good", "status_warn", "higher_is_better"):
            with self.subTest(key=key):
                self.assertEqual(sections[key], "Meta")
        for key in ("primary", "compare", "compareMode"):
            with self.subTest(key=key):
                self.assertIsNone(sections[key])

    def test_el_valor_de_la_meta_solo_se_guarda_con_valor_fijo(self):
        from sheets_reports.services.widget_service import WidgetService
        service = WidgetService.__new__(WidgetService)
        self.assertEqual(service._clean_style("kpi", {"targetMetric": "plan", "target": 100}),
                         {"targetMetric": "plan"})
        self.assertEqual(service._clean_style("kpi", {"target": 100}), {})
        self.assertEqual(service._clean_style("kpi", {"targetMetric": "fixed", "target": 100}),
                         {"targetMetric": "fixed", "target": 100})

    def test_sin_meta_los_controles_que_dependen_de_ella_se_deshabilitan(self):
        from sheets_reports.widgets.base import control_enabled
        schema = {c["key"]: c for c in WIDGETS.get("kpi").style_schema}
        for key in ("targetLabel", "statusBasis", "status_good", "status_warn", "higher_is_better"):
            with self.subTest(key=key):
                self.assertFalse(control_enabled(schema[key], {"targetMetric": ""}))
                self.assertTrue(control_enabled(schema[key], {"targetMetric": "plan"}))
        self.assertFalse(control_enabled(schema["target"], {"targetMetric": "plan"}))
        self.assertTrue(control_enabled(schema["target"], {"targetMetric": "fixed"}))
        self.assertTrue(control_enabled(schema["primary"], {}))


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

