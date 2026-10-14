import json
from unittest import mock

import pandas as pd

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from sheets_reports.models import Dashboard, Widget
from sheets_reports.tests.fixtures import compiled, make_board, render, sales_df, table_fields


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
    def test_vacios_y_no_finitos_se_serializan_como_null(self):
        df = sales_df()
        df["ventas"] = [100.0, float("nan"), float("inf"), float("-inf"), 0.0, -2.5]
        out = compiled("table", table_fields(["ventas"]), df=df)

        json.dumps(out, allow_nan=False)
        self.assertEqual(out["rows"], [
            {"ventas": 100.0}, {"ventas": None}, {"ventas": None},
            {"ventas": None}, {"ventas": 0.0}, {"ventas": -2.5},
        ])
        self.assertTrue(out["columns"][0]["numeric"])

    def test_fechas_y_nulos_de_pandas_son_json_valido(self):
        df = pd.DataFrame({
            "ID": [1, 2],
            "fecha": [pd.Timestamp("2026-10-14 12:30:00"), pd.NaT],
            "texto": ["Dato", pd.NA],
        })
        out = compiled("table", table_fields(["fecha", "texto"]), df=df)

        json.dumps(out, allow_nan=False)
        self.assertEqual(out["rows"], [
            {"fecha": "2026-10-14T12:30:00", "texto": "Dato"},
            {"fecha": None, "texto": None},
        ])

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


@mock.patch("sheets_reports.services.sheets.get_sheet_dataframe", side_effect=lambda *a, **k: sales_df())
class ApiTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_superuser("admin", "a@a.com", "x")
        self.client.force_login(user)
        self.dashboard, self.source = make_board(user, nombre="D")

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

    def test_crear_y_recargar_tablas_con_vacios_y_no_finitos(self, _df):
        df = sales_df()
        df["ventas"] = [100.0, float("nan"), float("inf"), float("-inf"), 0.0, -2.5]
        _df.side_effect = lambda *a, **k: df.copy()
        expected = [100.0, None, None, None, 0.0, -2.5]
        widget_ids = []

        for columns in (["ventas"], ["categoria", "ventas"]):
            with self.subTest(columns=columns):
                response = self.post_widget({"type": "table", "fields": table_fields(columns)})
                self.assertEqual(response.status_code, 201, response.content)
                created = json.loads(response.content, parse_constant=self.fail)
                self.assertIsNone(created.get("error"))
                self.assertEqual([r["ventas"] for r in created["data"]["rows"]], expected)
                widget_ids.append(created["id"])

        # F5 vuelve a pedir todos los widgets guardados, no la respuesta de creación.
        for _ in range(2):
            response = self.client.get(f"/api/dashboard/{self.dashboard.id}/render/")
            self.assertEqual(response.status_code, 200, response.content)
            loaded = json.loads(response.content, parse_constant=self.fail)
            by_id = {w["id"]: w for w in loaded["widgets"]}
            self.assertEqual(set(by_id), set(widget_ids))
            for widget_id in widget_ids:
                self.assertIsNone(by_id[widget_id]["error"])
                self.assertEqual([r["ventas"] for r in by_id[widget_id]["data"]["rows"]], expected)

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


class TableCompileTests(SimpleTestCase):
    def test_columnas_con_tipo_y_tope_de_filas(self):
        out = compiled("table", table_fields(["categoria", "ventas"]))
        self.assertEqual(out["columns"], [
            {"header": "Categoria", "field": "categoria", "numeric": False},
            {"header": "Ventas", "field": "ventas", "numeric": True},
        ])
        self.assertEqual(out["rows"][0], {"categoria": "Hogar", "ventas": 100.0})
        self.assertFalse(out["truncated"])
        self.assertEqual(out["total_rows"], 6)
