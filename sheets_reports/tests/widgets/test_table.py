"""Tabla de datos (`table`): las filas de la hoja tal cual, con las columnas elegidas."""
import json
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from sheets_reports.engine.plans import RowsResult
from sheets_reports.models import Dashboard, Widget
from sheets_reports.tests.fixtures import agg, compiled, errors_for, execute, sales_df, spec, view


def table_spec(columns=("mes", "ventas"), **overrides):
    return spec(**{"dimensions": [], "metrics": [], "columns": list(columns), **overrides})


class ValidationTests(SimpleTestCase):
    def test_spec_valido(self):
        self.assertEqual(errors_for("table", table_spec()), [])
        self.assertEqual(errors_for("table", table_spec(
            filters=[{"field": "anio", "op": "eq", "value": 2026}], sort={"by": "ventas", "dir": "desc"},
        )), [])

    def test_requiere_al_menos_una_columna(self):
        self.assertEqual(errors_for("table", table_spec(columns=[])),
                         ["columns: elige al menos una columna para mostrar."])

    def test_columna_inexistente_o_repetida(self):
        self.assertIn("'region' no existe", errors_for("table", table_spec(columns=["region"]))[0])
        self.assertIn("columns: no se puede repetir", errors_for("table", table_spec(columns=["mes", "mes"]))[0])

    def test_no_agrupa_ni_lleva_metricas(self):
        errors = errors_for("table", table_spec(dimensions=["categoria"], metrics=[agg("total")]))
        self.assertIn("dimensions: este tipo de widget no admite dimensión.", errors)
        self.assertIn("metrics: este tipo de widget no lleva métricas (muestra los datos tal cual).", errors)

    def test_orden_por_una_columna_mostrada(self):
        self.assertIn("sort.by: 'anio' no es válido",
                      errors_for("table", table_spec(sort={"by": "anio", "dir": "asc"}))[0])

    def test_los_demas_widgets_no_usan_columns(self):
        self.assertIn("no muestra columnas sueltas", errors_for("bar", spec(columns=["mes"]))[0])


class RowsPlanTests(SimpleTestCase):
    def test_filas_tal_cual_con_filtros_y_orden(self):
        result = execute(sales_df(), table_spec(
            filters=[{"field": "categoria", "op": "eq", "value": "Hogar"}], sort={"by": "ventas", "dir": "asc"},
        ), plan="rows")
        self.assertIsInstance(result, RowsResult)
        self.assertEqual(result.rows.to_dict(orient="records"), [
            {"mes": "Mar", "ventas": 25.0}, {"mes": "Feb", "ventas": 50.0}, {"mes": "Ene", "ventas": 100.0},
        ])
        self.assertFalse(result.truncated)

    def test_tope_de_filas(self):
        with mock.patch("sheets_reports.engine.plans.rows.MAX_ROWS", 2):
            result = execute(sales_df(), table_spec(), plan="rows")
        self.assertEqual(len(result.rows), 2)
        self.assertEqual(result.total_rows, 6)
        self.assertTrue(result.truncated)


class CompileTests(SimpleTestCase):
    def test_columnas_con_tipo_y_nombre_a_mostrar(self):
        out = compiled("table", table_spec(columns=["categoria", "ventas"]), {"labels": {"ventas": "Monto"}})
        self.assertEqual(out["columns"], [
            {"header": "categoria", "field": "categoria", "numeric": False},
            {"header": "Monto", "field": "ventas", "numeric": True},
        ])
        self.assertEqual(out["rows"][0], {"categoria": "Hogar", "ventas": 100.0})
        self.assertEqual(out["total_rows"], 6)
        self.assertNotIn("truncated", out)

    def test_view_spec(self):
        self.assertEqual(view("table", table_spec()),
                         {"widget": "table", "columns": ["mes", "ventas"], "percent": [], "title": "Datos",
                          "labels": {}, "display": {}})


@mock.patch("sheets_reports.views.get_sheet_dataframe", side_effect=lambda *a, **k: sales_df())
class ApiTests(TestCase):
    def test_crear_desde_el_builder(self, _df):
        user = get_user_model().objects.create_superuser("admin", "a@a.com", "x")
        self.client.force_login(user)
        dashboard = Dashboard.objects.create(nombre="D", owner=user,
                                             sheet_url="https://docs.google.com/spreadsheets/d/abc/edit")
        r = self.client.post(f"/api/dashboard/{dashboard.id}/widgets/", json.dumps({
            "type": "table", "columns": ["categoria", "ventas"], "sort": {"by": "ventas", "dir": "desc"},
        }), content_type="application/json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()["data"]["rows"][0], {"categoria": "Electrónica", "ventas": 300.0})
        self.assertEqual(Widget.objects.get().data_spec["columns"], ["categoria", "ventas"])
