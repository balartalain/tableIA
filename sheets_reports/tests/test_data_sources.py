"""Varias fuentes de datos por tablero: CRUD, cada widget se calcula sobre la suya, y la
migración que pasa la hoja de cada tablero a su primera fuente."""
import importlib
import json
from urllib.parse import quote
from unittest import mock

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

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
