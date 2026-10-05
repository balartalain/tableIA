import json
from unittest import mock
from urllib.parse import quote

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from sheets_reports.models import Dashboard, Widget
from sheets_reports.tests.fixtures import compiled, fields, render, sales_df
from sheets_reports.widgets import WIDGETS

class CompileTests(SimpleTestCase):
    def test_un_control_por_columna_en_el_orden_elegido(self):
        out = compiled("filter", fields(dimensions=["anio", "categoria"], metrics=[]))
        self.assertEqual([f["field"] for f in out["filters"]], ["anio", "categoria"])
        self.assertEqual(out["filters"][0]["options"], [2025, 2026])
        self.assertEqual(out["filters"][1]["options"], ["Electrónica", "Hogar", "Ropa"])

    def test_columna_inexistente_se_salta(self):
        out = compiled("filter", fields(dimensions=["categoria", "borrada"], metrics=[]))
        self.assertEqual([f["field"] for f in out["filters"]], ["categoria"])

    def test_tope_de_opciones(self):
        with mock.patch("sheets_reports.widgets.filter.MAX_FILTER_VALUES", 2):
            out = compiled("filter", fields(dimensions=["mes"], metrics=[]))
        self.assertEqual(out["filters"][0]["options"], ["Ene", "Feb", "Mar"][:2])
        self.assertTrue(out["filters"][0]["truncated"])

    def test_las_opciones_normalizadas_no_se_duplican(self):
        out = compiled("filter", fields(dimensions=["categoria"], metrics=[]))
        options = out["filters"][0]["options"]
        self.assertEqual(options, sorted(set(options), key=str))

    def test_sin_columnas_elegidas_expone_todas(self):
        out = compiled("filter", fields(dimensions=[], metrics=[]))
        self.assertEqual([f["field"] for f in out["filters"]], ["categoria", "mes", "anio", "ventas"])

    def test_el_estilo_trae_sus_defaults(self):
        out = render("filter", fields(dimensions=["mes"], metrics=[]))
        self.assertEqual(out["widget_form"]["style"]["layout"], "horizontal")


class BoardFilterTests(TestCase):
    """La selección de la caja viaja en `?filters=` y define el universo de todos los widgets."""

    def test_filters_de_tablero_sobre_un_widget_del_tablero(self):
        from sheets_reports.engine.steps.filter import apply_filters, parse_conditions
        conditions = parse_conditions([{"field": "categoria", "op": "in", "value": ["Hogar", "Ropa"]}])
        df = apply_filters(sales_df(), conditions)
        out = WIDGETS.get("bar").render(df, {"fields": fields(), "style": {}})["render_data"]
        self.assertEqual(out["categories"], ["Hogar", "Ropa"])
        self.assertEqual(out["series"][0]["data"], [175.0, 80.0])

    def test_parse_de_filtros_ignora_los_invalidos(self):
        from sheets_reports.models import Dashboard
        from sheets_reports.services.widget_service import WidgetService

        user = get_user_model().objects.create(username="u")
        dashboard = Dashboard.objects.create(
            nombre="D", owner=user,
            sheet_url="https://docs.google.com/spreadsheets/d/abc/edit")
        service = WidgetService(dashboard, sales_df())
        raw = json.dumps([
            {"field": "categoria", "op": "in", "value": ["Hogar"]},
            {"field": "pais", "op": "in", "value": ["DO"]},
        ])
        conditions, errors = service.parse_board_filters(raw)
        self.assertEqual(len(conditions), 1)
        self.assertIn("'pais' no existe", errors[0])

    def test_un_parametro_que_no_es_lista_da_error(self):
        from sheets_reports.models import Dashboard
        from sheets_reports.services.widget_service import WidgetService

        user = get_user_model().objects.create(username="u2")
        dashboard = Dashboard.objects.create(
            nombre="D", owner=user,
            sheet_url="https://docs.google.com/spreadsheets/d/abc/edit")
        service = WidgetService(dashboard, sales_df())
        with self.assertRaises(ValueError):
            service.parse_board_filters("nada")


@mock.patch("sheets_reports.views.get_sheet_dataframe", side_effect=lambda *a, **k: sales_df())
class ApiTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_superuser("admin", "a@a.com", "x")
        self.client.force_login(user)
        self.dashboard = Dashboard.objects.create(
            nombre="D", owner=user,
            sheet_url="https://docs.google.com/spreadsheets/d/abc/edit",
        )

    def post_widget(self, payload):
        return self.client.post(
            f"/api/dashboard/{self.dashboard.id}/widgets/",
            json.dumps(payload),
            content_type="application/json",
        )

    def test_una_sola_caja_por_tablero(self, _df):
        self.assertEqual(self.post_widget({"type": "filter", "title": "Filtros",
                                           "fields": {"dimensions": ["categoria"]}}).status_code, 201)
        r = self.post_widget({"type": "filter", "title": "Filtros",
                              "fields": {"dimensions": ["anio"]}})
        self.assertEqual(r.status_code, 422)
        self.assertIn("Filtros", r.json()["error"])
        self.assertEqual(Widget.objects.filter(type="filter").count(), 1)

    def test_la_seleccion_filtra_los_widgets_pero_no_la_caja(self, _df):
        self.post_widget({"type": "filter", "title": "Filtros", "fields": {"dimensions": ["categoria"]}})
        self.post_widget({"type": "bar", "title": "Ventas", "fields": fields()})

        selection = [{"field": "categoria", "op": "in", "value": ["Hogar", "Ropa"]}]
        r = self.client.get(f"/api/dashboard/{self.dashboard.id}/render/?filters={quote(json.dumps(selection))}")

        widgets = {w["type"]: w["data"] for w in r.json()["widgets"]}
        self.assertEqual(widgets["bar"]["categories"], ["Hogar", "Ropa"])
        self.assertEqual(widgets["filter"]["filters"][0]["options"], ["Electrónica", "Hogar", "Ropa"])

    def test_seleccion_con_muchos_valores(self, _df):
        selection = [{"field": "categoria", "op": "in", "value": ["Hogar", *[f"x{i}" for i in range(300)]]}]
        r = self.client.get(f"/api/dashboard/{self.dashboard.id}/render/?filters={quote(json.dumps(selection))}")
        self.assertEqual(r.json()["filter_errors"], [])
