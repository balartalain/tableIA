"""Matriz de correlación: el cálculo por par, el método, las celdas sin datos y el resumen."""
import pandas as pd
from django.test import SimpleTestCase

from sheets_reports.tests.fixtures import compiled, errors_for, table_fields


def correlation(df, columns=("a", "b"), style=None, **overrides):
    return compiled("correlation", table_fields(columns=columns, **overrides), style, df=df)


class CorrelationTests(SimpleTestCase):
    def test_positiva_y_negativa_perfectas(self):
        df = pd.DataFrame({"a": [1, 2, 3, 4], "b": [2, 4, 6, 8], "c": [8, 6, 4, 2]})
        out = correlation(df, ("a", "b", "c"))
        self.assertEqual(out["matrix"], [[1.0, 1.0, -1.0], [1.0, 1.0, -1.0], [-1.0, -1.0, 1.0]])
        self.assertEqual([v["label"] for v in out["variables"]], ["A", "B", "C"])
        self.assertEqual(out["rows"], 4)

    def test_spearman_ve_la_relacion_monotona(self):
        df = pd.DataFrame({"a": [1, 2, 3, 4, 5], "b": [1, 8, 27, 64, 1000]})
        pearson = correlation(df)["matrix"][0][1]
        spearman = correlation(df, style={"method": "spearman"})
        self.assertLess(pearson, 0.9)
        self.assertEqual(spearman["matrix"][0][1], 1.0)
        self.assertEqual(spearman["method"], "spearman")

    def test_el_filtro_se_aplica_antes(self):
        df = pd.DataFrame({"g": ["x", "x", "x", "y", "y", "y"],
                           "a": [1, 2, 3, 1, 2, 3], "b": [1, 2, 3, 3, 2, 1]})
        out = correlation(df, filters=[{"field": "g", "op": "eq", "value": "y"}])
        self.assertEqual(out["matrix"][0][1], -1.0)
        self.assertEqual(out["rows"], 3)

    def test_cada_par_usa_sus_filas_completas(self):
        df = pd.DataFrame({"a": [1, 2, 3, 4, 5], "b": [2, 4, 6, 8, 10], "c": [1, None, 3, None, 5]})
        out = correlation(df, ("a", "b", "c"))
        self.assertEqual(out["n"][0][1], 5)
        self.assertEqual(out["n"][0][2], 3)
        self.assertEqual(out["matrix"][0][2], 1.0)

    def test_sin_datos_suficientes_o_constante_es_vacio(self):
        df = pd.DataFrame({"a": [1, 2, 3, 4], "b": [1, None, None, 2], "c": [5, 5, 5, 5]})
        out = correlation(df, ("a", "b", "c"))
        self.assertIsNone(out["matrix"][0][1])
        self.assertIsNone(out["matrix"][0][2])
        self.assertIsNone(out["matrix"][2][2])
        self.assertEqual(out["top"], [])

    def test_lo_mas_relacionado_por_fuerza_sin_diagonal(self):
        df = pd.DataFrame({"a": [1, 2, 3, 4, 5], "b": [5, 4, 3, 2, 1], "c": [1, 3, 2, 5, 4]})
        out = correlation(df, ("a", "b", "c"))
        self.assertEqual([(t["a"], t["b"], t["r"]) for t in out["top"]][0], ("A", "B", -1.0))
        self.assertEqual(len(out["top"]), 3)
        self.assertTrue(all(t["a"] != t["b"] for t in out["top"]))
        strengths = [abs(t["r"]) for t in out["top"]]
        self.assertEqual(strengths, sorted(strengths, reverse=True))

    def test_nombre_a_mostrar(self):
        df = pd.DataFrame({"a": [1, 2, 3], "b": [3, 1, 2]})
        out = correlation(df, ({"field": "a", "label": "Ventas"}, "b"))
        self.assertEqual(out["variables"][0], {"field": "a", "label": "Ventas"})


class CorrelationValidationTests(SimpleTestCase):
    def test_solo_columnas_numericas(self):
        errors = errors_for("correlation", table_fields(columns=("categoria", "ventas")))
        self.assertTrue(any("no es numérica" in e for e in errors), errors)
        self.assertEqual(errors_for("correlation", table_fields(columns=("ventas", "anio"))), [])

    def test_al_menos_dos_variables(self):
        self.assertTrue(errors_for("correlation", table_fields(columns=("ventas",))))
