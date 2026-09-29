import json
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase

from sheets_reports.models import Dashboard, Widget
from sheets_reports.services.spec_validation import build_view_spec
from sheets_reports.tests.fixtures import sales_df, spec


@mock.patch("sheets_reports.views.get_sheet_dataframe", side_effect=lambda *a, **k: sales_df())
class ViewsTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("admin", "a@a.com", "x")
        self.client.force_login(self.user)
        self.dashboard = Dashboard.objects.create(
            nombre="Ventas", owner=self.user,
            sheet_url="https://docs.google.com/spreadsheets/d/abc123/edit#gid=0",
        )
        data_spec = spec()
        self.widget = Widget.objects.create(
            dashboard=self.dashboard, type="bar",
            data_spec=data_spec, view_spec=build_view_spec("bar", data_spec),
        )

    def test_render_no_llama_a_la_ia(self, _df):
        with mock.patch("sheets_reports.views.generate_widget_spec") as ai:
            r = self.client.get(f"/api/dashboard/{self.dashboard.id}/render/?filtro_anio=2026")
        ai.assert_not_called()
        self.assertEqual(r.status_code, 200)
        data = r.json()["widgets"][0]["data"]
        self.assertEqual(data["categories"], ["Hogar", "Electrónica"])
        self.assertEqual(data["series"][0]["data"], [175.0, 500.0])

    def test_kpi_porcentaje_usa_como_universo_los_filtros_del_tablero(self, _df):
        data_spec = spec(dimensions=[], metrics=[{"field": "categoria", "agg": "pct_count", "as": "porcentaje"}],
                         filters=[{"field": "categoria", "op": "eq", "value": "Hogar"}])
        Widget.objects.create(dashboard=self.dashboard, type="kpi",
                              data_spec=data_spec, view_spec=build_view_spec("kpi", data_spec))
        r = self.client.get(f"/api/dashboard/{self.dashboard.id}/render/?filtro_anio=2026")
        kpi = next(w for w in r.json()["widgets"] if w["type"] == "kpi")
        # 3 filas de Hogar entre las 5 de 2026 (no entre las 6 de la hoja).
        self.assertEqual(kpi["data"]["value"], 60.0)

    def test_render_rechaza_filtro_de_columna_inexistente(self, _df):
        r = self.client.get(f"/api/dashboard/{self.dashboard.id}/render/?filtro_pais=DO")
        self.assertEqual(r.status_code, 400)

    def test_update_spec_desde_builder(self, _df):
        with mock.patch("sheets_reports.views.generate_widget_spec") as ai:
            r = self.client.put(
                f"/api/widget/{self.widget.id}/spec/",
                json.dumps({
                    "dimensions": ["categoria"], "pivot": "mes", "stacked": True,
                    "metrics": [{"field": "ventas", "agg": "sum", "as": "total_ventas"}],
                }),
                content_type="application/json",
            )
        ai.assert_not_called()
        self.assertEqual(r.status_code, 200, r.content)
        self.widget.refresh_from_db()
        self.assertEqual(self.widget.data_spec["pivot"], "mes")
        self.assertEqual(self.widget.view_spec["seriesBy"], "mes")
        self.assertTrue(self.widget.view_spec["stacked"])
        self.assertEqual(len(r.json()["data"]["series"]), 3)

    def test_update_spec_count_sin_campo_usa_la_dimension(self, _df):
        r = self.client.put(
            f"/api/widget/{self.widget.id}/spec/",
            json.dumps({"dimensions": ["categoria"], "pivot": None, "stacked": True,
                        "metrics": [{"field": "", "agg": "count", "as": "cantidad"}]}),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["data_spec"]["metrics"][0]["field"], "categoria")
        self.assertFalse(r.json()["view_spec"]["stacked"])  # sin pivote no se apila
        self.assertEqual(r.json()["data"]["series"][0]["data"], [3, 2, 1])

    def test_update_spec_rechaza_pivote_igual_a_dimension(self, _df):
        r = self.client.put(
            f"/api/widget/{self.widget.id}/spec/",
            json.dumps({"dimensions": ["categoria"], "pivot": "categoria", "stacked": False,
                        "metrics": [{"field": "ventas", "agg": "sum", "as": "total_ventas"}]}),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 422)
        self.assertIn("pivot", r.json()["error"])

    def test_crear_widget_desde_builder_sin_ia(self, _df):
        with mock.patch("sheets_reports.views.generate_widget_spec") as ai:
            r = self.client.post(
                f"/api/dashboard/{self.dashboard.id}/widgets/",
                json.dumps({"type": "table", "dimensions": ["categoria"], "pivot": "mes",
                            "metrics": [{"field": "", "agg": "count", "as": "cantidad"}],
                            "position": {"x": 0, "y": 3, "w": 12, "h": 400}}),
                content_type="application/json",
            )
        ai.assert_not_called()
        self.assertEqual(r.status_code, 201, r.content)
        widget = Widget.objects.get(id=r.json()["id"])
        self.assertIsNone(widget.source_prompt)
        self.assertEqual(widget.position, {"x": 0, "y": 3, "w": 12, "h": 400})
        self.assertEqual(r.json()["data"]["columns"][1]["children"][0]["header"], "Ene")

    def test_crear_tabla_dinamica_con_varios_niveles(self, _df):
        r = self.client.post(
            f"/api/dashboard/{self.dashboard.id}/widgets/",
            json.dumps({"type": "table", "dimensions": ["anio", "categoria"], "pivot": ["mes"],
                        "metrics": [{"field": "", "agg": "count", "as": "cantidad"},
                                    {"field": "", "agg": "count", "as": "pct", "show_as": "pct_row"}]}),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 201, r.content)
        data = r.json()["data"]
        self.assertEqual(data["rowFields"], ["anio", "categoria"])
        subtotal = next(row for row in data["rows"] if row.get("__subtotal"))
        self.assertEqual(subtotal["anio"], "Total 2026")
        self.assertEqual(subtotal["__total.cantidad"], 5)

    def test_tabla_demasiado_grande_muestra_mensaje(self, _df):
        data_spec = spec(pivot="mes")
        Widget.objects.create(dashboard=self.dashboard, type="table",
                              data_spec=data_spec, view_spec=build_view_spec("table", data_spec))
        with mock.patch("sheets_reports.services.query_engine.MAX_TABLE_CELLS", 5):
            r = self.client.get(f"/api/dashboard/{self.dashboard.id}/render/")
        table = next(w for w in r.json()["widgets"] if w["type"] == "table")
        self.assertIn("celdas", table["error"])

    def test_crear_widget_invalido_no_guarda_nada(self, _df):
        r = self.client.post(
            f"/api/dashboard/{self.dashboard.id}/widgets/",
            json.dumps({"type": "bar", "dimensions": ["categoria"], "pivot": "categoria",
                        "metrics": [{"field": "ventas", "agg": "sum", "as": "total"}]}),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 422)
        self.assertEqual(Widget.objects.count(), 1)

    def test_schema_incluye_columnas_agrupables(self, _df):
        r = self.client.get(f"/api/dashboard/{self.dashboard.id}/schema/")
        # anio es numérica pero con pocos valores enteros: se puede agrupar. ventas no.
        self.assertEqual(r.json()["dimension_fields"], ["categoria", "mes", "anio"])

    def test_update_spec_rechaza_pivote_con_varias_metricas(self, _df):
        r = self.client.put(
            f"/api/widget/{self.widget.id}/spec/",
            json.dumps({
                "dimensions": ["categoria"], "pivot": "mes", "stacked": False,
                "metrics": [
                    {"field": "ventas", "agg": "sum", "as": "total_ventas"},
                    {"field": "categoria", "agg": "count", "as": "cantidad"},
                ],
            }),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 422)
        self.assertIn("Con pivote solo se permite una métrica", r.json()["error"])
        self.widget.refresh_from_db()
        self.assertIsNone(self.widget.data_spec["pivot"])

    def test_generate_guarda_spec_y_prompt(self, _df):
        generated = {
            "widget_type": "kpi",
            "data_spec": spec(dimensions=[]),
            "view_spec": build_view_spec("kpi", spec(dimensions=[])),
        }
        with mock.patch("sheets_reports.views.generate_widget_spec", return_value=generated):
            r = self.client.post(
                f"/api/dashboard/{self.dashboard.id}/widgets/generate/",
                json.dumps({"prompt": "total de ventas", "widget_type": "kpi"}),
                content_type="application/json",
            )
        self.assertEqual(r.status_code, 201, r.content)
        widget = Widget.objects.get(id=r.json()["id"])
        self.assertEqual(widget.source_prompt, "total de ventas")
        self.assertEqual(r.json()["data"], {"value": 755.0, "label": "Total ventas"})

    def test_paginas_renderizan(self, _df):
        for url in ("/", f"/tableros/{self.dashboard.id}/edit/", f"/tableros/{self.dashboard.id}/shared/"):
            r = self.client.get(url)
            self.assertEqual(r.status_code, 200, url)
        r = self.client.get(f"/tableros/{self.dashboard.id}/edit/")
        self.assertContains(r, "window.REFRESH_MINUTES = 5")
        self.assertContains(r, "donut-widget.js")


class SinUsuarioTests(TestCase):
    def test_lista_explica_como_crear_usuario(self):
        r = self.client.get("/api/dashboards/")
        self.assertEqual(r.status_code, 401)
        self.assertIn("createsuperuser", r.json()["error"])

    def test_home_siempre_muestra_nuevo_tablero(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Nuevo tablero")
