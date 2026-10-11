"""V de Cramér: cálculo de asociación entre variables categóricas, casos límite y validación."""
import pandas as pd
from django.test import SimpleTestCase

from sheets_reports.tests.fixtures import compiled, errors_for, table_fields


def cramer(df, columns=("categoria", "region"), style=None, **overrides):
    return compiled("cramer", table_fields(columns=columns, **overrides), style, df=df)


class CramerTests(SimpleTestCase):
    def test_asociacion_perfecta(self):
        df = pd.DataFrame({
            "a": ["A", "B", "C", "A", "B", "C"],
            "b": ["X", "Y", "Z", "X", "Y", "Z"],
        })
        out = cramer(df, ("a", "b"))
        self.assertEqual(out["matrix"][0][1], 1.0)
        self.assertEqual(out["matrix"][0][0], 1.0)
        self.assertEqual(out["rows"], 6)

    def test_asociacion_parcial(self):
        df = pd.DataFrame({
            "a": ["A", "A", "B", "B", "C", "C", "A", "B"],
            "b": ["X", "Y", "X", "Y", "Z", "Z", "X", "Y"],
        })
        out = cramer(df, ("a", "b"))
        val = out["matrix"][0][1]
        self.assertIsNotNone(val)
        self.assertGreaterEqual(val, 0.0)
        self.assertLessEqual(val, 1.0)

    def test_filtro_aplicado_antes(self):
        df = pd.DataFrame({
            "g": ["1", "1", "1", "2", "2", "2"],
            "a": ["A", "B", "C", "A", "A", "A"],
            "b": ["X", "Y", "Z", "X", "Y", "Z"],
        })
        out = cramer(df, ("a", "b"), filters=[{"field": "g", "op": "eq", "value": "1"}])
        self.assertEqual(out["matrix"][0][1], 1.0)
        self.assertEqual(out["rows"], 3)

    def test_constante_o_sin_datos_da_none(self):
        df = pd.DataFrame({
            "a": ["A", "B", "C", "D"],
            "b": ["X", None, None, "Y"],
            "c": ["M", "M", "M", "M"],
        })
        out = cramer(df, ("a", "b", "c"))
        self.assertIsNone(out["matrix"][0][1])  # < MIN_ROWS
        self.assertIsNone(out["matrix"][0][2])  # c constante
        self.assertIsNone(out["matrix"][2][2])  # autorrelación constante

    def test_lo_mas_asociado(self):
        df = pd.DataFrame({
            "a": ["A", "B", "C", "A", "B", "C"],
            "b": ["X", "Y", "Z", "X", "Y", "Z"],
            "c": ["M", "M", "N", "N", "M", "N"],
        })
        out = cramer(df, ("a", "b", "c"))
        self.assertEqual(out["top"][0]["a"], "A")
        self.assertEqual(out["top"][0]["b"], "B")
        self.assertEqual(out["top"][0]["r"], 1.0)


class CramerValidationTests(SimpleTestCase):
    def test_al_menos_dos_variables(self):
        self.assertTrue(errors_for("cramer", table_fields(columns=("categoria",))))
        self.assertEqual(errors_for("cramer", table_fields(columns=("categoria", "mes"))), [])
