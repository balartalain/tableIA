"""Contrato común de todos los widgets: `render` devuelve `render_data` + `widget_form` o un error."""
from django.test import SimpleTestCase

from sheets_reports.tests.fixtures import (agg, compiled, fields, render, sales_df, sellers_df, table_fields)


class RenderContractTests(SimpleTestCase):
    def test_todo_widget_devuelve_render_data_y_widget_form(self):
        for key, spec in [
            ("bar", fields()),
            ("line", fields(dimensions=["mes"])),
            ("donut", fields()),
            ("kpi", fields(dimensions=[], metrics=[agg("total_ventas")])),
            ("dynamic_table", fields()),
            ("table", table_fields(["categoria"])),
            ("filter", fields(dimensions=["mes"], metrics=[])),
        ]:
            with self.subTest(widget=key):
                out = render(key, spec)
                self.assertIn("render_data", out)
                self.assertIn("widget_form", out)
                self.assertNotIn("error", out)

    def test_un_error_no_tumba_el_form(self):
        out = render("bar", fields(dimensions=["categoria"],
                                   metrics=[{"field": "no_existe", "agg": "sum", "alias": "x"}]))
        self.assertIn("error", out)
        self.assertIn("widget_form", out)

    def test_los_estilos_se_completan_con_los_defaults(self):
        out = render("bar", fields(), {})
        self.assertEqual(out["widget_form"]["style"]["showGrid"], True)
        self.assertEqual(out["widget_form"]["style"]["barWidth"], 70)

    def test_el_error_de_una_hoja_sin_filas_no_lanza(self):
        out = compiled("bar", fields(), {}, df=sales_df().head(0))
        self.assertEqual(out["categories"], [])
        self.assertEqual(out["series"], [])

    def test_kpi_sobre_una_hoja_vacia(self):
        out = compiled("kpi", fields(dimensions=[], metrics=[agg("total_ventas")]),
                       {}, df=sellers_df().head(0))
        self.assertEqual(out["value"], 0)
        self.assertIsNone(out["compare"])
