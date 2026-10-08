"""Campos calculados de la fuente: el lenguaje de fórmulas, los campos por fila (una columna
más) y los agregados (una métrica que se evalúa por grupo en todo el motor)."""
import pandas as pd
from django.test import SimpleTestCase

from sheets_reports.engine.context import SheetContext
from sheets_reports.engine.formulas import (
    FormulaError,
    aggregated_fields,
    apply_calculated_fields,
    compile_formula,
    formula_tree,
    rename_columns,
)
from sheets_reports.tests.fixtures import compiled, errors_for, execute, fields


def budget_df() -> pd.DataFrame:
    """Los gastos del tablero de ejemplo: Tecnología ejecuta 15 500 de 16 000."""
    return pd.DataFrame({
        "Departamento": ["TI", "TI", "TI", "RH", "RH"],
        "Campus": ["Santiago", "Santo Domingo", "Nagua", "Nagua", "Santiago"],
        "Presupuesto_Asignado": [5000.0, 8000.0, 3000.0, 2000.0, 1500.0],
        "Gasto_Real": [4200.0, 8500.0, 2800.0, 1800.0, 1300.0],
        "Respuesta": ["Sí", "No", "Sí", "Sí", None],
    })


EXECUTION = {"id": "a", "name": "% Ejecución", "format": "percent",
             "formula": "SUM([Gasto_Real]) / SUM([Presupuesto_Asignado]) * 100"}


def with_fields(*definitions) -> pd.DataFrame:
    return apply_calculated_fields(budget_df(), list(definitions))


class FormulaLanguageTests(SimpleTestCase):
    def test_por_fila_aritmetica_y_nombres(self):
        f = compile_formula("[Gasto_Real] - presupuesto_asignado", budget_df().columns)
        self.assertFalse(f.aggregated)
        self.assertEqual(f.columns, ("Gasto_Real", "Presupuesto_Asignado"))   # sin distinguir mayúsculas
        self.assertEqual(f.evaluate_rows(budget_df()).tolist(), [-800, 500, -200, -200, -200])

    def test_if_con_texto_y_condiciones(self):
        df = budget_df()
        self.assertEqual(compile_formula('IF([Respuesta] = "Sí", 1, 0)', df.columns)
                         .evaluate_rows(df).tolist(), [1, 0, 1, 1, 0])
        status = compile_formula('IF(Gasto_Real > Presupuesto_Asignado AND NOT Departamento = "RH", '
                                 '"Excedido", "En presupuesto")', df.columns)
        self.assertEqual(status.evaluate_rows(df).tolist()[:2], ["En presupuesto", "Excedido"])

    def test_agregado_por_grupo(self):
        f = compile_formula(EXECUTION["formula"], budget_df().columns)
        self.assertTrue(f.aggregated)
        ti = budget_df()[budget_df()["Departamento"] == "TI"]
        self.assertAlmostEqual(f.aggregate(ti), 96.875)

    def test_agregaciones_sobre_expresiones(self):
        df = budget_df()
        self.assertAlmostEqual(compile_formula('AVG(IF([Respuesta] = "Sí", 1, 0)) * 100', df.columns)
                               .aggregate(df), 60.0)
        self.assertEqual(compile_formula("COUNT([Respuesta])", df.columns).aggregate(df), 4)
        self.assertEqual(compile_formula("COUNT_DISTINCT(Campus)", df.columns).aggregate(df), 3)

    def test_agregacion_evalua_por_fila_y_resume_el_grupo(self):
        """Lo que explica la ayuda: lo de adentro es el valor de cada fila, no un total."""
        df = budget_df()
        ti = df[df["Departamento"] == "TI"]

        def value(formula, rows=df):
            return compile_formula(formula, df.columns).aggregate(rows)

        self.assertAlmostEqual(value("AVG([Gasto_Real] - [Presupuesto_Asignado])", ti), -500 / 3)
        self.assertEqual(value("SUM([Gasto_Real]) - SUM([Presupuesto_Asignado])", ti), -500)
        # COUNT cuenta valores no vacíos (el 0 también): para contar los que cumplen, SUM(IF(…, 1, 0)).
        self.assertEqual(value('COUNT(IF([Respuesta] = "Sí", 1, 0))'), 5)
        self.assertEqual(value('SUM(IF([Respuesta] = "Sí", 1, 0))'), 3)
        self.assertEqual(value("COUNT([Respuesta])"), 4)   # sin la vacía
        self.assertEqual(value("COUNT(1)"), 5)             # todas las filas
        # Una comparación vale 1 o 0.
        self.assertAlmostEqual(value('AVG([Respuesta] = "Sí") * 100'), 60.0)   # la vacía cuenta como 0
        self.assertAlmostEqual(value('SUM(IF([Respuesta] = "Sí", 1, 0)) / COUNT([Respuesta]) * 100'), 75.0)
        # Las agregaciones numéricas ignoran el texto: sin números, vacío.
        self.assertIsNone(value("AVG([Respuesta])"))
        self.assertEqual(value("MAX([Gasto_Real] - [Presupuesto_Asignado])", ti), 500)

    def test_arbol_para_el_constructor(self):
        def node(kind, value=None, *args):
            return {"kind": kind, "value": value, "args": list(args)}
        self.assertEqual(
            formula_tree('IF([grado] = "a" OR grado = \'b\', "X", -1.5)'),
            node("func", "IF",
                 node("bin", "OR", node("bin", "=", node("col", "grado"), node("str", "a")),
                      node("bin", "=", node("col", "grado"), node("str", "b"))),
                 node("str", "X"), node("neg", None, node("num", 1.5))))
        self.assertEqual(formula_tree("AVG([Gasto Real])"), node("func", "AVG", node("col", "Gasto Real")))
        self.assertIsNone(formula_tree("SUM("))

    def test_division_entre_cero_queda_vacia(self):
        df = budget_df()
        self.assertTrue(compile_formula("Gasto_Real / 0", df.columns).evaluate_rows(df).isna().all())
        empty = df.iloc[0:0]
        self.assertIsNone(compile_formula(EXECUTION["formula"], df.columns).aggregate(empty))

    def test_errores_legibles(self):
        cases = {
            "SUM([Gasto_Real]) / [Presupuesto_Asignado]": "mezcla valores agregados",
            "SUM(SUM(Gasto_Real))": "dentro de otra",
            "[Gasto Real]": "no existe",
            "Gasto Real": "entre corchetes: [Gasto Real]",
            "PROMEDIO(Gasto_Real)": "no existe",
            "IF(1, 2)": "tres partes",
            "(Gasto_Real + 1": "Se esperaba «)»",
            "": "Escribe una fórmula",
        }
        for formula, message in cases.items():
            with self.subTest(formula=formula):
                with self.assertRaises(FormulaError) as ctx:
                    compile_formula(formula, budget_df().columns)
                self.assertIn(message, str(ctx.exception))

    def test_renombrar_columnas_en_el_texto(self):
        self.assertEqual(
            rename_columns("SUM([Gasto_Real]) / SUM(Presupuesto_Asignado) * 100",
                           {"Gasto_Real": "Gasto", "Presupuesto_Asignado": "Presupuesto total"}),
            "SUM([Gasto]) / SUM([Presupuesto total]) * 100")


class ApplyCalculatedFieldsTests(SimpleTestCase):
    def test_por_fila_son_columnas_y_agregados_van_en_attrs(self):
        df = with_fields({"id": "d", "name": "Diferencia", "formula": "Gasto_Real - Presupuesto_Asignado"},
                         {"id": "p", "name": "Excedido", "formula": "IF(Diferencia > 0, 1, 0)"},
                         EXECUTION)
        self.assertEqual(df["Excedido"].tolist(), [0, 1, 0, 0, 0])   # usa el campo anterior
        self.assertEqual(list(aggregated_fields(df)), ["% Ejecución"])
        self.assertTrue(aggregated_fields(df)["% Ejecución"].percent)

    def test_un_campo_roto_se_omite_y_en_estricto_falla(self):
        broken = {"id": "x", "name": "Roto", "formula": "[Ya_No_Esta] + 1"}
        self.assertNotIn("Roto", with_fields(broken).columns)
        with self.assertRaisesMessage(FormulaError, "«Roto»"):
            apply_calculated_fields(budget_df(), [broken], strict=True)

    def test_nombre_repetido_con_una_columna(self):
        with self.assertRaisesMessage(FormulaError, "Ya hay una columna"):
            apply_calculated_fields(budget_df(), [{"id": "x", "name": "Campus", "formula": "1"}], strict=True)

    def test_un_agregado_no_entra_en_otra_formula(self):
        with self.assertRaisesMessage(FormulaError, "es un campo agregado"):
            apply_calculated_fields(budget_df(), [EXECUTION, {"id": "b", "name": "Doble",
                                                              "formula": "[% Ejecución] * 2"}], strict=True)


class AggregatedFieldInEngineTests(SimpleTestCase):
    metric = {"field": "% Ejecución", "agg": "auto", "alias": "ejec"}

    def test_por_grupo_y_en_total(self):
        df = with_fields(EXECUTION)
        out = execute(df, fields(dimensions=["Departamento"], metrics=[self.metric]))
        by_dep = {r["Departamento"]: r["ejec"] for r in out["data"]}
        self.assertAlmostEqual(by_dep["TI"], 96.875)
        self.assertAlmostEqual(by_dep["RH"], 3100 / 3500 * 100)
        self.assertEqual(out["metadata"]["percent_metrics"], ["ejec"])
        kpi = execute(df, fields(dimensions=[], metrics=[self.metric]))
        self.assertAlmostEqual(kpi["data"]["values"]["ejec"], 18600 / 19500 * 100)

    def test_respeta_las_condiciones_de_la_metrica(self):
        metric = {**self.metric, "filters": [{"field": "Campus", "op": "eq", "value": "Nagua"}]}
        out = execute(with_fields(EXECUTION), fields(dimensions=["Departamento"], metrics=[metric]))
        by_dep = {r["Departamento"]: r["ejec"] for r in out["data"]}
        self.assertAlmostEqual(by_dep["TI"], 2800 / 3000 * 100)

    def test_grafico_con_pivote_y_tabla_dinamica_con_totales(self):
        df = with_fields(EXECUTION)
        bar = compiled("bar", fields(dimensions=["Departamento"], metrics=[self.metric]), df=df)
        self.assertEqual(bar["series"][0]["name"], "% Ejecución")   # el nombre del campo
        self.assertEqual(bar["percent"], ["% Ejecución"])
        table = compiled("dynamic_table", fields(dimensions=["Departamento"], pivots=["Campus"],
                                                 metrics=[self.metric]), df=df)
        ti = next(r for r in table["rows"] if r["Departamento"] == "TI")
        # El total de la fila es el cociente de los totales, no una suma de porcentajes.
        self.assertAlmostEqual(ti["__total.ejec"], 96.875)
        self.assertIn("__total.ejec", table["percent"])

    def test_kpi_con_formato_porcentaje(self):
        out = compiled("kpi", fields(dimensions=[], metrics=[self.metric]), df=with_fields(EXECUTION))
        self.assertTrue(out["formatted_value"].endswith(" %"))


class AggregatedFieldValidationTests(SimpleTestCase):
    def ctx(self):
        return SheetContext.from_dataframe(with_fields(EXECUTION), "0")

    def test_solo_como_metrica_con_agg_auto(self):
        ok = fields(dimensions=["Departamento"], metrics=[{"field": "% Ejecución", "agg": "auto", "alias": "e"}])
        self.assertEqual(errors_for("bar", ok, ctx=self.ctx()), [])
        wrong_agg = fields(dimensions=["Departamento"],
                           metrics=[{"field": "% Ejecución", "agg": "sum", "alias": "e"}])
        self.assertIn("usa agg 'auto'", errors_for("bar", wrong_agg, ctx=self.ctx())[0])
        auto_on_column = fields(dimensions=["Departamento"],
                                metrics=[{"field": "Gasto_Real", "agg": "auto", "alias": "e"}])
        self.assertIn("solo se usa con un campo calculado agregado",
                      errors_for("bar", auto_on_column, ctx=self.ctx())[0])
        as_dimension = fields(dimensions=["% Ejecución"],
                              metrics=[{"field": "Gasto_Real", "agg": "sum", "alias": "g"}])
        self.assertTrue(errors_for("bar", as_dimension, ctx=self.ctx()))
