"""Varias fuentes de datos por tablero: CRUD, cada widget se calcula sobre la suya, y la
migración que pasa la hoja de cada tablero a su primera fuente."""
import importlib
import json
from urllib.parse import quote
from unittest import mock

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from sheets_reports.engine.formulas import formula_tree
from sheets_reports.models import DataSource, Widget
from sheets_reports.services.sheets import SheetError
from sheets_reports.tests.fixtures import fields, make_board, sales_df

SHEET = "sheets_reports.services.sheets.get_sheet_dataframe"


def stock_df():
    import pandas as pd
    return pd.DataFrame({"producto": ["A", "B"], "stock": [5, 7]})


def by_sheet(sheet_id, gid, **kwargs):
    """La hoja `abc` es la de ventas; `inv`, la de inventario; otra cualquiera no se puede leer."""
    if sheet_id == "abc":
        return sales_df()
    if sheet_id == "inv":
        return stock_df()
    raise SheetError("No se pudo leer la hoja.")


def json_body(client, method, url, data):
    return getattr(client, method)(url, json.dumps(data), content_type="application/json")


@mock.patch(SHEET, side_effect=by_sheet)
class SourcesCrudTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("admin", "a@a.com", "x")
        self.client.force_login(self.user)
        self.dashboard, self.source = make_board(self.user, sheet_name="Ventas", tab_name="Hoja 1")

    def test_lista_y_agrega(self, _df):
        r = json_body(self.client, "post", f"/api/dashboard/{self.dashboard.id}/sources/",
                      {"sheet_id": "inv", "sheet_gid": "9", "sheet_name": "Inventario", "tab_name": "Stock",
                       "columns": [{"name": "stock", "type": "number", "include": True}]})
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()["label"], "Inventario · Stock")
        listed = self.client.get(f"/api/dashboard/{self.dashboard.id}/sources/").json()["sources"]
        self.assertEqual([s["label"] for s in listed], ["Ventas · Hoja 1", "Inventario · Stock"])
        self.assertEqual((listed[1]["columns_included"], listed[1]["columns_total"]), (1, 1))

    def test_agregar_valida(self, _df):
        url = f"/api/dashboard/{self.dashboard.id}/sources/"
        self.assertEqual(json_body(self.client, "post", url, {"sheet_id": ""}).status_code, 400)
        self.assertEqual(json_body(self.client, "post", url, {
            "sheet_id": "inv", "columns": [{"name": "x", "type": "moneda"}]}).status_code, 400)

    def test_editar_columnas_y_eliminar_sin_chequeos(self, _df):
        widget = Widget.objects.create(dashboard=self.dashboard, source=self.source, type="bar",
                                       fields=fields(), style={})
        r = json_body(self.client, "put", f"/api/sources/{self.source.id}/",
                      {"columns": [{"name": "categoria", "type": "text", "include": False},
                                   {"name": "ventas", "type": "number", "include": True}]})
        self.assertEqual(r.status_code, 200, r.content)
        # La columna que usa el widget ya no está: el error se ve al calcularlo.
        data = self.client.get(f"/api/dashboard/{self.dashboard.id}/render/").json()
        self.assertIn("categoria", data["widgets"][0]["error"])

        self.assertEqual(self.client.delete(f"/api/sources/{self.source.id}/").status_code, 200)
        widget.refresh_from_db()
        self.assertIsNone(widget.source)
        data = self.client.get(f"/api/dashboard/{self.dashboard.id}/render/").json()
        self.assertIn("fuente de datos", data["widgets"][0]["error"])

    def test_columnas_para_editar_combinan_lo_guardado_con_la_hoja(self, _df):
        self.source.columns = [{"name": "anio", "type": "text", "include": True},
                               {"name": "categoria", "type": "text", "include": False},
                               {"name": "ya_no_esta", "type": "number", "include": True}]
        self.source.save()
        r = self.client.get(f"/api/sources/{self.source.id}/columns/").json()
        by_name = {c["name"]: c for c in r["columns"]}
        self.assertEqual(list(by_name), ["categoria", "mes", "anio", "ventas"])  # orden de la hoja
        self.assertEqual((by_name["anio"]["type"], by_name["anio"]["include"]), ("text", True))
        self.assertFalse(by_name["categoria"]["include"])
        self.assertEqual((by_name["ventas"]["type"], by_name["ventas"]["include"]), ("number", True))

    def test_fuentes_ajenas_no_se_tocan(self, _df):
        _, other = make_board(get_user_model().objects.create(username="pepe"), nombre="Ajeno")
        self.assertEqual(self.client.delete(f"/api/sources/{other.id}/").status_code, 404)
        self.assertEqual(self.client.get(f"/api/sources/{other.id}/schema/").status_code, 404)
        self.assertEqual(self.client.get(f"/api/sources/{other.id}/columns/").status_code, 404)

    def test_schema_por_fuente(self, _df):
        inv = DataSource.objects.create(dashboard=self.dashboard, sheet_id="inv")
        self.assertEqual(self.client.get(f"/api/sources/{inv.id}/schema/").json()["all_fields"],
                         ["producto", "stock"])


SALES_COLUMNS = [{"name": "categoria", "type": "text", "include": True},
                 {"name": "mes", "type": "text", "include": True},
                 {"name": "anio", "type": "number", "include": True},
                 {"name": "ventas", "type": "number", "include": True}]


def with_column(name, **changes):
    return [{**c, **changes} if c["name"] == name else c for c in SALES_COLUMNS]


@mock.patch(SHEET, side_effect=by_sheet)
class SourceChangesTests(TestCase):
    """Renombrar columnas, reemplazar la hoja, actualizar los datos y avisar qué se rompe."""

    def setUp(self):
        self.user = get_user_model().objects.create_superuser("admin", "a@a.com", "x")
        self.client.force_login(self.user)
        self.dashboard, self.source = make_board(self.user, sheet_name="Ventas", tab_name="Hoja 1",
                                                 columns=SALES_COLUMNS)
        self.widget = Widget.objects.create(
            dashboard=self.dashboard, source=self.source, type="bar", title="Por categoría",
            fields=fields(sort_by="-ventas"), style={"columnOrder": ["categoria", "ventas"]})

    def put(self, data):
        return json_body(self.client, "put", f"/api/sources/{self.source.id}/", data)

    def test_renombrar_una_columna_reescribe_sus_widgets(self, _df):
        r = self.put({"columns": with_column("ventas", label="Monto")})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["impact"], [])
        self.widget.refresh_from_db()
        self.assertEqual(self.widget.fields["metrics"][0]["field"], "Monto")
        self.assertEqual(self.widget.fields["sort_by"], "-Monto")
        self.assertEqual(self.widget.style["columnOrder"], ["categoria", "Monto"])
        # La columna se llama así en todo: schema y cálculo.
        schema = self.client.get(f"/api/sources/{self.source.id}/schema/").json()
        self.assertIn("Monto", schema["all_fields"])
        self.assertNotIn("ventas", schema["all_fields"])
        data = self.client.get(f"/api/dashboard/{self.dashboard.id}/render/").json()
        self.assertIsNone(data["widgets"][0]["error"])
        # Y se puede volver al encabezado original.
        self.put({"columns": SALES_COLUMNS})
        self.widget.refresh_from_db()
        self.assertEqual(self.widget.fields["metrics"][0]["field"], "ventas")

    def test_dos_columnas_con_el_mismo_nombre_a_mostrar_dan_error(self, _df):
        r = self.put({"columns": with_column("mes", label="categoria")})
        self.assertEqual(r.status_code, 400)
        self.assertIn("«categoria»", r.json()["error"])

    def test_dry_run_avisa_sin_guardar(self, _df):
        r = self.put({"columns": with_column("categoria", include=False), "dry_run": True})
        self.assertEqual(r.json(), {"impact": [{"id": self.widget.id, "title": "Por categoría",
                                                "columns": ["categoria"]}]})
        self.source.refresh_from_db()
        self.assertEqual(self.source.columns, SALES_COLUMNS)

    def test_cambiar_el_tipo_tambien_avisa(self, _df):
        r = self.put({"columns": with_column("ventas", type="text"), "dry_run": True})
        self.assertEqual(r.json()["impact"][0]["columns"], ["ventas"])

    def test_nombre_propio_de_la_fuente(self, _df):
        r = self.put({"columns": SALES_COLUMNS, "name": "Ventas 2026"})
        self.assertEqual((r.json()["label"], r.json()["name"], r.json()["original_label"]),
                         ("Ventas 2026", "Ventas 2026", "Ventas · Hoja 1"))
        r = self.put({"columns": SALES_COLUMNS, "name": " "})
        self.assertEqual(r.json()["label"], "Ventas · Hoja 1")

    def test_reemplazar_la_hoja(self, _df):
        payload = {"sheet_id": "inv", "sheet_gid": "7", "sheet_name": "Inventario", "tab_name": "Stock",
                   "columns": [{"name": "producto", "type": "text", "include": True},
                               {"name": "stock", "type": "number", "include": True}]}
        impact = self.put({**payload, "dry_run": True}).json()["impact"]
        self.assertEqual(impact[0]["columns"], ["categoria", "ventas"])
        r = self.put(payload)
        self.assertEqual(r.status_code, 200, r.content)
        self.source.refresh_from_db()
        self.assertEqual((self.source.sheet_id, self.source.gid, self.source.label),
                         ("inv", "7", "Inventario · Stock"))
        self.widget.refresh_from_db()
        self.assertEqual(self.widget.source, self.source)   # el widget sigue en la misma fuente

    def test_actualizar_datos_relee_la_hoja_sin_guardar(self, _df):
        saved = [*with_column("ventas", label="Monto"), {"name": "borrada", "type": "text", "include": True}]
        self.source.columns = saved
        self.source.save()
        with mock.patch("sheets_reports.services.sheets.refresh_sheet", return_value=sales_df()) as refresh:
            r = self.client.get(f"/api/sources/{self.source.id}/columns/?refresh=1")
        self.assertEqual(r.status_code, 200, r.content)
        refresh.assert_called_once_with("abc", "0", headers=True)
        # La estructura actual de la hoja, con lo guardado de las columnas que siguen.
        columns = r.json()["columns"]
        self.assertEqual([c["name"] for c in columns], ["categoria", "mes", "anio", "ventas"])
        self.assertEqual(columns[3]["label"], "Monto")
        # Nada se guarda hasta «Guardar cambios».
        self.source.refresh_from_db()
        self.assertEqual(self.source.columns, saved)

    def test_actualizar_datos_con_la_hoja_caida(self, _df):
        with mock.patch("sheets_reports.services.sheets.refresh_sheet", side_effect=SheetError("caída")):
            r = self.client.get(f"/api/sources/{self.source.id}/columns/?refresh=1")
        self.assertEqual((r.status_code, r.json()["error"]), (502, "caída"))

    def test_actualizar_datos_en_el_selector_de_una_hoja_nueva(self, _df):
        with mock.patch("sheets_reports.services.sheets.refresh_sheet", return_value=stock_df()) as refresh:
            r = self.client.get("/api/sources/google/spreadsheets/inv/tabs/3/columns/?refresh=1&headers=0")
        self.assertEqual([c["name"] for c in r.json()["columns"]], ["producto", "stock"])
        refresh.assert_called_once_with("inv", "3", headers=False)

    def test_eliminar_en_dry_run_lista_sus_widgets(self, _df):
        r = self.client.delete(f"/api/sources/{self.source.id}/?dry_run=1")
        self.assertEqual(r.json()["impact"], [{"id": self.widget.id, "title": "Por categoría", "columns": []}])
        self.assertTrue(DataSource.objects.filter(id=self.source.id).exists())

    def test_columnas_para_editar_traen_el_nombre_a_mostrar_y_los_encabezados(self, _df):
        self.source.columns = with_column("ventas", label="Monto")
        self.source.save()
        r = self.client.get(f"/api/sources/{self.source.id}/columns/").json()
        self.assertTrue(r["first_row_headers"])
        self.assertEqual({c["name"]: c["label"] for c in r["columns"]}["ventas"], "Monto")
        self.client.get(f"/api/sources/{self.source.id}/columns/?headers=0")
        self.assertEqual(_df.call_args.kwargs, {"headers": False})


SHARE = {"id": "s", "name": "Participación", "formula": "SUM(ventas) / SUM(anio) * 100", "format": "percent"}


@mock.patch(SHEET, side_effect=by_sheet)
class CalculatedFieldsTests(TestCase):
    """Campos calculados de la fuente: se guardan validados contra la hoja, los widgets los
    usan (por fila como columna, agregados como métrica «auto») y siguen sus renombres."""

    def setUp(self):
        self.user = get_user_model().objects.create_superuser("admin", "a@a.com", "x")
        self.client.force_login(self.user)
        self.dashboard, self.source = make_board(self.user, columns=SALES_COLUMNS)

    def put(self, data):
        return json_body(self.client, "put", f"/api/sources/{self.source.id}/",
                         {"columns": SALES_COLUMNS, **data})

    def test_guardar_y_usar_en_widgets(self, _df):
        double = {"id": "d", "name": "Doble", "formula": "ventas * 2"}
        r = self.put({"calculated_fields": [double, SHARE]})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["calculated_fields"][1]["format"], "percent")
        # Con su árbol para el constructor de bloques (no se guarda).
        self.assertEqual(r.json()["calculated_fields"][0]["tree"]["kind"], "bin")
        self.source.refresh_from_db()
        self.assertNotIn("tree", self.source.calculated_fields[0])
        schema = self.client.get(f"/api/sources/{self.source.id}/schema/").json()
        self.assertIn("Doble", schema["all_fields"])                   # por fila: una columna más
        self.assertEqual(schema["aggregated_fields"], [{"name": "Participación", "format": "percent"}])
        Widget.objects.create(dashboard=self.dashboard, source=self.source, type="bar", fields=fields(
            metrics=[{"field": "Participación", "agg": "auto", "alias": "p"}]), style={})
        data = self.client.get(f"/api/dashboard/{self.dashboard.id}/render/").json()["widgets"][0]
        self.assertIsNone(data["error"])
        self.assertEqual(data["data"]["percent"], ["Participación"])

    def test_formula_invalida_no_se_guarda(self, _df):
        r = self.put({"calculated_fields": [{"id": "x", "name": "Malo", "formula": "SUM(ventas) / anio"}]})
        self.assertEqual(r.status_code, 400)
        self.assertIn("«Malo»", r.json()["error"])
        self.source.refresh_from_db()
        self.assertEqual(self.source.calculated_fields, [])

    def test_forma_invalida(self, _df):
        for bad, message in (([{"id": "a", "name": "", "formula": "1"}], "nombre"),
                             ([SHARE, {**SHARE}], "dos campos"),
                             ([{**SHARE, "format": "moneda"}], "Formato")):
            with self.subTest(message=message):
                r = self.put({"calculated_fields": bad})
                self.assertEqual(r.status_code, 400)
                self.assertIn(message, r.json()["error"])

    def test_renombrar_una_columna_reescribe_las_formulas(self, _df):
        self.put({"calculated_fields": [SHARE]})
        r = self.put({"columns": with_column("ventas", label="Monto"), "calculated_fields": [SHARE]})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["calculated_fields"][0]["formula"], "SUM([Monto]) / SUM(anio) * 100")

    def test_renombrar_o_quitar_un_campo_sigue_a_sus_widgets(self, _df):
        self.put({"calculated_fields": [SHARE]})
        widget = Widget.objects.create(dashboard=self.dashboard, source=self.source, type="bar", title="Part.",
                                       fields=fields(metrics=[{"field": "Participación", "agg": "auto", "alias": "p"}]),
                                       style={})
        self.put({"calculated_fields": [{**SHARE, "name": "% Part."}]})
        widget.refresh_from_db()
        self.assertEqual(widget.fields["metrics"][0]["field"], "% Part.")
        impact = self.put({"calculated_fields": [], "dry_run": True}).json()["impact"]
        self.assertEqual(impact, [{"id": widget.id, "title": "Part.", "columns": ["% Part."]}])

    def test_agregar_los_campos_que_propone_la_ia(self, _df):
        url = f"/api/sources/{self.source.id}/calculated-fields/"
        new = {"name": "Participación", "formula": SHARE["formula"], "format": "percent"}
        r = json_body(self.client, "post", url, {"fields": [new]})
        self.assertEqual(r.status_code, 200, r.content)
        saved = r.json()["calculated_fields"]
        self.assertEqual([(f["name"], f["format"]) for f in saved], [("Participación", "percent")])
        self.assertTrue(saved[0]["id"].startswith("cf_"))
        # Aplicar otra vez la misma propuesta no lo duplica; otra fórmula con el mismo nombre falla.
        self.assertEqual(len(json_body(self.client, "post", url, {"fields": [new]}).json()["calculated_fields"]), 1)
        clash = json_body(self.client, "post", url, {"fields": [{**new, "formula": "SUM(ventas)"}]})
        self.assertEqual(clash.status_code, 400)
        broken = json_body(self.client, "post", url, {"fields": [{"name": "Malo", "formula": "SUM(nada)"}]})
        self.assertIn("no existe", broken.json()["error"])

    def test_vista_previa_de_la_formula(self, _df):
        url = f"/api/sources/{self.source.id}/formula/"
        row = json_body(self.client, "post", url, {"formula": "ventas * 2"}).json()
        self.assertEqual(row, {"formula": "ventas * 2", "kind": "row", "values": [200.0, 600.0, 100.0, 160.0, 400.0]})
        total = json_body(self.client, "post", url, {"formula": "SUM(ventas)"}).json()
        self.assertEqual(total, {"formula": "SUM(ventas)", "kind": "aggregated", "values": [755.0]})
        # Con lo que hay en el editor sin guardar: una columna renombrada y un campo anterior.
        draft = json_body(self.client, "post", url, {
            "formula": "[Doble] + [Monto]", "columns": with_column("ventas", label="Monto"),
            "calculated_fields": [{"id": "d", "name": "Doble", "formula": "Monto * 2"}]}).json()
        self.assertEqual(draft["values"][0], 300.0)
        error = json_body(self.client, "post", url, {"formula": "SUM(ventas) / anio"}).json()
        self.assertIn("mezcla", error["error"])

    def test_generar_la_formula_con_ia(self, _df):
        url = f"/api/sources/{self.source.id}/formula/ai/"
        answer = {"ok": True, "formula": "[Monto] * 2", "name": "Doble"}
        with mock.patch("sheets_reports.services.ai_formula.generate_json", return_value=answer):
            # Con lo que hay en el editor sin guardar: «ventas» se muestra como «Monto».
            out = json_body(self.client, "post", url, {"prompt": "el doble", "columns": with_column("ventas", label="Monto")}).json()
        self.assertEqual((out["formula"], out["name"], out["kind"]), ("[Monto] * 2", "Doble", "row"))
        self.assertEqual(out["tree"], formula_tree("[Monto] * 2"))
        with mock.patch("sheets_reports.services.ai_formula.generate_json", return_value={"ok": False, "reason": "No hay costos."}):
            self.assertIn("No hay costos.", json_body(self.client, "post", url, {"prompt": "margen"}).json()["error"])
        self.assertEqual(json_body(self.client, "post", url, {"prompt": " "}).status_code, 400)

    def test_el_constructor_manda_el_arbol(self, _df):
        """El constructor de bloques manda `tree`: el servidor lo escribe como texto."""
        url = f"/api/sources/{self.source.id}/formula/"
        total = json_body(self.client, "post", url, {"tree": formula_tree("SUM([ventas]) * 2")}).json()
        self.assertEqual(total, {"formula": "SUM([ventas]) * 2", "kind": "aggregated", "values": [1510.0]})
        hole = {"kind": "bin", "value": "+", "args": [{"kind": "col", "value": "ventas", "args": []}, None]}
        self.assertIn("huecos", json_body(self.client, "post", url, {"tree": hole}).json()["error"])
        # Al guardar, el árbol se guarda como texto; con huecos, 400 con el nombre del campo.
        r = self.put({"calculated_fields": [{"id": "s", "name": "Doble", "tree": formula_tree("[ventas] * 2")}]})
        self.assertEqual(r.status_code, 200, r.content)
        self.source.refresh_from_db()
        self.assertEqual(self.source.calculated_fields[0]["formula"], "[ventas] * 2")
        r = self.put({"calculated_fields": [{"id": "s", "name": "Doble", "tree": hole}]})
        self.assertEqual(r.status_code, 400)
        self.assertIn("«Doble»", r.json()["error"])


@mock.patch(SHEET, side_effect=by_sheet)
class MultiSourceWidgetsTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("admin", "a@a.com", "x")
        self.client.force_login(self.user)
        self.dashboard, self.sales = make_board(self.user)
        self.inv = DataSource.objects.create(dashboard=self.dashboard, sheet_id="inv")

    def widget(self, source, **field_kwargs):
        return Widget.objects.create(dashboard=self.dashboard, source=source, type="bar",
                                     fields=fields(**field_kwargs), style={})

    def render(self, query=""):
        return self.client.get(f"/api/dashboard/{self.dashboard.id}/render/{query}").json()

    def test_cada_widget_se_calcula_sobre_su_fuente(self, _df):
        self.widget(self.sales)
        self.widget(self.inv, dimensions=["producto"], metrics=[{"agg": "sum", "field": "stock", "alias": "s"}])
        data = self.render()
        self.assertEqual(sorted(data["widgets"][0]["data"]["categories"]), ["Electrónica", "Hogar", "Ropa"])
        self.assertEqual(data["widgets"][1]["data"]["categories"], ["A", "B"])
        self.assertEqual(data["widgets"][1]["source_id"], self.inv.id)
        self.assertEqual([s["id"] for s in data["dashboard"]["sources"]], [self.sales.id, self.inv.id])

    def test_una_fuente_que_falla_solo_afecta_a_sus_widgets(self, _df):
        broken = DataSource.objects.create(dashboard=self.dashboard, sheet_id="rota")
        self.widget(self.sales)
        self.widget(broken)
        data = self.render()
        self.assertIsNone(data["widgets"][0]["error"])
        self.assertIn("No se pudo leer", data["widgets"][1]["error"])

    def test_filtro_del_tablero_en_columna_de_una_sola_fuente_no_avisa(self, _df):
        self.widget(self.sales)
        self.widget(self.inv, dimensions=["producto"], metrics=[{"agg": "sum", "field": "stock", "alias": "s"}])
        flt = quote(json.dumps([{"field": "categoria", "op": "in", "value": ["Hogar"]}]))
        data = self.render(f"?filters={flt}")
        self.assertEqual(data["filter_errors"], [])
        self.assertEqual(data["widgets"][0]["data"]["categories"], ["Hogar"])
        self.assertEqual(data["widgets"][1]["data"]["categories"], ["A", "B"])
        flt = quote(json.dumps([{"field": "pais", "op": "in", "value": ["DO"]}]))
        self.assertEqual(len(self.render(f"?filters={flt}")["filter_errors"]), 1)

    def test_crear_widget_en_otra_fuente_valida_contra_esa_hoja(self, _df):
        url = f"/api/dashboard/{self.dashboard.id}/widgets/"
        payload = {"type": "bar", "fields": fields(dimensions=["producto"],
                                                   metrics=[{"agg": "sum", "field": "stock", "alias": "s"}])}
        self.assertEqual(json_body(self.client, "post", url, payload).status_code, 422)  # primera = ventas
        r = json_body(self.client, "post", url, {**payload, "source": self.inv.id})
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(Widget.objects.get(id=r.json()["id"]).source, self.inv)

    def test_fuente_de_otro_tablero_se_rechaza(self, _df):
        _, other = make_board(self.user, nombre="Otro")
        widget = self.widget(self.sales)
        r = json_body(self.client, "post", f"/api/dashboard/{self.dashboard.id}/widgets/",
                      {"type": "bar", "fields": fields(), "source": other.id})
        self.assertEqual(r.status_code, 400)
        r = json_body(self.client, "put", f"/api/widget/{widget.id}/", {"source": other.id})
        self.assertEqual(r.status_code, 400)

    def test_cambiar_la_fuente_de_un_widget(self, _df):
        widget = self.widget(self.sales)
        r = json_body(self.client, "put", f"/api/widget/{widget.id}/", {
            "source": self.inv.id,
            "fields": fields(dimensions=["producto"], metrics=[{"agg": "sum", "field": "stock", "alias": "s"}])})
        self.assertEqual(r.status_code, 200, r.content)
        widget.refresh_from_db()
        self.assertEqual(widget.source, self.inv)


class MigrationTests(SimpleTestCase):
    """La función de datos de 0010: la hoja del tablero pasa a ser su primera fuente."""

    def test_la_hoja_del_tablero_pasa_a_una_fuente(self):
        migration = importlib.import_module("sheets_reports.migrations.0010_data_sources")
        created, updated = [], []
        dashboard = mock.Mock(sheet_url="https://docs.google.com/spreadsheets/d/ab_C-1/edit#gid=5",
                              sheet_gid="5", sheet_name="Ventas", tab_name="Hoja", columns=None)
        models = {
            "Dashboard": mock.Mock(objects=mock.Mock(all=lambda: [dashboard])),
            "DataSource": mock.Mock(objects=mock.Mock(create=lambda **kw: created.append(kw) or "src")),
            "Widget": mock.Mock(objects=mock.Mock(
                filter=lambda **kw: mock.Mock(update=lambda **u: updated.append((kw, u))))),
        }
        apps = mock.Mock(get_model=lambda app, name: models[name])
        migration.sheets_to_sources(apps, None)
        self.assertEqual(created, [{"dashboard": dashboard, "sheet_id": "ab_C-1", "gid": "5",
                                    "sheet_name": "Ventas", "tab_name": "Hoja", "columns": []}])
        self.assertEqual(updated, [({"dashboard": dashboard}, {"source": "src"})])


@mock.patch(SHEET, side_effect=by_sheet)
class ColumnFormatTests(TestCase):
    """El formato de una columna numérica (moneda, %) se elige en la fuente y lo heredan las
    métricas que la resumen; la métrica puede elegir otro."""

    def setUp(self):
        self.user = get_user_model().objects.create_superuser("admin", "a@a.com", "x")
        self.client.force_login(self.user)
        self.dashboard, self.source = make_board(self.user, columns=SALES_COLUMNS)

    def put(self, columns):
        return json_body(self.client, "put", f"/api/sources/{self.source.id}/", {"columns": columns})

    def render(self, widget_type, widget_fields, **kwargs):
        Widget.objects.all().delete()
        Widget.objects.create(dashboard=self.dashboard, source=self.source, type=widget_type,
                              fields=widget_fields, style={}, **kwargs)
        data = self.client.get(f"/api/dashboard/{self.dashboard.id}/render/").json()["widgets"][0]
        self.assertIsNone(data["error"])
        return data["data"]

    def test_se_guarda_solo_en_columnas_numericas(self, _df):
        r = self.put([{**c, "format": "percent"} if c["name"] == "categoria" else
                      {**c, "format": "currency"} if c["name"] == "ventas" else c for c in SALES_COLUMNS])
        self.assertEqual(r.status_code, 200, r.content)
        self.source.refresh_from_db()
        saved = {c["name"]: c.get("format") for c in self.source.columns}
        self.assertEqual(saved, {"categoria": None, "mes": None, "anio": None, "ventas": "currency"})
        columns = self.client.get(f"/api/sources/{self.source.id}/columns/").json()["columns"]
        self.assertEqual({c["name"]: c["format"] for c in columns}["ventas"], "currency")

    def test_formato_desconocido_da_error(self, _df):
        r = self.put(with_column("ventas", format="moneda"))
        self.assertEqual(r.status_code, 400)
        self.assertIn("Formato", r.json()["error"])

    def test_la_tabla_dinamica_hereda_el_formato_en_todas_sus_columnas(self, _df):
        self.put(with_column("ventas", format="currency"))
        data = self.render("dynamic_table", fields(pivots=["mes"], metrics=[
            {"field": "ventas", "agg": "sum", "alias": "total_ventas"},
            {"agg": "count", "alias": "cantidad"},
        ]))
        formats = data["formats"]
        ventas = [f for f in formats if f.endswith("total_ventas")]
        self.assertEqual(len(ventas), 3 + 1)                       # Ene, Feb, Mar + Total general
        self.assertEqual({formats[f] for f in ventas}, {"currency"})
        self.assertFalse(any(f.endswith("cantidad") for f in formats))   # un conteo no es moneda

    def test_la_metrica_elige_otro_formato(self, _df):
        self.put(with_column("ventas", format="currency"))
        data = self.render("dynamic_table", fields(metrics=[
            {"field": "ventas", "agg": "sum", "alias": "total_ventas", "format": "progress"},
            {"field": "ventas", "agg": "avg", "alias": "promedio", "format": "number"},
        ]))
        self.assertEqual(data["formats"], {"total_ventas": "progress"})

    def test_la_tabla_y_el_kpi_tambien(self, _df):
        self.put(with_column("ventas", format="currency"))
        table = self.render("table", {"dimensions": [], "pivots": [], "metrics": [], "filters": [],
                                      "columns": [{"field": "categoria"}, {"field": "ventas"}],
                                      "sort_by": None, "limit": None})
        self.assertEqual(table["formats"], {"ventas": "currency"})
        kpi = self.render("kpi", fields(dimensions=[], metrics=[{"field": "ventas", "agg": "sum", "alias": "t"}]))
        self.assertEqual(kpi["format"], "currency")
