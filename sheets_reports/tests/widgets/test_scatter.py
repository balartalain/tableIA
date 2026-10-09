"""Gráfico de dispersión: un punto por fila con los dos valores, los grupos por color, la
muestra y las tendencias."""
import pandas as pd
from django.test import SimpleTestCase

from sheets_reports.tests.fixtures import compiled, errors_for, table_fields
from sheets_reports.widgets import scatter as scatter_module


def scatter(df, columns=("a", "b"), style=None, **overrides):
    return compiled("scatter", table_fields(columns=columns, **overrides), style, df=df)


def points(out):
    return [p for g in out["groups"] for p in g["points"]]


class ScatterTests(SimpleTestCase):
    def test_un_punto_por_fila_en_orden_x_y(self):
        df = pd.DataFrame({"a": [1, 2, 3], "b": [10, 20, 30]})
        out = scatter(df)
        self.assertEqual(out["groups"], [{"name": "", "points": [[1.0, 10.0], [2.0, 20.0], [3.0, 30.0]],
                                          "rows": 3, "trend": out["trend"]}])
        self.assertIsNone(out["color"])
        self.assertEqual((out["x"]["label"], out["y"]["label"]), ("A", "B"))
        self.assertEqual((out["rows"], out["shown"]), (3, 3))

    def test_las_filas_sin_alguno_de_los_valores_no_cuentan(self):
        df = pd.DataFrame({"a": [1, None, 3, 4], "b": [1, 2, None, 4]})
        out = scatter(df)
        self.assertEqual(points(out), [[1.0, 1.0], [4.0, 4.0]])
        self.assertEqual(out["rows"], 2)

    def test_tendencia_recta_exacta(self):
        df = pd.DataFrame({"a": [1, 2, 3, 4], "b": [3, 5, 7, 9]})
        trend = scatter(df)["trend"]
        self.assertAlmostEqual(trend["slope"], 2.0)
        self.assertAlmostEqual(trend["intercept"], 1.0)
        self.assertEqual(trend["r"], 1.0)
        self.assertEqual((trend["from"], trend["to"]), (1.0, 4.0))

    def test_sin_tendencia_si_x_no_varia(self):
        df = pd.DataFrame({"a": [2, 2, 2], "b": [1, 2, 3]})
        self.assertIsNone(scatter(df)["trend"])

    def test_y_constante_tiene_recta_pero_no_r(self):
        df = pd.DataFrame({"a": [1, 2, 3], "b": [5, 5, 5]})
        trend = scatter(df)["trend"]
        self.assertAlmostEqual(trend["slope"], 0.0)
        self.assertIsNone(trend["r"])

    def test_muestra_fija_con_muchas_filas_y_tendencia_con_todas(self):
        size = scatter_module.MAX_POINTS + 500
        df = pd.DataFrame({"a": range(size), "b": [2 * v for v in range(size)]})
        first, second = scatter(df), scatter(df)
        self.assertEqual(first["shown"], scatter_module.MAX_POINTS)
        self.assertEqual(len(points(first)), scatter_module.MAX_POINTS)
        self.assertEqual(first["rows"], size)
        self.assertEqual(points(first), points(second))
        self.assertEqual((first["trend"]["from"], first["trend"]["to"]), (0.0, float(size - 1)))

    def test_el_filtro_se_aplica_antes(self):
        df = pd.DataFrame({"g": ["x", "x", "y"], "a": [1, 2, 3], "b": [1, 2, 3]})
        out = scatter(df, filters=[{"field": "g", "op": "eq", "value": "y"}])
        self.assertEqual(points(out), [[3.0, 3.0]])

    def test_nombre_a_mostrar(self):
        df = pd.DataFrame({"a": [1, 2], "b": [3, 4]})
        out = scatter(df, ({"field": "a", "label": "Experiencia"}, "b"))
        self.assertEqual(out["x"], {"field": "a", "label": "Experiencia"})


class ScatterColorTests(SimpleTestCase):
    """`dimensions` colorea: un grupo por categoría, sin agrupar ni agregar las filas."""

    def test_un_grupo_por_categoria_por_frecuencia(self):
        df = pd.DataFrame({"tipo": ["SUV", "Sedan", "SUV", "SUV", "Sedan", "Compact"],
                           "a": [1, 2, 3, 4, 5, 6], "b": [1, 2, 3, 4, 5, 6]})
        out = scatter(df, dimensions=["tipo"])
        self.assertEqual(out["color"], {"field": "tipo", "label": "Tipo"})
        self.assertEqual([(g["name"], g["rows"]) for g in out["groups"]],
                         [("SUV", 3), ("Sedan", 2), ("Compact", 1)])
        self.assertEqual(out["groups"][0]["points"], [[1.0, 1.0], [3.0, 3.0], [4.0, 4.0]])
        self.assertEqual(out["rows"], 6)

    def test_tendencia_por_grupo_y_global(self):
        """Cada grupo baja aunque, juntos, suban: la pendiente de cada uno es la que importa."""
        df = pd.DataFrame({"g": ["p"] * 3 + ["q"] * 3,
                           "a": [1, 2, 3, 11, 12, 13], "b": [3, 2, 1, 13, 12, 11]})
        out = scatter(df, dimensions=["g"])
        self.assertEqual([g["trend"]["r"] for g in out["groups"]], [-1.0, -1.0])
        self.assertGreater(out["trend"]["slope"], 0)

    def test_las_categorias_de_mas_van_a_otros(self):
        names = [f"c{i}" for i in range(12)]
        df = pd.DataFrame({"g": names + ["c0"], "a": range(13), "b": range(13)})
        out = scatter(df, dimensions=["g"])
        groups = [g["name"] for g in out["groups"]]
        self.assertEqual(len(groups), scatter_module.MAX_GROUPS)
        self.assertEqual(groups[0], "c0")
        self.assertEqual(groups[-1], "Otros")
        self.assertEqual(sum(g["rows"] for g in out["groups"]), 13)

    def test_sin_valor_tiene_su_grupo_al_final(self):
        df = pd.DataFrame({"g": [None, "x", "x", ""], "a": [1, 2, 3, 4], "b": [1, 2, 3, 4]})
        out = scatter(df, dimensions=["g"])
        self.assertEqual([(g["name"], g["rows"]) for g in out["groups"]], [("x", 2), ("(Sin valor)", 2)])

    def test_la_muestra_conserva_cada_grupo(self):
        size = scatter_module.MAX_POINTS * 2
        df = pd.DataFrame({"g": ["x", "y"] * (size // 2), "a": range(size), "b": range(size)})
        out = scatter(df, dimensions=["g"])
        self.assertEqual(sum(len(g["points"]) for g in out["groups"]), scatter_module.MAX_POINTS)
        self.assertTrue(all(len(g["points"]) > scatter_module.MAX_POINTS * 0.4 for g in out["groups"]))
        self.assertEqual([g["rows"] for g in out["groups"]], [size // 2, size // 2])


class ScatterValidationTests(SimpleTestCase):
    def test_exactamente_dos_columnas_numericas(self):
        self.assertEqual(errors_for("scatter", table_fields(columns=("ventas", "anio"))), [])
        self.assertTrue(errors_for("scatter", table_fields(columns=("ventas",))))
        self.assertTrue(errors_for("scatter", table_fields(columns=("ventas", "anio", "ventas"))))
        errors = errors_for("scatter", table_fields(columns=("categoria", "ventas")))
        self.assertTrue(any("no es numérica" in e for e in errors), errors)

    def test_color_por_una_columna_de_categorias(self):
        self.assertEqual(errors_for("scatter", table_fields(columns=("ventas", "anio"),
                                                            dimensions=["categoria"])), [])
        self.assertTrue(errors_for("scatter", table_fields(columns=("ventas", "anio"),
                                                           dimensions=["categoria", "mes"])))

    def test_sin_orden_ni_limite(self):
        self.assertTrue(errors_for("scatter", table_fields(columns=("ventas", "anio"), sort_by="ventas")))
