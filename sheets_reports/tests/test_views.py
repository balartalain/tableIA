import json
from unittest import mock
from urllib.parse import quote

from django.contrib.auth import get_user_model
from django.test import TestCase

from sheets_reports.models import Dashboard, Widget
from sheets_reports.services.ai_spec import SpecGenerationError
from sheets_reports.tests.fixtures import agg, sales_df, spec
from sheets_reports.tests.fixtures import view as build_view_spec


def board_filters(*conditions) -> str:
    """Query string de los filtros del tablero (lo que arma la caja de filtros)."""
    return "filters=" + quote(json.dumps(list(conditions)))


ANIO_2026 = {"field": "anio", "op": "in", "value": [2026]}


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
            r = self.client.get(f"/api/dashboard/{self.dashboard.id}/render/?{board_filters(ANIO_2026)}")
        ai.assert_not_called()
        self.assertEqual(r.status_code, 200)
        data = r.json()["widgets"][0]["data"]
        self.assertEqual(data["categories"], ["Hogar", "Electrónica"])
        self.assertEqual(data["series"][0]["data"], [175.0, 500.0])

    def test_kpi_porcentaje_usa_como_universo_los_filtros_del_tablero(self, _df):
        data_spec = spec(dimensions=[], metrics=[agg("porcentaje", "count", show_as="pct_total")],
                         filters=[{"field": "categoria", "op": "eq", "value": "Hogar"}])
        Widget.objects.create(dashboard=self.dashboard, type="kpi",
                              data_spec=data_spec, view_spec=build_view_spec("kpi", data_spec))
        r = self.client.get(f"/api/dashboard/{self.dashboard.id}/render/?{board_filters(ANIO_2026)}")
        kpi = next(w for w in r.json()["widgets"] if w["type"] == "kpi")
        # 3 filas de Hogar entre las 5 de 2026 (no entre las 6 de la hoja).
        self.assertEqual(kpi["data"]["value"], 60.0)

    def test_filtro_de_columna_inexistente_se_ignora_sin_tumbar_el_tablero(self, _df):
        # Ej. una URL compartida cuando la columna ya no está en la hoja.
        r = self.client.get(f"/api/dashboard/{self.dashboard.id}/render/?"
                            + board_filters({"field": "pais", "op": "in", "value": ["DO"]}, ANIO_2026))
        self.assertEqual(r.status_code, 200)
        self.assertIn("'pais' no existe", r.json()["filter_errors"][0])
        # El filtro válido sí se aplicó.
        self.assertEqual(r.json()["widgets"][0]["data"]["categories"], ["Hogar", "Electrónica"])

    def test_render_valida_las_reglas_de_las_condiciones(self, _df):
        r = self.client.get(f"/api/dashboard/{self.dashboard.id}/render/?"
                            + board_filters({"field": "ventas", "op": "between", "value": [1]}))
        self.assertEqual(r.status_code, 200)
        self.assertIn("dos números", r.json()["filter_errors"][0])

    def test_filters_que_no_es_una_lista_json(self, _df):
        r = self.client.get(f"/api/dashboard/{self.dashboard.id}/render/?filters=nada")
        self.assertEqual(r.status_code, 400)

    def test_crear_kpi_con_condiciones_y_roles(self, _df):
        r = self.client.post(
            f"/api/dashboard/{self.dashboard.id}/widgets/",
            json.dumps({
                "type": "kpi", "dimensions": [], "trend_by": "mes",
                "filters": [{"field": "categoria", "op": "ne", "value": "Ropa"}],
                "metrics": [
                    agg("actual", filters=[{"field": "anio", "op": "eq", "relative": "max"}]),
                    agg("anterior", filters=[{"field": "anio", "op": "eq", "relative": "second_max"}]),
                ],
                "kpi": {"primary": "actual", "compare": "anterior", "target": 1000,
                        "status": {"basis": "target_pct", "good": 100, "warn": 50}},
            }),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 201, r.content)
        data = r.json()["data"]
        self.assertEqual(data["value"], 675.0)
        # Sin Ropa no hay filas de 2025: la suma es 0 y la variación % no existe.
        self.assertEqual(data["compare"]["value"], 0)
        self.assertIsNone(data["compare"]["delta_pct"])
        self.assertEqual(data["target"]["pct"], 67.5)
        self.assertEqual(data["status"], "warn")
        self.assertEqual(data["trend"]["data"], [400.0, 250.0, 25.0])

    def test_update_kpi_conserva_roles_si_no_se_envian(self, _df):
        data_spec = spec(dimensions=[], metrics=[agg("a"), agg("b")])
        kpi = Widget.objects.create(dashboard=self.dashboard, type="kpi", data_spec=data_spec,
                                    view_spec=build_view_spec("kpi", data_spec, {"kpi": {"compare": "b"}}))
        r = self.client.put(f"/api/widget/{kpi.id}/spec/", json.dumps({"metrics": [agg("a"), agg("b", "avg")]}),
                            content_type="application/json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["view_spec"]["compare"], "b")

    def test_crear_barras_top_n_con_otros(self, _df):
        r = self.client.post(
            f"/api/dashboard/{self.dashboard.id}/widgets/",
            json.dumps({"type": "bar", "dimensions": ["categoria"], "metrics": [agg("total_ventas")],
                        "sort": {"by": "total_ventas", "dir": "desc"}, "limit": {"n": 1, "others": True}}),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()["data"]["categories"], ["Electrónica", "Otros"])
        self.assertEqual(r.json()["data"]["series"][0]["data"], [500.0, 255.0])

    def test_update_spec_desde_builder(self, _df):
        with mock.patch("sheets_reports.views.generate_widget_spec") as ai:
            r = self.client.put(
                f"/api/widget/{self.widget.id}/spec/",
                json.dumps({
                    "dimensions": ["categoria"], "pivots": ["mes"], "stacked": True,
                    "metrics": [{"type": "agg", "field": "ventas", "agg": "sum", "as": "total_ventas"}],
                }),
                content_type="application/json",
            )
        ai.assert_not_called()
        self.assertEqual(r.status_code, 200, r.content)
        self.widget.refresh_from_db()
        self.assertEqual(self.widget.data_spec["pivots"], ["mes"])
        self.assertEqual(self.widget.view_spec["seriesBy"], "mes")
        self.assertTrue(self.widget.view_spec["stacked"])
        self.assertEqual(len(r.json()["data"]["series"]), 3)

    def test_update_spec_count_sin_campo(self, _df):
        r = self.client.put(
            f"/api/widget/{self.widget.id}/spec/",
            json.dumps({"dimensions": ["categoria"], "stacked": True,
                        "metrics": [{"type": "agg", "agg": "count", "as": "cantidad"}]}),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 200, r.content)
        self.assertNotIn("field", r.json()["data_spec"]["metrics"][0])
        self.assertFalse(r.json()["view_spec"]["stacked"])  # sin pivote no se apila
        self.assertEqual(r.json()["data"]["series"][0]["data"], [3, 2, 1])

    def test_update_spec_rechaza_pivote_igual_a_dimension(self, _df):
        r = self.client.put(
            f"/api/widget/{self.widget.id}/spec/",
            json.dumps({"dimensions": ["categoria"], "pivots": ["categoria"], "stacked": False,
                        "metrics": [{"type": "agg", "field": "ventas", "agg": "sum", "as": "total_ventas"}]}),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 422)
        self.assertIn("pivot", r.json()["error"])

    def test_crear_widget_desde_builder_sin_ia(self, _df):
        with mock.patch("sheets_reports.views.generate_widget_spec") as ai:
            r = self.client.post(
                f"/api/dashboard/{self.dashboard.id}/widgets/",
                json.dumps({"type": "dynamic_table", "dimensions": ["categoria"], "pivots": ["mes"],
                            "metrics": [{"type": "agg", "agg": "count", "as": "cantidad"}],
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
            json.dumps({"type": "dynamic_table", "dimensions": ["anio", "categoria"], "pivots": ["mes"],
                        "metrics": [{"type": "agg", "agg": "count", "as": "cantidad"},
                                    {"type": "agg", "agg": "count", "as": "pct", "show_as": "pct_row"}]}),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 201, r.content)
        data = r.json()["data"]
        self.assertEqual(data["rowFields"], ["anio", "categoria"])
        subtotal = next(row for row in data["rows"] if row.get("__subtotal"))
        self.assertEqual(subtotal["anio"], "Total 2026")
        self.assertEqual(subtotal["__total.cantidad"], 5)

    def test_tabla_demasiado_grande_muestra_mensaje(self, _df):
        data_spec = spec(pivots=["mes"])
        Widget.objects.create(dashboard=self.dashboard, type="dynamic_table",
                              data_spec=data_spec, view_spec=build_view_spec("dynamic_table", data_spec))
        with mock.patch("sheets_reports.engine.plans.pivot_table.MAX_TABLE_CELLS", 5):
            r = self.client.get(f"/api/dashboard/{self.dashboard.id}/render/")
        table = next(w for w in r.json()["widgets"] if w["type"] == "dynamic_table")
        self.assertIn("celdas", table["error"])

    def test_update_spec_conserva_labels_que_no_envia_el_builder(self, _df):
        self.widget.view_spec = {**self.widget.view_spec, "labels": {"total_ventas": "Ingresos"}}
        self.widget.save()
        r = self.client.put(
            f"/api/widget/{self.widget.id}/spec/",
            json.dumps({"dimensions": ["categoria"], "metrics": [{"type": "agg", "field": "ventas", "agg": "sum", "as": "total_ventas"}]}),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["view_spec"]["labels"], {"total_ventas": "Ingresos"})

    def test_crear_tabla_con_nombre_a_mostrar(self, _df):
        r = self.client.post(
            f"/api/dashboard/{self.dashboard.id}/widgets/",
            json.dumps({"type": "dynamic_table", "dimensions": ["categoria"],
                        "metrics": [{"type": "agg", "field": "ventas", "agg": "sum", "as": "total_ventas"}],
                        "labels": {"categoria": "Categoría", "total_ventas": "Ventas"}}),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 201, r.content)
        columns = r.json()["data"]["columns"]
        self.assertEqual([c["header"] for c in columns], ["Categoría", "Ventas"])
        self.assertEqual(Widget.objects.get(id=r.json()["id"]).view_spec["labels"],
                         {"categoria": "Categoría", "total_ventas": "Ventas"})

    def test_crear_widget_invalido_no_guarda_nada(self, _df):
        r = self.client.post(
            f"/api/dashboard/{self.dashboard.id}/widgets/",
            json.dumps({"type": "bar", "dimensions": ["categoria"], "pivots": ["categoria"],
                        "metrics": [{"type": "agg", "field": "ventas", "agg": "sum", "as": "total"}]}),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 422)
        self.assertEqual(Widget.objects.count(), 1)

    def test_schema_incluye_columnas_agrupables(self, _df):
        r = self.client.get(f"/api/dashboard/{self.dashboard.id}/schema/")
        # anio es numérica pero con pocos valores enteros: se puede agrupar. ventas no.
        self.assertEqual(r.json()["dimension_fields"], ["categoria", "mes", "anio"])
        self.assertEqual(r.json()["sample_values"]["mes"], ["Ene", "Feb", "Mar"])

    def test_schema_trae_el_manifiesto_con_las_columnas_de_la_hoja(self, _df):
        manifest = self.client.get(f"/api/dashboard/{self.dashboard.id}/schema/").json()["widget_manifest"]
        dims = next(p for p in manifest["bar"]["parts"] if p["key"] == "dimensions")
        self.assertEqual([o["value"] for o in dims["options"]], ["categoria", "mes", "anio"])
        # La página del editor lo arma sin leer la hoja.
        page = self.client.get(f"/tableros/{self.dashboard.id}/edit/").context["widget_manifest"]
        self.assertEqual(next(p for p in page["bar"]["parts"] if p["key"] == "dimensions")["options"], [])

    def test_update_spec_rechaza_pivote_con_varias_metricas(self, _df):
        r = self.client.put(
            f"/api/widget/{self.widget.id}/spec/",
            json.dumps({
                "dimensions": ["categoria"], "pivots": ["mes"], "stacked": False,
                "metrics": [
                    {"type": "agg", "field": "ventas", "agg": "sum", "as": "total_ventas"},
                    {"type": "agg", "agg": "count", "as": "cantidad"},
                ],
            }),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 422)
        self.assertIn("Con pivote solo se permite una métrica", r.json()["error"])
        self.widget.refresh_from_db()
        self.assertEqual(self.widget.data_spec["pivots"], [])

    def test_asistente_de_tabla_devuelve_spec_sin_guardar(self, _df):
        generated = {
            "widget_type": "dynamic_table",
            "data_spec": spec(),
            "view_spec": build_view_spec("dynamic_table", spec()),
        }
        widgets_before = Widget.objects.count()
        with mock.patch("sheets_reports.views.generate_widget_spec", return_value=generated) as ai:
            r = self.client.post(
                f"/api/dashboard/{self.dashboard.id}/table-assistant/",
                json.dumps({"prompt": "ventas por categoría"}),
                content_type="application/json",
            )
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(ai.call_args.args[:2], ("ventas por categoría", "dynamic_table"))
        self.assertEqual(r.json(), {"data_spec": generated["data_spec"], "view_spec": generated["view_spec"]})
        self.assertEqual(Widget.objects.count(), widgets_before)

    def test_asistente_de_tabla_error_legible(self, _df):
        with mock.patch("sheets_reports.views.generate_widget_spec",
                        side_effect=SpecGenerationError("La columna Precio no existe.")):
            r = self.client.post(
                f"/api/dashboard/{self.dashboard.id}/table-assistant/",
                json.dumps({"prompt": "precio promedio"}),
                content_type="application/json",
            )
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.json()["error"], "La columna Precio no existe.")

    def test_paginas_renderizan(self, _df):
        for url in ("/", f"/tableros/{self.dashboard.id}/edit/", f"/tableros/{self.dashboard.id}/shared/"):
            r = self.client.get(url)
            self.assertEqual(r.status_code, 200, url)
        r = self.client.get(f"/tableros/{self.dashboard.id}/edit/")
        self.assertContains(r, "window.REFRESH_MINUTES = 5")
        self.assertContains(r, "donut-widget.js")
        self.assertContains(r, 'id="module-rail"')
        self.assertContains(r, "tableia:rail-collapsed")


class SinUsuarioTests(TestCase):
    def test_lista_explica_como_crear_usuario(self):
        r = self.client.get("/api/dashboards/")
        self.assertEqual(r.status_code, 401)
        self.assertIn("createsuperuser", r.json()["error"])

    def test_home_siempre_muestra_nuevo_tablero(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Nuevo tablero")
