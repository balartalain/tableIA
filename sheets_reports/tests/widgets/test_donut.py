"""Gráfico de dona: lo que `compile` le devuelve al frontend."""
from django.test import SimpleTestCase

from sheets_reports.tests.fixtures import compiled, fields


class DonutCompileTests(SimpleTestCase):
    def test_series_y_etiquetas(self):
        out = compiled("donut", fields())
        self.assertEqual(out["labels"], ["Hogar", "Electrónica", "Ropa"])
        self.assertEqual(out["series"], [175.0, 500.0, 80.0])
        self.assertNotIn("_apex_options", out)
