import json
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from sheets_reports.models import Dashboard, Widget
from sheets_reports.tests.fixtures import compiled, render, sales_df, table_fields


class RowsExecutionTests(SimpleTestCase):
    def test_filas_tal_cual_con_orden(self):
        out = compiled("table", table_fields(["mes", "ventas"], sort_by="ventas"))["rows"]
        self.assertEqual(out[0], {"mes": "Mar", "ventas": 25.0})
        self.assertEqual(out[1], {"mes": "Feb", "ventas": 50.0})

    def test_orden_descendente(self):
        out = compiled("table", table_fields(["mes", "ventas"], sort_by="-ventas"))["rows"]
        self.assertEqual(out[0]["ventas"], 300.0)

    def test_tope_de_filas(self):
        with mock.patch("sheets_reports.widgets.table.MAX_ROWS", 2):
            out = compiled("table", table_fields(["categoria"]))
        self.assertEqual(len(out["rows"]), 2)
        self.assertEqual(out["total_rows"], 6)
        self.assertTrue(out["truncated"])

    def test_solo_las_columnas_elegidas(self):
        out = compiled("table", table_fields(["mes"]))
        self.assertEqual(list(out["rows"][0]), ["mes"])


class CompileTests(SimpleTestCase):
    def test_columnas_con_tipo(self):
        out = compiled("table", table_fields(["categoria", "ventas"]))
        self.assertEqual(out["columns"], [
            {"header": "Categoria", "field": "categoria", "numeric": False},
            {"header": "Ventas", "field": "ventas", "numeric": True},
        ])
        self.assertEqual(out["rows"][0], {"categoria": "Hogar", "ventas": 100.0})
        self.assertEqual(out["total_rows"], 6)

    def test_el_orden_es_el_de_las_columnas(self):
        out = compiled("table", table_fields(["ventas", "mes"]))
        self.assertEqual([c["field"] for c in out["columns"]], ["ventas", "mes"])

    def test_cabeceras_con_nombre_a_mostrar(self):
        out = compiled("table", table_fields(["categoria", {"field": "ventas", "label": "Monto"}]))
        self.assertEqual([c["header"] for c in out["columns"]], ["Categoria", "Monto"])

    def test_sin_nombre_a_mostrar_se_muestra_la_columna(self):
        out = compiled("table", table_fields(["ventas"]))
        self.assertEqual(out["columns"][0],
                         {"header": "Ventas", "field": "ventas", "numeric": True})


class RenderContractTests(SimpleTestCase):
    def test_los_defaults_de_estilo(self):
        out = render("table", table_fields(["mes"]))
        self.assertEqual(out["widget_form"]["style"]["pageSize"], 10)
        self.assertTrue(out["widget_form"]["style"]["showPagination"])


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

    def test_crear_desde_el_builder(self, _df):
        r = self.post_widget({
            "type": "table",
            "title": "Datos",
            "fields": {"columns": [{"field": "categoria"}, {"field": "ventas"}],
                       "sort_by": "-ventas"},
            "style": {"pageSize": 25},
        })
        self.assertEqual(r.status_code, 201, r.content)
        data = r.json()["data"]
        self.assertEqual(data["rows"][0], {"categoria": "Electrónica", "ventas": 300.0})
        self.assertEqual([c["header"] for c in data["columns"]], ["Categoria", "Ventas"])
        widget = Widget.objects.get()
        self.assertEqual(widget.fields["columns"], [{"field": "categoria"}, {"field": "ventas"}])
        self.assertEqual(widget.style["pageSize"], 25)

    def test_el_nombre_a_mostrar_llega_a_la_cabecera(self, _df):
        r = self.post_widget({
            "type": "table",
            "title": "Datos",
            "fields": {"columns": [{"field": "ventas", "label": "Monto"}]},
        })
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()["data"]["columns"][0]["header"], "Monto")

    def test_una_tabla_se_define_con_columns_no_con_dimensions(self, _df):
        r = self.post_widget({"type": "table", "title": "Datos",
                              "fields": {"dimensions": ["categoria", "ventas"]}})
        self.assertEqual(r.status_code, 422, r.content)
        self.assertIn("dimensions", r.json()["error"])
        self.assertEqual(Widget.objects.count(), 0)

    def test_crear_con_una_columna_que_no_existe_no_guarda_nada(self, _df):
        r = self.post_widget({"type": "table", "title": "Datos",
                              "fields": {"columns": [{"field": "inexistente"}]}})
        self.assertEqual(r.status_code, 422, r.content)
        self.assertIn("inexistente", r.json()["error"])
        self.assertEqual(Widget.objects.count(), 0)

    def test_actualizar_sin_tocar_lo_que_no_se_envia(self, _df):
        r = self.post_widget({"type": "table", "title": "Datos",
                              "fields": {"columns": [{"field": "categoria"}]}})
        widget_id = r.json()["id"]

        put = self.client.put(f"/api/widget/{widget_id}/",
                              json.dumps({"title": "Otro nombre",
                                          "fields": {"columns": [{"field": "mes"}]}}),
                              content_type="application/json")
        self.assertEqual(put.status_code, 200, put.content)
        widget = Widget.objects.get(id=widget_id)
        self.assertEqual(widget.title, "Otro nombre")
        self.assertEqual(widget.fields["columns"], [{"field": "mes"}])

    def test_borrar(self, _df):
        r = self.post_widget({"type": "table", "title": "Datos",
                              "fields": {"columns": [{"field": "categoria"}]}})
        widget_id = r.json()["id"]
        d = self.client.delete(f"/api/widget/{widget_id}/")
        self.assertEqual(d.status_code, 200)
        self.assertEqual(Widget.objects.count(), 0)
