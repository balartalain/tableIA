"""Matriz de correlación: el cálculo por par, el método, las celdas sin datos y el resumen."""
import pandas as pd
from django.test import SimpleTestCase

from sheets_reports.services.ai_spec import form_errors
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
        self.assertEqual(out["variables"][0], {"field": "a", "label": "Ventas", "scale": "discrete"})


class ScalesTests(SimpleTestCase):
    """Variables numéricas u ordinales: Spearman en cuanto una es ordinal."""

    def test_la_medida_depende_de_las_escalas(self):
        df = pd.DataFrame({
            "monto": [10.5, 20.1, 30.7, 40.2, 50.9, 60.3],
            "costo": [5.5, 9.1, 16.2, 19.9, 26.4, 29.8],
            "nivel": ["Bajo", "Medio", "Alto", "Bajo", "Medio", "Alto"],
        })
        out = correlation(df, ("monto", "costo", "nivel"))
        self.assertEqual([v["scale"] for v in out["variables"]], ["continuous", "continuous", "ordinal"])
        self.assertEqual(out["measures"][0][1], "pearson")
        self.assertEqual(out["measures"][0][2], "spearman")
        self.assertEqual(out["measures"][2][2], "spearman")

    def test_una_nominal_es_una_fila_por_categoria(self):
        df = pd.DataFrame({"sede": ["N", "S", "E", "N", "S", "E", "N"],
                           "monto": [9.5, 1.5, 4.5, 8.5, 2.5, 5.5, 9.0]})
        out = correlation(df, ({"field": "sede", "label": "Sede"}, "monto"))
        self.assertEqual([v["label"] for v in out["variables"]], ["Sede: N", "Sede: S", "Sede: E", "Monto"])
        self.assertEqual([v.get("group") for v in out["variables"]], ["sede", "sede", "sede", None])
        # Entre categorías de la misma variable no hay medida.
        self.assertIsNone(out["matrix"][0][1])
        self.assertEqual(out["measures"][0][1], "same_variable")
        self.assertEqual(out["matrix"][0][0], 1.0)
        # Con un número: Spearman con signo (los de N tienen montos altos, los de S bajos).
        self.assertEqual(out["measures"][0][3], "spearman")
        self.assertGreater(out["matrix"][0][3], 0.5)
        self.assertLess(out["matrix"][1][3], -0.5)
        self.assertTrue(all(t["a"].split(":")[0] != t["b"].split(":")[0] for t in out["top"]))

    def test_una_dicotomica_es_una_sola_fila_la_del_si(self):
        df = pd.DataFrame({"becado": ["No", "Sí", "No", "Sí", "No aplica", None],
                           "nota": [60.5, 90.5, 65.5, 95.5, 99.5, 10.5]})
        out = correlation(df, ("becado", "nota"))
        self.assertEqual([v["label"] for v in out["variables"]], ["Becado: Sí", "Nota"])
        # Separa perfecto, pero los empates del 0/1 dejan a Spearman por debajo de 1.
        self.assertGreater(out["matrix"][0][1], 0.85)
        # «No aplica» y el vacío no cuentan.
        self.assertEqual(out["n"][0][1], 4)

    def test_la_ordinal_se_ordena_por_su_escala(self):
        # Satisfacción sube con el monto: Spearman con el orden de la escala, no el alfabético.
        df = pd.DataFrame({
            "satisfaccion": ["Insatisfecho", "Neutral", "Satisfecho", "Muy satisfecho"],
            "monto": [1.5, 2.5, 3.5, 4.5],
        })
        out = correlation(df, ("satisfaccion", "monto"))
        self.assertEqual(out["measures"][0][1], "spearman")
        self.assertEqual(out["matrix"][0][1], 1.0)

    def test_la_escala_guardada_en_la_fuente_manda(self):
        df = pd.DataFrame({"talla": ["S", "M", "L", "S", "M", "L"], "peso": [50.5, 70.5, 90.5, 55.5, 72.5, 95.5]})
        df.attrs["column_scales"] = {"talla": {"scale": "ordinal", "order": ["S", "M", "L"]}}
        out = correlation(df, ("talla", "peso"))
        self.assertEqual(out["variables"][0]["scale"], "ordinal")
        self.assertEqual(out["measures"][0][1], "spearman")
        self.assertGreater(out["matrix"][0][1], 0.9)

    def test_valores_fuera_del_orden_cuentan_como_vacios(self):
        df = pd.DataFrame({"talla": ["S", "M", "L", "XL"], "peso": [50.5, 70.5, 90.5, 99.5]})
        df.attrs["column_scales"] = {"talla": {"scale": "ordinal", "order": ["S", "M", "L"]}}
        out = correlation(df, ("talla", "peso"))
        self.assertEqual(out["variables"][0]["unmatched"], 1)
        self.assertEqual(out["n"][0][1], 3)


class CorrelationValidationTests(SimpleTestCase):
    def test_admite_numeros_y_ordinales(self):
        self.assertEqual(errors_for("correlation", table_fields(columns=("ventas", "anio"))), [])
        self.assertEqual(errors_for("correlation", table_fields(columns=("mes", "ventas"))), [])

    def test_admite_nominales_con_pocas_categorias(self):
        self.assertEqual(errors_for("correlation", table_fields(columns=("categoria", "ventas"))), [])

    def test_tope_de_categorias_y_de_filas(self):
        from sheets_reports.engine.context import SheetContext
        df = pd.DataFrame({"muchas": [f"c{i}" for i in range(11)] * 3,
                           "pocas": [f"p{i % 8}" for i in range(33)],
                           "monto": [float(i) for i in range(33)]})
        ctx = SheetContext.from_dataframe(df, "0")
        errors = errors_for("correlation", table_fields(columns=("muchas", "monto")), ctx=ctx)
        self.assertTrue(any("tiene 11 categorías" in e for e in errors), errors)
        df = pd.DataFrame({f"v{k}": [f"c{i % 8}" for i in range(40)] for k in range(4)})
        ctx = SheetContext.from_dataframe(df, "0")
        errors = errors_for("correlation", table_fields(columns=("v0", "v1", "v2", "v3")), ctx=ctx)
        self.assertTrue(any("tendría 32 filas" in e for e in errors), errors)

    def test_no_admite_columnas_que_no_se_analizan(self):
        errors = errors_for("correlation", table_fields(columns=("ID", "ventas")))
        self.assertTrue(any("no se puede relacionar" in e for e in errors), errors)

    def test_la_ia_corrige_la_escala_con_su_orden(self):
        from sheets_reports.engine.context import SheetContext
        df = pd.DataFrame({"talla": ["S", "M", "L", "S"], "peso": [1.5, 2.5, 3.5, 4.5]})
        ctx = SheetContext.from_dataframe(df, "0")
        cols = table_fields(columns=("talla", "peso"))
        ok = form_errors({"widget_type": "correlation", "title": "x", "fields": cols, "style": {},
                          "column_scales": [{"field": "talla", "scale": "ordinal", "order": ["S", "M", "L"]}]},
                         ctx, "correlation")
        self.assertEqual(ok, [])
        missing = form_errors({"widget_type": "correlation", "title": "x", "fields": cols, "style": {},
                               "column_scales": [{"field": "talla", "scale": "ordinal", "order": ["S", "M"]}]},
                              ctx, "correlation")
        self.assertTrue(any("no incluye: L" in e for e in missing), missing)
        no_order = form_errors({"widget_type": "correlation", "title": "x", "fields": cols, "style": {},
                                "column_scales": [{"field": "talla", "scale": "ordinal"}]},
                               ctx, "correlation")
        self.assertTrue(any("necesita `order`" in e for e in no_order), no_order)

    def test_otro_widget_no_admite_escalas(self):
        errors = errors_for("table", table_fields(columns=("categoria",)))
        self.assertEqual(errors, [])
        from sheets_reports.tests.fixtures import sales_ctx
        errors = form_errors({"widget_type": "table", "title": "x", "fields": table_fields(columns=("categoria",)),
                              "style": {}, "column_scales": [{"field": "categoria", "scale": "nominal"}]},
                             sales_ctx(), "table")
        self.assertTrue(any("no usa la escala" in e for e in errors), errors)

    def test_al_menos_dos_variables(self):
        self.assertTrue(errors_for("correlation", table_fields(columns=("ventas",))))
