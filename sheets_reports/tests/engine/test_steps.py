"""Los pasos del motor: → agregación/pivote → fórmulas → ventanas → orden/límite."""
from unittest import mock

import pandas as pd
from django.test import SimpleTestCase

from sheets_reports.engine import ResultTooLargeError
from sheets_reports.engine import AGGREGATIONS, agg_name, build_query_result, run_steps
from sheets_reports.engine.steps.aggregation import apply_aggregation
from sheets_reports.engine.steps.filter import filter_rows
from sheets_reports.tests.fixtures import agg, calc, execute, fields, sales_df, sellers_df
from sheets_reports.widgets.schemas import WidgetFields


class AggregationTests(SimpleTestCase):
    def test_agregaciones_disponibles_se_traducen_a_pandas(self):
        self.assertEqual(sorted(AGGREGATIONS), [
            "avg", "count", "count_distinct", "max", "mean", "median", "min", "std", "sum",
        ])
        self.assertEqual(agg_name("avg"), "mean")
        self.assertEqual(agg_name("count_distinct"), "nunique")
        self.assertEqual(agg_name("suma_inventada"), "suma_inventada")

    def test_dos_metricas_sobre_el_mismo_campo_no_colisionan(self):
        out = execute(sales_df(), fields(metrics=[
            agg("total", "sum"), agg("promedio", "avg"), agg("maximo", "max"),
        ]))
        row = next(r for r in out["data"] if r["categoria"] == "Hogar")
        self.assertEqual((row["total"], row["promedio"], row["maximo"]), (175.0, 58.333333333333336, 100.0))

    def test_count_sin_campo_cuenta_filas(self):
        out = execute(sales_df(), fields(dimensions=[], metrics=[{"agg": "count", "alias": "filas"}]))
        self.assertEqual(out["type"], "scalar")
        self.assertEqual(out["data"], {"values": {"filas": 6}})

    def test_count_distinct_sobre_columna_de_texto(self):
        out = execute(sales_df(), fields(dimensions=[], metrics=[
            {"agg": "count_distinct", "field": "categoria", "alias": "categorias"},
        ]))
        self.assertEqual(out["data"]["values"]["categorias"], 3)

    def test_orden_de_la_primera_dimension_respeta_la_hoja(self):
        out = execute(sales_df(), fields(metrics=[agg("total")]))
        self.assertEqual(out["data"][0]["categoria"], "Hogar")   # aparece primero en la hoja
        self.assertEqual([r["categoria"] for r in out["data"]],
                         ["Hogar", "Electrónica", "Ropa"])


class FilterStepTests(SimpleTestCase):
    def test_los_filtros_del_widget_recortan_las_filas(self):
        out = execute(sales_df(), fields(
            filters=[{"field": "anio", "op": "eq", "value": 2026}],
            metrics=[agg("total")],
        ))
        self.assertEqual(out["data"][0]["total"], 175.0)

    def test_se_filtran_antes_de_agrupar(self):
        # Si el filtro corriera después, Hogar incluiría la fila de 2025.
        out = execute(sales_df(), fields(
            filters=[{"field": "anio", "op": "eq", "value": 2025}],
            metrics=[agg("total")],
        ))
        self.assertEqual(out["data"], [{"categoria": "Ropa", "total": 80.0}])

    def test_filtros_propios_de_una_metrica(self):
        out = execute(sales_df(), fields(dimensions=[], metrics=[
            agg("h2026", filters=[{"field": "anio", "op": "eq", "value": 2026}]),
            agg("h2025", filters=[{"field": "anio", "op": "eq", "value": 2025}]),
        ]))
        self.assertEqual(out["data"]["values"], {"h2026": 675.0, "h2025": 80.0})


class PivotTests(SimpleTestCase):
    def test_una_columna_por_valor_del_pivote(self):
        out = execute(sales_df(), fields(pivots=["mes"], metrics=[agg("total")]))
        self.assertEqual(out["type"], "pivot_chart")
        self.assertEqual([r["categoria"] for r in out["data"]], ["Hogar", "Electrónica", "Ropa"])
        self.assertEqual(out["data"][0]["Ene_total"], 100.0)
        self.assertEqual(out["data"][0]["Mar_total"], 25.0)
        self.assertEqual(out["metadata"]["pivot_values"], ["Ene", "Feb", "Mar"])

    def test_varias_metricas_en_el_pivote(self):
        out = execute(sales_df(), fields(pivots=["mes"], metrics=[agg("total"), {"agg": "count", "alias": "filas"}]))
        self.assertIn("Ene_total", out["data"][0])
        self.assertIn("Ene_filas", out["data"][0])

    def test_un_fan_out_demasiado_grande_se_corta(self):
        df = pd.DataFrame({
            "a": list("abcdef"),
            "b": list("uvwxyz"),
            "c": range(6),
        })
        with mock.patch("sheets_reports.engine.steps.aggregation.MAX_PIVOT_CELLS", 4):
            with self.assertRaises(ResultTooLargeError) as ctx:
                execute(df, fields(dimensions=["a"], pivots=["b"], metrics=[agg("total", field="c")]))
        self.assertIn("celdas", str(ctx.exception))


class ScalarTests(SimpleTestCase):
    def test_sin_dimensiones_devuelve_los_valores_de_las_metricas(self):
        out = execute(sales_df(), fields(dimensions=[], pivots=[], metrics=[agg("total"), agg("filas", "count")]))
        self.assertEqual(out["type"], "scalar")
        self.assertEqual(out["data"]["values"]["total"], 755.0)
        self.assertEqual(out["data"]["values"]["filas"], 6)


class CalculatedAndWindowTests(SimpleTestCase):
    def test_formula_sobre_las_columnas_agregadas(self):
        out = execute(sales_df(), fields(metrics=[agg("total"), calc("doble", "total * 2")]))
        self.assertEqual(out["data"][0]["doble"], 350.0)  # Hogar: 175 × 2

    def test_una_formula_mala_no_tumba_el_resultado(self):
        out = execute(sales_df(), fields(metrics=[agg("total"), calc("rota", "no_existe(")]))
        self.assertIsNone(out["data"][0]["rota"])

    def test_percent_of_total_sobre_el_alias(self):
        out = execute(sales_df(), fields(metrics=[
            agg("total"), {"field": "ventas", "agg": "sum", "alias": "pct",
                           "window": {"type": "percent_of_total"}},
        ]))
        row = out["data"][0]
        self.assertEqual(row["total"], 175.0)
        self.assertAlmostEqual(row["pct"], 23.18, places=2)  # 175 / 755

    def test_running_total(self):
        out = execute(sales_df(), fields(metrics=[
            agg("total"), {"field": "ventas", "agg": "sum", "alias": "acum",
                           "window": {"type": "running_total"}},
        ]))
        self.assertEqual([r["acum"] for r in out["data"]], [175.0, 675.0, 755.0])


class SortLimitTests(SimpleTestCase):
    def test_orden_ascendente_y_descendente(self):
        asc = execute(sales_df(), fields(metrics=[agg("total")], sort_by="total"))
        self.assertEqual(asc["data"][0]["categoria"], "Ropa")
        desc = execute(sales_df(), fields(metrics=[agg("total")], sort_by="-total"))
        self.assertEqual(desc["data"][0]["categoria"], "Electrónica")

    def test_orden_por_dimension(self):
        out = execute(sales_df(), fields(metrics=[agg("total")], sort_by="-categoria"))
        self.assertEqual(out["data"][0]["categoria"], "Ropa")

    def test_limit_recorta(self):
        out = execute(sales_df(), fields(metrics=[agg("total")], sort_by="-total", limit=2))
        self.assertEqual(len(out["data"]), 2)
        self.assertEqual([r["categoria"] for r in out["data"]], ["Electrónica", "Hogar"])

    def test_una_columna_de_orden_inexistente_no_lanza(self):
        out = execute(sales_df(), fields(metrics=[agg("total")], sort_by="no_existe"))
        self.assertEqual(len(out["data"]), 3)


class MetadataTests(SimpleTestCase):
    def test_los_pasos_dejan_la_metadata_que_usan_los_widgets(self):
        out = execute(sales_df(), fields(pivots=["mes"], metrics=[agg("total")]))
        meta = out["metadata"]
        self.assertEqual(meta["dimensions"], ["categoria"])
        self.assertEqual(meta["pivots"], ["mes"])
        self.assertEqual(meta["pivot_column"], "mes")
        self.assertEqual(meta["metrics"], [agg("total")])

    def test_un_widget_puede_encadenar_solo_algunos_pasos(self):
        form_fields = WidgetFields.from_dict(fields(dimensions=[], metrics=[agg("total", field="plan")]))
        metadata = {}
        df = filter_rows(sellers_df(), form_fields.filters, metadata)
        df = apply_aggregation(df, form_fields, metadata)
        out = build_query_result(df, form_fields, metadata)
        self.assertEqual(out["data"]["values"]["total"], 1020.0)


class NestedPivotTests(SimpleTestCase):
    """La tabla dinámica (`widget_type="dynamic_table"`) anida filas y columnas: cada nivel
    lleva su subtotal y el cruce, el total general, los porcentajes y el orden se calculan
    sobre esas claves y no sobre el marco plano."""

    @staticmethod
    def nested(df, **overrides):
        out = run_steps(df, WidgetFields.from_dict(fields(**overrides)), widget_type="dynamic_table")
        return out["metadata"]["nested"]

    def test_anida_filas_columnas_y_celdas(self):
        n = self.nested(sellers_df(), dimensions=["categoria", "anio"], pivots=["vendedor"],
                        metrics=[agg("total_ventas")])
        self.assertEqual(n["metrics"], ["total_ventas"])
        self.assertEqual(n["column_keys"], [("Ana",), ("Luis",), ("Eva",)])
        self.assertEqual([(r["key"], r["subtotal"]) for r in n["rows"] if r["subtotal"]],
                         [(["Hogar"], True), (["Ropa"], True)])
        hogar = next(r for r in n["rows"] if r["key"] == ["Hogar"])
        self.assertEqual(hogar["totals"]["total_ventas"], 600.0)
        self.assertEqual(hogar["cells"][("Ana",)]["total_ventas"], 100.0)
        self.assertEqual(n["grand"]["totals"]["total_ventas"], 1070.0)

    def test_el_orden_y_el_limite_actuan_sobre_las_filas_anidadas(self):
        n = self.nested(sellers_df(), dimensions=["categoria", "anio"],
                        sort_by="total_ventas", limit=3, metrics=[agg("total_ventas")])
        self.assertEqual([r["key"] for r in n["rows"]],
                         [["Ropa", 2025], ["Ropa", 2026], ["Ropa"]])

    def test_el_porcentaje_de_cada_celda_usa_el_total_de_su_columna(self):
        n = self.nested(sales_df(), dimensions=["categoria"], pivots=["mes"],
                        metrics=[{"field": "ventas", "agg": "sum", "alias": "pct",
                                  "window": {"type": "percent_of_total"}}])
        self.assertEqual(n["cells"][(("Hogar",), ("Ene",))]["pct"], 25.0)  # 100 de los 400 de Ene
        self.assertEqual(n["grand"]["totals"]["pct"], 100.0)

    def test_las_formulas_se_calculan_tambien_en_los_subtotales(self):
        n = self.nested(sellers_df(), dimensions=["categoria", "anio"],
                        metrics=[agg("total_ventas"), agg("total_plan", "sum", "plan"),
                                 {"type": "formula", "alias": "margen",
                                  "expression": "total_plan / total_ventas", "field": "ventas"}])
        self.assertIn("margen", n["metrics"])
        hogar = next(r for r in n["rows"] if r["key"] == ["Hogar"])
        # El subtotal NO suma los márgenes de sus hijos: es plan/ventas del propio subtotal.
        self.assertAlmostEqual(hogar["totals"]["margen"], 520 / 600, places=6)

    def test_un_cruce_demasiado_grande_se_corta(self):
        df = pd.DataFrame({"a": list("abcdef"), "b": list("uvwxyz"), "c": range(6)})
        with mock.patch("sheets_reports.engine.steps.aggregation.MAX_PIVOT_CELLS", 4):
            with self.assertRaises(ResultTooLargeError) as ctx:
                run_steps(df, WidgetFields.from_dict(
                    fields(dimensions=["a"], pivots=["b"], metrics=[agg("total", field="c")])),
                    widget_type="dynamic_table")
        self.assertIn("celdas", str(ctx.exception))
