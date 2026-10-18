"""Conectar la hoja al crear un tablero: listar el Drive de la cuenta de servicio, sus
pestañas y columnas, y guardar qué columnas se usan y con qué tipo."""
import json
from unittest import mock

import pandas as pd
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings

from sheets_reports.models import Dashboard
from sheets_reports.services import sheets
from sheets_reports.services.sheets import SheetError, apply_column_config, infer_column_types
from sheets_reports.tests.fixtures import make_board, sales_df

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"},
          "sheets": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache",
                     "LOCATION": "sheets-tests", "TIMEOUT": None}}


def raw_df() -> pd.DataFrame:
    """Como llega de gviz: montos con coma de miles como texto y fechas día/mes/año."""
    return pd.DataFrame({
        "categoria": ["Hogar", "Ropa", None],
        "monto": ["1,200", "300", None],
        "fecha": ["05/01/2026", "20/02/2026", None],
        "mes": ["Ene", "Feb", "Mar"],
        "anio": [2026, 2025, 2026],
    })


class ColumnTypesTests(SimpleTestCase):
    def test_infiere_numero_fecha_y_texto(self):
        df = raw_df()
        df["monto"] = pd.to_numeric(df["monto"].str.replace(",", ""))
        self.assertEqual(infer_column_types(df),
                         {"categoria": "text", "monto": "number", "fecha": "date",
                          "mes": "date", "anio": "number"})

    def test_sin_configuracion_devuelve_la_hoja_tal_cual(self):
        df = raw_df()
        self.assertIs(apply_column_config(df, []), df)

    def test_un_tipo_guardado_que_dejaria_la_columna_vacia_se_ignora(self):
        df = pd.DataFrame({"opcion": ["Sí", "No", "Sí"], "vacia": [None, None, None]})
        out = apply_column_config(df, [{"name": "opcion", "type": "number", "include": True},
                                       {"name": "vacia", "type": "number", "include": True}])
        self.assertEqual(out["opcion"].tolist(), ["Sí", "No", "Sí"])
        self.assertTrue(out["vacia"].isna().all())

    def test_excluye_y_convierte(self):
        df = apply_column_config(raw_df(), [
            {"name": "categoria", "type": "text", "include": False},
            {"name": "monto", "type": "number", "include": True},
            {"name": "fecha", "type": "date", "include": True},
            {"name": "anio", "type": "text", "include": True},
            {"name": "ya_no_existe", "type": "number", "include": True},
        ])
        self.assertNotIn("categoria", df.columns)
        self.assertEqual(df["monto"].tolist()[:2], [1200.0, 300.0])
        self.assertTrue(pd.isna(df["monto"].iloc[2]))
        self.assertEqual(df["fecha"].tolist(), ["2026-01-05", "2026-02-20", None])
        self.assertEqual(df["anio"].tolist(), ["2026", "2025", "2026"])
        self.assertEqual(df["mes"].tolist(), ["Ene", "Feb", "Mar"])  # sin configurar: intacta

    def test_el_nombre_a_mostrar_reemplaza_al_encabezado(self):
        df = apply_column_config(raw_df(), [
            {"name": "monto", "label": "Monto total", "type": "number", "include": True},
            {"name": "categoria", "label": "Nunca", "type": "text", "include": False},
            {"name": "mes", "label": "", "type": "text", "include": True},
        ])
        self.assertEqual(list(df.columns), ["Monto total", "fecha", "mes", "anio"])
        self.assertEqual(df["Monto total"].tolist()[:2], [1200.0, 300.0])

    def test_meses_y_anios_como_fecha_quedan_como_periodo(self):
        df = apply_column_config(raw_df(), [{"name": "mes", "type": "date", "include": True},
                                            {"name": "anio", "type": "date", "include": True}])
        self.assertEqual(df["mes"].tolist(), ["Ene", "Feb", "Mar"])
        self.assertEqual(df["anio"].tolist(), ["2026", "2025", "2026"])


class ColumnAliasTests(SimpleTestCase):
    """Una columna sin alias guardado (el ID, una nueva de la hoja) recibe el mismo en el
    editor y en el motor, y no repite el de ninguna guardada."""

    SAVED = [{"name": "a", "type": "text", "include": True, "alias": "col_2"},
             {"name": "b", "type": "text", "include": True, "alias": "col_3"}]

    def frame(self, *names) -> pd.DataFrame:
        return pd.DataFrame({name: ["x"] for name in names})

    def editor_aliases(self, df, saved) -> dict:
        from sheets_reports.views import _merge_saved_columns, _sheet_columns
        return {c["name"]: c["alias"] for c in _merge_saved_columns(_sheet_columns(df), saved)}

    def test_el_id_toma_el_alias_de_su_posicion(self):
        df = self.frame("_ID", "a", "b")
        aliases = apply_column_config(df, self.SAVED).attrs["column_aliases"]
        self.assertEqual(aliases, {"_ID": "col_1", "a": "col_2", "b": "col_3"})
        self.assertEqual(self.editor_aliases(df, self.SAVED), aliases)

    def test_con_una_excluida_no_se_repite(self):
        saved = [{**self.SAVED[0], "include": False}, self.SAVED[1]]
        df = self.frame("_ID", "a", "b")
        aliases = apply_column_config(df, saved).attrs["column_aliases"]
        self.assertEqual(aliases, {"_ID": "col_1", "b": "col_3"})
        self.assertEqual(self.editor_aliases(df, saved)["_ID"], "col_1")

    def test_el_id_al_final_no_pisa_el_alias_de_la_ultima(self):
        saved = [{**self.SAVED[0], "alias": "col_1"}, {**self.SAVED[1], "alias": "col_3"}]
        df = self.frame("a", "b", "_ID")
        aliases = apply_column_config(df, saved).attrs["column_aliases"]
        self.assertEqual(aliases, {"a": "col_1", "b": "col_3", "_ID": "col_4"})
        self.assertEqual(self.editor_aliases(df, saved), aliases)

    def test_una_columna_nueva_en_medio_no_repite(self):
        df = self.frame("_ID", "nueva", "a", "b")
        aliases = apply_column_config(df, self.SAVED).attrs["column_aliases"]
        self.assertEqual(aliases, {"_ID": "col_1", "nueva": "col_4", "a": "col_2", "b": "col_3"})
        self.assertEqual(self.editor_aliases(df, self.SAVED), aliases)


class SourceEndpointsTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("admin", "a@a.com", "x")
        self.client.force_login(self.user)

    def test_pestanas(self):
        tabs = {"name": "Ventas", "tabs": [{"gid": "0", "title": "Hoja 1"}]}
        with mock.patch("sheets_reports.services.google_drive.list_tabs", return_value=tabs):
            r = self.client.get("/api/sources/google/spreadsheets/abc/tabs/")
        self.assertEqual(r.json(), tabs)

    def test_columnas_con_tipo_inferido_y_ejemplos(self):
        with mock.patch("sheets_reports.services.sheets.get_sheet_dataframe", return_value=sales_df()) as get:
            r = self.client.get("/api/sources/google/spreadsheets/abc/tabs/123/columns/")
        get.assert_called_once_with("abc", "123", headers=True)
        data = r.json()
        self.assertEqual(data["rows"], 6)
        by_name = {c["name"]: c for c in data["columns"]}
        self.assertEqual(by_name["ventas"]["type"], "number")
        self.assertEqual(by_name["categoria"]["type"], "text")
        self.assertEqual(by_name["categoria"]["samples"], ["Hogar", "Electrónica", "Ropa"])

    def test_editor_nuevo_abre_el_selector(self):
        r = self.client.get("/tableros/nuevo/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "window.DASHBOARD_ID = null")
        self.assertContains(r, "sourcePicker({ mode: 'create' })")
        self.assertNotContains(r, 'id="share-btn"')


class CreateFromSourceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("admin", "a@a.com", "x")
        self.client.force_login(self.user)

    def post(self, **data):
        body = {"nombre": "Ventas", "sheet_id": "abc_1-2", "sheet_gid": "77",
                "sheet_name": "Ventas 2026", "tab_name": "Detalle", **data}
        return self.client.post("/api/dashboards/", json.dumps(body), content_type="application/json")

    def test_crea_con_la_hoja_y_las_columnas(self):
        columns = [{"name": "ventas", "type": "number", "include": True},
                   {"name": "categoria", "type": "text", "include": False}]
        r = self.post(columns=columns)
        self.assertEqual(r.status_code, 201, r.content)
        source = Dashboard.objects.get(id=r.json()["id"]).sources.get()
        self.assertEqual((source.sheet_id, source.gid), ("abc_1-2", "77"))
        self.assertEqual((source.sheet_name, source.tab_name), ("Ventas 2026", "Detalle"))
        self.assertEqual(source.columns, columns)
        self.assertEqual(r.json()["sources"], ["Ventas 2026 · Detalle"])

    def test_crea_con_alias_y_se_conserva(self):
        columns = [{"name": "ventas", "type": "number", "include": True, "alias": "col_1"},
                   {"name": "categoria", "type": "text", "include": False, "alias": "col_2"}]
        r = self.post(columns=columns)
        self.assertEqual(r.status_code, 201, r.content)
        source = Dashboard.objects.get(id=r.json()["id"]).sources.get()
        self.assertEqual(source.columns, columns)

    def test_valida_las_columnas(self):
        for columns in ([{"name": "ventas", "type": "moneda", "include": True}],
                        [{"name": "ventas", "type": "number", "include": False}],
                        [{"type": "number"}],
                        "ventas"):
            self.assertEqual(self.post(columns=columns).status_code, 400, columns)
        self.assertEqual(self.post(sheet_id="../x").status_code, 400)

    def test_el_tablero_usa_las_columnas_elegidas(self):
        r = self.post(columns=[{"name": "categoria", "type": "text", "include": False},
                               {"name": "anio", "type": "text", "include": True}])
        with mock.patch("sheets_reports.services.sheets.get_sheet_dataframe", return_value=sales_df()):
            source = Dashboard.objects.get(id=r.json()["id"]).sources.get()
            schema = self.client.get(f"/api/sources/{source.id}/schema/").json()
        self.assertNotIn("categoria", schema["all_fields"])
        self.assertNotIn("anio", schema["numeric_fields"])
        self.assertIn("ventas", schema["numeric_fields"])

    def test_abrir_el_editor_marca_la_ultima_apertura(self):
        d, _ = make_board(self.user, nombre="x")
        self.assertIsNone(self.client.get("/api/dashboards/").json()[0]["last_opened_at"])
        self.client.get(f"/tableros/{d.id}/edit/")
        d.refresh_from_db()
        self.assertIsNotNone(d.last_opened_at)


def csv_response(text):
    return mock.Mock(status_code=200, headers={"Content-Type": "text/csv; charset=utf-8"}, text=text)


@mock.patch("sheets_reports.services.sheets._read_attempts", return_value=[None])
class FetchSheetTests(SimpleTestCase):
    CSV = "Producto,Ventas,,\r\nA,\"1,200\",,\r\nB,300,,\r\n"

    def test_con_encabezados(self, _read):
        with mock.patch("requests.get", return_value=csv_response(self.CSV)) as get:
            df = sheets.fetch_sheet_dataframe("abc", "5")
        self.assertEqual(get.call_args.kwargs["params"], {"format": "csv", "gid": "5"})
        self.assertEqual(list(df.columns), ["Producto", "Ventas"])   # sin las de relleno
        self.assertEqual(df["Ventas"].tolist(), [1200, 300])

    def test_sin_encabezados_las_columnas_se_llaman_por_su_letra(self, _read):
        with mock.patch("requests.get", return_value=csv_response(self.CSV)):
            df = sheets.fetch_sheet_dataframe("abc", "5", headers=False)
        self.assertEqual(list(df.columns), ["Columna A", "Columna B"])
        self.assertEqual(df["Columna A"].tolist(), ["Producto", "A", "B"])   # la fila 1 es un dato

    def test_letras_de_columna(self, _read):
        self.assertEqual([sheets.column_letter(i) for i in (0, 25, 26, 27, 701, 702)],
                         ["A", "Z", "AA", "AB", "ZZ", "AAA"])

    def test_respuesta_que_no_es_csv(self, _read):
        html = mock.Mock(status_code=200, headers={"Content-Type": "text/html"}, text="<html>")
        with mock.patch("requests.get", return_value=html):
            with self.assertRaises(SheetError):
                sheets.fetch_sheet_dataframe("abc", "5")


@override_settings(CACHES=LOCMEM)
class SheetCacheTests(SimpleTestCase):
    def setUp(self):
        from django.core.cache import caches
        caches["sheets"].clear()

    def test_la_hoja_queda_en_cache_hasta_actualizar(self):
        source = mock.Mock(sheet_id="abc", gid="0", first_row_headers=True, is_form_response=False)
        with mock.patch.object(sheets, "fetch_sheet_dataframe", return_value=sales_df()) as fetch:
            self.assertIsNone(sheets.fetched_at(source))
            sheets.get_sheet_dataframe("abc", "0")
            sheets.get_sheet_dataframe("abc", "0")
            self.assertEqual(fetch.call_count, 1)
            first = sheets.fetched_at(source)
            self.assertIsNotNone(first)

            refreshed = sheets.refresh_sheet("abc", "0")
            self.assertEqual(len(refreshed), len(sales_df()))   # devuelve el frame nuevo
            self.assertEqual(fetch.call_count, 2)
            self.assertGreaterEqual(sheets.fetched_at(source), first)

            # Con otra opción de encabezados es otra lectura.
            sheets.get_sheet_dataframe("abc", "0", headers=False)
            self.assertEqual(fetch.call_count, 3)

    def test_devuelve_copias(self):
        with mock.patch.object(sheets, "fetch_sheet_dataframe", return_value=sales_df()):
            sheets.get_sheet_dataframe("abc", "0")["ventas"] = 0
            self.assertNotEqual(sheets.get_sheet_dataframe("abc", "0")["ventas"].sum(), 0)
