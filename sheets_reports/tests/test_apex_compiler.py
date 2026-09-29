from django.test import SimpleTestCase

from sheets_reports.services.apex_compiler import compile_view
from sheets_reports.services.query_engine import run_data_spec
from sheets_reports.services.spec_validation import build_view_spec
from sheets_reports.tests.fixtures import sales_df, spec


def compiled(widget_type, data_spec, options=None):
    view_spec = build_view_spec(widget_type, data_spec, options)
    layout = "table" if widget_type == "table" else "auto"
    return compile_view(widget_type, run_data_spec(sales_df(), data_spec, layout=layout), view_spec)


class CompileViewTests(SimpleTestCase):
    def test_kpi(self):
        out = compiled("kpi", spec(dimensions=[]), {"labels": {"total_ventas": "Total"}})
        self.assertEqual(out, {"value": 755.0, "label": "Total"})

    def test_kpi_porcentaje(self):
        out = compiled("kpi", spec(dimensions=[], metrics=[{"field": "ventas", "agg": "pct_sum", "as": "porcentaje"}],
                                   filters=[{"field": "anio", "op": "eq", "value": 2026}]))
        self.assertEqual(out, {"value": 89.4, "label": "Porcentaje", "percent": True})

    def test_bar_y_table_marcan_series_y_campos_de_porcentaje(self):
        metrics = [{"field": "ventas", "agg": "sum", "as": "total_ventas"},
                   {"field": "ventas", "agg": "pct_sum", "as": "porcentaje"}]
        self.assertEqual(compiled("bar", spec(metrics=metrics))["percent"], ["Porcentaje"])
        self.assertEqual(compiled("table", spec(metrics=metrics))["percent"], ["porcentaje"])
        pivoted = compiled("table", spec(pivot="mes", metrics=[metrics[1]]))
        # Una por mes + la columna "Total general".
        self.assertEqual(len(pivoted["percent"]), 4)

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
            {"header": "Total general", "field": "__total.total_ventas", "total": True},
        ])
        self.assertEqual(out["rows"][0], {
            "categoria": "Hogar",
            "__pivots.Ene.total_ventas": 100.0,
            "__pivots.Feb.total_ventas": 50.0,
            "__pivots.Mar.total_ventas": 25.0,
            "__total.total_ventas": 175.0,
        })
        self.assertEqual(out["totals"], {
            "categoria": "Total general",
            "__pivots.Ene.total_ventas": 400.0,
            "__pivots.Feb.total_ventas": 330.0,
            "__pivots.Mar.total_ventas": 25.0,
            "__total.total_ventas": 755.0,
        })

    def test_table_con_pivote_y_varias_metricas_agrupa_por_valor_del_pivote(self):
        out = compiled("table", spec(pivot="mes", metrics=[
            {"field": "categoria", "agg": "count", "as": "cantidad"},
            {"field": "categoria", "agg": "count", "as": "pct", "show_as": "pct_row"},
        ]), {"labels": {"pct": "%"}})
        self.assertEqual(out["columns"][1], {"header": "Ene", "children": [
            {"header": "Cantidad", "field": "__pivots.Ene.cantidad"},
            {"header": "%", "field": "__pivots.Ene.pct"},
        ]})
        self.assertEqual(out["columns"][-1], {"header": "Total general", "total": True, "children": [
            {"header": "Cantidad", "field": "__total.cantidad"},
            {"header": "%", "field": "__total.pct"},
        ]})
        self.assertEqual(out["rows"][0]["__pivots.Ene.pct"], 33.33)
        self.assertIn("__total.pct", out["percent"])
        self.assertNotIn("__total.cantidad", out["percent"])

    def test_table_plana_trae_fila_de_totales(self):
        out = compiled("table", spec())
        self.assertEqual(out["totals"], {"categoria": "Total general", "total_ventas": 755.0})


class PivotTableCompileTests(SimpleTestCase):
    def test_varias_filas_marcan_subtotales(self):
        out = compiled("table", spec(dimensions=["anio", "categoria"]))
        self.assertEqual([c["field"] for c in out["columns"]], ["anio", "categoria", "total_ventas"])
        self.assertEqual(out["rowFields"], ["anio", "categoria"])
        self.assertEqual(out["rows"][2], {"anio": "Total 2026", "categoria": None, "__subtotal": True, "total_ventas": 675.0})
        self.assertEqual(out["totals"], {"anio": "Total general", "total_ventas": 755.0})

    def test_dos_pivotes_anidan_columnas_con_subtotales(self):
        out = compiled("table", spec(pivot=["anio", "mes"]))
        sep = "\x1f"
        self.assertEqual(out["columns"][1], {"header": "Total ventas", "children": [
            {"header": "2026", "children": [
                {"header": "Ene", "field": f"__pivots.2026{sep}Ene.total_ventas"},
                {"header": "Feb", "field": f"__pivots.2026{sep}Feb.total_ventas"},
                {"header": "Mar", "field": f"__pivots.2026{sep}Mar.total_ventas"},
            ]},
            {"header": "Total 2026", "field": "__pivots.2026.total_ventas", "subtotal": True},
            {"header": "2025", "children": [
                {"header": "Feb", "field": f"__pivots.2025{sep}Feb.total_ventas"},
            ]},
            {"header": "Total 2025", "field": "__pivots.2025.total_ventas", "subtotal": True},
        ]})
        self.assertEqual(out["rows"][0]["__pivots.2026.total_ventas"], 175.0)

    def test_dos_pivotes_y_varias_metricas(self):
        out = compiled("table", spec(pivot=["anio", "mes"], metrics=[
            {"field": "categoria", "agg": "count", "as": "cantidad"},
            {"field": "categoria", "agg": "count", "as": "pct", "show_as": "pct_row"},
        ]))
        anio_2026 = out["columns"][1]
        self.assertEqual(anio_2026["header"], "2026")
        self.assertEqual([c["header"] for c in anio_2026["children"][0]["children"]], ["Cantidad", "Pct"])
        self.assertEqual(out["columns"][2]["header"], "Total 2026")
        self.assertTrue(out["columns"][2]["subtotal"])
