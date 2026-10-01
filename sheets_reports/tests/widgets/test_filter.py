"""Caja de filtros (`filter`): opciones por columna; la selección viaja como filtros del tablero."""
import json
from unittest import mock
from urllib.parse import quote

import pandas as pd
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from sheets_reports.dsl.conditions import distinct_values
from sheets_reports.engine.plans import ColumnValuesResult
from sheets_reports.models import Dashboard, Widget
from sheets_reports.services.ai_spec import build_tool_parameters
from sheets_reports.tests.fixtures import agg, compiled, errors_for, execute, sales_ctx, sales_df, spec, view


def filter_spec(columns=("categoria", "anio"), **overrides):
    return spec(**{"dimensions": [], "metrics": [], "columns": list(columns), **overrides})


class ValidationTests(SimpleTestCase):
    def test_spec_valido(self):
        self.assertEqual(errors_for("filter", filter_spec()), [])

    def test_requiere_al_menos_un_filtro(self):
        self.assertEqual(errors_for("filter", filter_spec(columns=[])),
                         ["columns: elige al menos una columna para mostrar."])

    def test_columna_inexistente_o_repetida(self):
        self.assertIn("'region' no existe", errors_for("filter", filter_spec(columns=["region"]))[0])
        self.assertIn("no se puede repetir", errors_for("filter", filter_spec(columns=["mes", "mes"]))[0])

    def test_no_lleva_metricas_ni_orden(self):
        self.assertIn("no lleva métricas", errors_for("filter", filter_spec(metrics=[agg("t")]))[0])
        self.assertIn("no se ordena", errors_for("filter", filter_spec(sort={"by": "anio", "dir": "asc"}))[0])


class DistinctValuesTests(SimpleTestCase):
    def test_opciones_normalizadas_como_las_compara_el_filtro(self):
        self.assertEqual(distinct_values(pd.Series([" Hogar", "Hogar ", "Ropa", None, "  ", "Árbol"])),
                         ["Árbol", "Hogar", "Ropa"])
        # Una columna entera leída como float (por una celda vacía) da enteros: 2026, no 2026.0.
        self.assertEqual(distinct_values(pd.Series([2026.0, None, 2025.0])), [2025, 2026])

    def test_plan_column_values(self):
        result = execute(sales_df(), filter_spec(), plan="column_values")
        self.assertIsInstance(result, ColumnValuesResult)
        self.assertEqual(result.values, {"categoria": ["Electrónica", "Hogar", "Ropa"], "anio": [2025, 2026]})

    def test_tope_de_opciones(self):
        with mock.patch("sheets_reports.engine.plans.column_values.MAX_OPTIONS", 2):
            out = compiled("filter", filter_spec(columns=["mes"]))
        self.assertEqual(out["filters"][0]["options"], ["Ene", "Feb"])
        self.assertTrue(out["filters"][0]["truncated"])


class CompileTests(SimpleTestCase):
    def test_un_control_por_columna_en_su_orden(self):
        out = compiled("filter", filter_spec(columns=["anio", "categoria"]), {"labels": {"anio": "Año"}})
        self.assertEqual(out, {"filters": [
            {"field": "anio", "label": "Año", "type": "multi_select", "options": [2025, 2026]},
            {"field": "categoria", "label": "categoria", "type": "multi_select",
             "options": ["Electrónica", "Hogar", "Ropa"]},
        ]})

    def test_controles_se_reconcilian_con_las_columnas(self):
        v = view("filter", filter_spec(columns=["mes"]), {"controls": {"mes": "nada", "vieja": "multi_select"}})
        self.assertEqual(v["controls"], {"mes": "multi_select"})
        self.assertEqual(v["title"], "Filtros")


@mock.patch("sheets_reports.views.get_sheet_dataframe", side_effect=lambda *a, **k: sales_df())
class ApiTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_superuser("admin", "a@a.com", "x")
        self.client.force_login(user)
        self.dashboard = Dashboard.objects.create(nombre="D", owner=user,
                                                  sheet_url="https://docs.google.com/spreadsheets/d/abc/edit")

    def post(self, payload):
        return self.client.post(f"/api/dashboard/{self.dashboard.id}/widgets/", json.dumps(payload),
                                content_type="application/json")

    def test_una_sola_caja_por_tablero(self, _df):
        self.assertEqual(self.post({"type": "filter", "columns": ["categoria"]}).status_code, 201)
        r = self.post({"type": "filter", "columns": ["anio"]})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.json()["error"], "Solo se puede agregar un widget «Filtros» por tablero.")
        self.assertEqual(Widget.objects.filter(type="filter").count(), 1)

    def test_la_seleccion_filtra_los_widgets_pero_no_la_caja(self, _df):
        self.post({"type": "filter", "columns": ["categoria"]})
        self.post({"type": "bar", "dimensions": ["categoria"], "metrics": [agg("total_ventas")]})
        selection = [{"field": "categoria", "op": "in", "value": ["Hogar", "Ropa"]}]
        r = self.client.get(f"/api/dashboard/{self.dashboard.id}/render/?filters={quote(json.dumps(selection))}")
        widgets = {w["type"]: w["data"] for w in r.json()["widgets"]}
        self.assertEqual(widgets["bar"]["categories"], ["Hogar", "Ropa"])
        self.assertEqual(widgets["filter"]["filters"][0]["options"], ["Electrónica", "Hogar", "Ropa"])

    def test_seleccion_con_muchos_valores(self, _df):
        # Un selector múltiple puede mandar más valores que el tope de un `in` del builder (200).
        selection = [{"field": "categoria", "op": "in", "value": ["Hogar", *[f"x{i}" for i in range(300)]]}]
        r = self.client.get(f"/api/dashboard/{self.dashboard.id}/render/?filters={quote(json.dumps(selection))}")
        self.assertEqual(r.json()["filter_errors"], [])


class AiTests(SimpleTestCase):
    def test_la_ia_no_propone_cajas_de_filtro(self):
        params = build_tool_parameters(sales_ctx(), widget_type=None)
        self.assertNotIn("filter", params["properties"]["widget_type"]["enum"])
