"""El `style_schema` es el contrato de `style`: lo que llega al form y las claves propias del KPI
y de la tabla dinámica. Las reglas comunes a todos los widgets (solo datos, tipos, opciones,
defaults, capacidades) están en `test_architecture.WidgetContractTests`."""
from django.test import SimpleTestCase

from sheets_reports.services.widget_service import WidgetService
from sheets_reports.widgets import WIDGETS

def clean_style(widget_type: str, style: dict) -> dict:
    return WidgetService.__new__(WidgetService)._clean_style(widget_type, style)


class StyleSchemaTests(SimpleTestCase):
    def test_todos_los_widgets_tienen_style_schema(self):
        for key, widget in WIDGETS.items():
            schema = widget.style_schema
            self.assertIsInstance(schema, list, f"{key} debe declarar style_schema")
            self.assertTrue(schema, f"{key} no tiene claves de estilo")

    def test_las_claves_no_se_repiten(self):
        for key, widget in WIDGETS.items():
            keys = [c["key"] for c in widget.style_schema]
            self.assertEqual(len(keys), len(set(keys)), key)

    def test_titulo_en_todos_los_widgets(self):
        for key, widget in WIDGETS.items():
            keys = [c["key"] for c in widget.style_schema]
            self.assertIn("title", keys, key)

    def test_estilos_fuera_del_schema_no_llegan_al_form(self):
        self.assertEqual(clean_style("bar", {"stacked": True, "etiqueta": "x"}), {"stacked": True})

    def test_el_orden_de_la_leyenda_llega_al_form(self):
        self.assertEqual(clean_style("bar", {"seriesOrder": ["B", "A"], "etiqueta": "x"}),
                         {"seriesOrder": ["B", "A"]})


class CapabilitiesTests(SimpleTestCase):
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
