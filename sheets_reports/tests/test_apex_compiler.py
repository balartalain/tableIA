from django.test import SimpleTestCase

from sheets_reports.services.apex_compiler import compile_view
from sheets_reports.services.query_engine import run_data_spec
from sheets_reports.services.spec_validation import build_view_spec
from sheets_reports.tests.fixtures import sales_df, spec


def compiled(widget_type, data_spec, options=None):
    view_spec = build_view_spec(widget_type, data_spec, options)
    return compile_view(widget_type, run_data_spec(sales_df(), data_spec), view_spec)


class CompileViewTests(SimpleTestCase):
    def test_kpi(self):
        out = compiled("kpi", spec(dimensions=[]), {"labels": {"total_ventas": "Total"}})
        self.assertEqual(out, {"value": 755.0, "label": "Total"})

    def test_bar_sin_pivote_una_serie_por_metrica(self):
        out = compiled("bar", spec(metrics=[
            {"field": "ventas", "agg": "sum", "as": "total_ventas"},
            {"field": "categoria", "agg": "count", "as": "cantidad"},
        ]), {"labels": {"cantidad": "Cantidad"}})
        self.assertEqual(out["categories"], ["Hogar", "Electrónica", "Ropa"])
        self.assertEqual(out["series"], [
            {"name": "Total ventas", "data": [175.0, 500.0, 80.0]},
            {"name": "Cantidad", "data": [3, 2, 1]},
        ])
        self.assertFalse(out["stacked"])

    def test_bar_con_pivote_una_serie_por_valor(self):
        out = compiled("bar", spec(pivot="mes"), {"stacked": True})
        self.assertEqual(out["categories"], ["Hogar", "Electrónica", "Ropa"])
        self.assertEqual(out["series"], [
            {"name": "Ene", "data": [100.0, 300.0, 0]},
            {"name": "Feb", "data": [50.0, 200.0, 80.0]},
            {"name": "Mar", "data": [25.0, 0, 0]},
        ])
        self.assertTrue(out["stacked"])

    def test_line_no_lleva_stacked(self):
        self.assertNotIn("stacked", compiled("line", spec(dimensions=["mes"])))

    def test_donut(self):
        out = compiled("donut", spec(sort={"by": "total_ventas", "dir": "desc"}))
        self.assertEqual(out, {"series": [500.0, 175.0, 80.0], "labels": ["Electrónica", "Hogar", "Ropa"]})

    def test_table_plana(self):
        out = compiled("table", spec())
        self.assertEqual(out["columns"], [
            {"header": "Categoria", "field": "categoria"},
            {"header": "Total ventas", "field": "total_ventas"},
        ])
        self.assertEqual(out["rows"][0], {"categoria": "Hogar", "total_ventas": 175.0})

    def test_table_con_pivote_columnas_anidadas(self):
        out = compiled("table", spec(pivot="mes"), {"labels": {"total_ventas": "Ventas"}})
        self.assertEqual(out["columns"], [
            {"header": "Categoria", "field": "categoria"},
            {"header": "Ventas", "children": [
                {"header": "Ene", "field": "__pivots.Ene.total_ventas"},
                {"header": "Feb", "field": "__pivots.Feb.total_ventas"},
                {"header": "Mar", "field": "__pivots.Mar.total_ventas"},
            ]},
        ])
        self.assertEqual(out["rows"][0], {
            "categoria": "Hogar",
            "__pivots.Ene.total_ventas": 100.0,
            "__pivots.Feb.total_ventas": 50.0,
            "__pivots.Mar.total_ventas": 25.0,
        })
