"""Gráfico de líneas: lo que `compile` le devuelve al frontend."""
from django.test import SimpleTestCase

from sheets_reports.tests.fixtures import agg, compiled, fields


class LineCompileTests(SimpleTestCase):
    def test_no_lleva_stacked(self):
        out = compiled("line", fields(dimensions=["mes"], metrics=[agg("total_ventas")]))
        self.assertNotIn("stacked", out)
        self.assertNotIn("horizontal", out)
        self.assertEqual(out["categories"], ["Ene", "Feb", "Mar"])
        self.assertEqual(out["series"][0]["data"], [400.0, 330.0, 25.0])

    def test_mantiene_el_orden_de_la_hoja(self):
        out = compiled("line", fields(dimensions=["mes"], metrics=[agg("total_ventas")]))
        self.assertEqual(out["percent"], [])
