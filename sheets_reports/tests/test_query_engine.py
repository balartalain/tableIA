from unittest import mock

import pandas as pd
from django.test import SimpleTestCase

from sheets_reports.services.query_engine import ResultTooLargeError, run_data_spec
from sheets_reports.tests.fixtures import sales_df, spec


def cells(result, dimension_value, metric=None):
    """{valor_pivote: valor} de una fila de un resultado con pivote, para una métrica."""
    metric = metric or result["metric"]
    return {p: v[metric] for p, v in result["rows"][dimension_value].items()}


class RunDataSpecTests(SimpleTestCase):
    def test_sin_pivote_devuelve_dataframe_plano_en_orden_de_aparicion(self):
        result = run_data_spec(sales_df(), spec())

        self.assertIsInstance(result, pd.DataFrame)
        self.assertEqual(list(result.columns), ["categoria", "total_ventas"])
        self.assertEqual(
            result.to_dict(orient="records"),
            [
                {"categoria": "Hogar", "total_ventas": 175.0},
                {"categoria": "Electrónica", "total_ventas": 500.0},
                {"categoria": "Ropa", "total_ventas": 80.0},
            ],
        )

    def test_multiples_metricas(self):
        result = run_data_spec(sales_df(), spec(metrics=[
            {"field": "categoria", "agg": "count", "as": "cantidad"},
            {"field": "ventas", "agg": "avg", "as": "promedio_ventas"},
            {"field": "ventas", "agg": "sum", "as": "total_ventas"},
        ]))

        rows = {r["categoria"]: r for r in result.to_dict(orient="records")}
        self.assertEqual(list(result.columns), ["categoria", "cantidad", "promedio_ventas", "total_ventas"])
        self.assertEqual(rows["Hogar"]["cantidad"], 3)
        self.assertAlmostEqual(rows["Hogar"]["promedio_ventas"], 175.0 / 3)
        self.assertEqual(rows["Electrónica"]["cantidad"], 2)
        self.assertEqual(rows["Electrónica"]["total_ventas"], 500.0)

    def test_con_pivote_devuelve_estructura_anidada(self):
        result = run_data_spec(sales_df(), spec(pivot="mes"))

        self.assertEqual(result["dimension"], "categoria")
        self.assertEqual(result["pivot"], "mes")
        self.assertEqual(result["metric"], "total_ventas")
        self.assertEqual(result["dimension_values"], ["Hogar", "Electrónica", "Ropa"])
        # Orden de aparición en la hoja, no alfabético.
        self.assertEqual(result["pivot_values"], ["Ene", "Feb", "Mar"])
        self.assertEqual(result["metrics"], ["total_ventas"])
        self.assertEqual(cells(result, "Hogar"), {"Ene": 100.0, "Feb": 50.0, "Mar": 25.0})
        # Combinación sin filas: una suma vacía es 0.
        self.assertEqual(cells(result, "Ropa"), {"Ene": 0, "Feb": 80.0, "Mar": 0})
        self.assertEqual(result["row_totals"]["Hogar"], {"total_ventas": 175.0})
        self.assertEqual(result["column_totals"]["Feb"], {"total_ventas": 330.0})
        self.assertEqual(result["grand_totals"], {"total_ventas": 755.0})

    def test_con_pivote_y_avg_deja_none_en_combinaciones_vacias(self):
        result = run_data_spec(sales_df(), spec(
            pivot="mes", metrics=[{"field": "ventas", "agg": "avg", "as": "promedio"}],
        ))
        self.assertIsNone(result["rows"]["Ropa"]["Ene"]["promedio"])
        # El total de un promedio es el promedio real, no la suma ni el promedio de celdas.
        self.assertAlmostEqual(result["row_totals"]["Hogar"]["promedio"], 175.0 / 3)
        self.assertAlmostEqual(result["grand_totals"]["promedio"], 755.0 / 6)

    def test_filtros_eq_in_y_gt(self):
        result = run_data_spec(sales_df(), spec(filters=[
            {"field": "anio", "op": "eq", "value": 2026},
            {"field": "categoria", "op": "in", "value": ["Hogar", "Ropa"]},
            {"field": "ventas", "op": "gt", "value": 30},
        ]))
        self.assertEqual(result.to_dict(orient="records"), [{"categoria": "Hogar", "total_ventas": 150.0}])

    def test_filtro_con_valor_texto_sobre_columna_numerica(self):
        # Los filtros del tablero llegan por URL como texto.
        result = run_data_spec(sales_df(), spec(filters=[{"field": "anio", "op": "eq", "value": "2025"}]))
        self.assertEqual(result.to_dict(orient="records"), [{"categoria": "Ropa", "total_ventas": 80.0}])

    def test_filtros_ne_lte(self):
        result = run_data_spec(sales_df(), spec(filters=[
            {"field": "categoria", "op": "ne", "value": "Hogar"},
            {"field": "ventas", "op": "lte", "value": 200},
        ]))
        self.assertEqual(
            result.to_dict(orient="records"),
            [{"categoria": "Ropa", "total_ventas": 80.0}, {"categoria": "Electrónica", "total_ventas": 200.0}],
        )

    def test_sort_por_metrica_desc(self):
        result = run_data_spec(sales_df(), spec(sort={"by": "total_ventas", "dir": "desc"}))
        self.assertEqual(list(result["categoria"]), ["Electrónica", "Hogar", "Ropa"])

    def test_sort_con_pivote_ordena_por_total_de_la_metrica(self):
        result = run_data_spec(sales_df(), spec(pivot="mes", sort={"by": "total_ventas", "dir": "asc"}))
        self.assertEqual(result["dimension_values"], ["Ropa", "Hogar", "Electrónica"])

    def test_kpi_sin_dimension(self):
        result = run_data_spec(sales_df(), spec(
            dimensions=[],
            metrics=[{"field": "ventas", "agg": "sum", "as": "total_ventas"}],
            filters=[{"field": "anio", "op": "eq", "value": 2026}],
        ))
        self.assertEqual(result, {"total_ventas": 675.0})

    def test_kpi_count(self):
        result = run_data_spec(sales_df(), spec(
            dimensions=[], metrics=[{"field": "categoria", "agg": "count", "as": "cantidad"}],
        ))
        self.assertEqual(result, {"cantidad": 6})

    def test_filtros_que_no_dejan_filas(self):
        result = run_data_spec(sales_df(), spec(filters=[{"field": "anio", "op": "eq", "value": 1999}]))
        self.assertTrue(result.empty)
        self.assertEqual(list(result.columns), ["categoria", "total_ventas"])

    def test_pct_count_legacy_sin_pivote_suma_100(self):
        result = run_data_spec(sales_df(), spec(metrics=[{"field": "categoria", "agg": "pct_count", "as": "porcentaje"}]))
        self.assertEqual(
            result.to_dict(orient="records"),
            [{"categoria": "Hogar", "porcentaje": 50.0},
             {"categoria": "Electrónica", "porcentaje": 33.33},
             {"categoria": "Ropa", "porcentaje": 16.67}],
        )

    def test_pct_sum_sin_pivote(self):
        result = run_data_spec(sales_df(), spec(metrics=[{"field": "ventas", "agg": "pct_sum", "as": "porcentaje"}]))
        self.assertEqual(list(result["porcentaje"]), [23.18, 66.23, 10.6])

    def test_pct_row_con_pivote_cada_fila_suma_100(self):
        result = run_data_spec(sales_df(), spec(
            pivot="mes", metrics=[{"field": "categoria", "agg": "count", "as": "porcentaje", "show_as": "pct_row"}],
        ))
        self.assertEqual(cells(result, "Hogar", "porcentaje"), {"Ene": 33.33, "Feb": 33.33, "Mar": 33.33})
        self.assertEqual(cells(result, "Ropa", "porcentaje"), {"Ene": 0.0, "Feb": 100.0, "Mar": 0.0})
        self.assertEqual(result["row_totals"]["Ropa"], {"porcentaje": 100.0})
        # La fila de totales: cuánto pesa cada mes en el total general.
        self.assertEqual(result["column_totals"]["Feb"], {"porcentaje": 50.0})
        self.assertEqual(result["grand_totals"], {"porcentaje": 100.0})

    def test_pct_column_con_pivote_cada_columna_suma_100(self):
        result = run_data_spec(sales_df(), spec(
            pivot="mes", metrics=[{"field": "categoria", "agg": "count", "as": "porcentaje", "show_as": "pct_column"}],
        ))
        self.assertEqual(cells(result, "Hogar", "porcentaje"), {"Ene": 50.0, "Feb": 33.33, "Mar": 100.0})
        self.assertEqual(result["column_totals"]["Feb"], {"porcentaje": 100.0})
        self.assertEqual(result["row_totals"]["Hogar"], {"porcentaje": 50.0})

    def test_pct_total_con_pivote_todas_las_celdas_suman_100(self):
        result = run_data_spec(sales_df(), spec(
            pivot="mes", metrics=[{"field": "categoria", "agg": "count", "as": "porcentaje", "show_as": "pct_total"}],
        ))
        self.assertEqual(cells(result, "Hogar", "porcentaje"), {"Ene": 16.67, "Feb": 16.67, "Mar": 16.67})
        self.assertEqual(result["row_totals"]["Hogar"], {"porcentaje": 50.0})
        total = sum(v["porcentaje"] for row in result["rows"].values() for v in row.values())
        self.assertAlmostEqual(total, 100, delta=0.1)

    def test_pct_legacy_con_pivote_se_traduce(self):
        by_row = run_data_spec(sales_df(), spec(
            pivot="mes", metrics=[{"field": "categoria", "agg": "pct_count", "as": "porcentaje"}],
        ))
        self.assertEqual(cells(by_row, "Ropa", "porcentaje"), {"Ene": 0.0, "Feb": 100.0, "Mar": 0.0})
        of_total = run_data_spec(sales_df(), spec(
            pivot="mes", metrics=[{"field": "categoria", "agg": "pct_count", "as": "porcentaje", "of": "total"}],
        ))
        self.assertEqual(cells(of_total, "Ropa", "porcentaje"), {"Ene": 0.0, "Feb": 16.67, "Mar": 0.0})

    def test_pivote_con_varias_metricas(self):
        result = run_data_spec(sales_df(), spec(pivot="mes", metrics=[
            {"field": "categoria", "agg": "count", "as": "cantidad"},
            {"field": "categoria", "agg": "count", "as": "porcentaje", "show_as": "pct_row"},
        ]))
        self.assertEqual(result["metrics"], ["cantidad", "porcentaje"])
        self.assertEqual(result["rows"]["Hogar"]["Ene"], {"cantidad": 1, "porcentaje": 33.33})
        self.assertEqual(result["row_totals"]["Hogar"], {"cantidad": 3, "porcentaje": 100.0})

    def test_sort_con_pivote_por_metrica_usa_el_total_de_la_fila(self):
        result = run_data_spec(sales_df(), spec(pivot="mes", metrics=[
            {"field": "ventas", "agg": "sum", "as": "total_ventas"},
            {"field": "categoria", "agg": "count", "as": "cantidad"},
        ], sort={"by": "cantidad", "dir": "desc"}))
        self.assertEqual(result["dimension_values"], ["Hogar", "Electrónica", "Ropa"])

    def test_nuevas_agregaciones(self):
        result = run_data_spec(sales_df(), spec(metrics=[
            {"field": "mes", "agg": "count_distinct", "as": "meses"},
            {"field": "ventas", "agg": "min", "as": "minimo"},
            {"field": "ventas", "agg": "max", "as": "maximo"},
            {"field": "ventas", "agg": "median", "as": "mediana"},
        ]))
        hogar = result.to_dict(orient="records")[0]
        self.assertEqual(hogar, {"categoria": "Hogar", "meses": 3, "minimo": 25.0, "maximo": 100.0, "mediana": 50.0})
        self.assertEqual(result.attrs["totals"], {"meses": 3, "minimo": 25.0, "maximo": 300.0, "mediana": 90.0})

    def test_sin_pivote_totales_y_pct_column(self):
        result = run_data_spec(sales_df(), spec(metrics=[
            {"field": "ventas", "agg": "sum", "as": "total_ventas"},
            {"field": "ventas", "agg": "sum", "as": "porcentaje", "show_as": "pct_column"},
        ]))
        self.assertEqual(list(result["porcentaje"]), [23.18, 66.23, 10.6])
        self.assertEqual(result.attrs["totals"], {"total_ventas": 755.0, "porcentaje": 100.0})

    def test_kpi_pct_es_filtrado_sobre_sin_filtrar(self):
        count = run_data_spec(sales_df(), spec(
            dimensions=[], metrics=[{"field": "categoria", "agg": "pct_count", "as": "porcentaje"}],
            filters=[{"field": "categoria", "op": "eq", "value": "Hogar"}],
        ))
        self.assertEqual(count, {"porcentaje": 50.0})
        total = run_data_spec(sales_df(), spec(
            dimensions=[], metrics=[{"field": "ventas", "agg": "pct_sum", "as": "porcentaje"}],
            filters=[{"field": "anio", "op": "eq", "value": 2026}],
        ))
        self.assertEqual(total, {"porcentaje": 89.4})

    def test_pct_con_total_cero_es_none(self):
        result = run_data_spec(sales_df().assign(ventas=0.0), spec(
            dimensions=[], metrics=[{"field": "ventas", "agg": "pct_sum", "as": "porcentaje"}],
        ))
        self.assertEqual(result, {"porcentaje": None})


class PivotTableTests(SimpleTestCase):
    """run_data_spec(layout="table"): filas y columnas anidadas con subtotales."""

    def table(self, **overrides):
        return run_data_spec(sales_df(), spec(**overrides), layout="table")

    def test_varias_filas_con_subtotal_despues_de_cada_grupo(self):
        result = self.table(dimensions=["anio", "categoria"])
        self.assertEqual([r["key"] for r in result["rows"]], [
            (2026, "Hogar"), (2026, "Electrónica"), (2026,), (2025, "Ropa"), (2025,),
        ])
        by_key = {r["key"]: r for r in result["rows"]}
        self.assertTrue(by_key[(2026,)]["subtotal"])
        self.assertFalse(by_key[(2026, "Hogar")]["subtotal"])
        self.assertEqual(by_key[(2026,)]["totals"], {"total_ventas": 675.0})
        self.assertEqual(result["grand"]["totals"], {"total_ventas": 755.0})

    def test_orden_por_metrica_en_cada_nivel(self):
        result = self.table(dimensions=["anio", "categoria"], sort={"by": "total_ventas", "dir": "asc"})
        self.assertEqual([r["key"] for r in result["rows"]], [
            (2025, "Ropa"), (2025,), (2026, "Hogar"), (2026, "Electrónica"), (2026,),
        ])

    def test_orden_por_una_dimension_solo_ordena_su_nivel(self):
        result = self.table(dimensions=["anio", "categoria"], sort={"by": "categoria", "dir": "asc"})
        self.assertEqual([r["key"] for r in result["rows"]][:3], [(2026, "Electrónica"), (2026, "Hogar"), (2026,)])

    def test_dos_pivotes_con_subtotal_de_columna(self):
        result = self.table(pivot=["anio", "mes"])
        self.assertEqual(result["column_keys"], [
            (2026, "Ene"), (2026, "Feb"), (2026, "Mar"), (2026,), (2025, "Feb"), (2025,),
        ])
        hogar = result["rows"][0]["cells"]
        self.assertEqual(hogar[(2026, "Ene")], {"total_ventas": 100.0})
        self.assertEqual(hogar[(2026,)], {"total_ventas": 175.0})
        self.assertEqual(hogar[(2025, "Feb")], {"total_ventas": 0})
        self.assertEqual(result["grand"]["cells"][(2026,)], {"total_ventas": 675.0})

    def test_porcentajes_en_subtotales_usan_el_total_del_mismo_nivel(self):
        result = self.table(dimensions=["categoria"], pivot=["anio", "mes"], metrics=[
            {"field": "ventas", "agg": "sum", "as": "pct", "show_as": "pct_column"},
        ])
        electronica = result["rows"][1]["cells"]
        self.assertEqual(electronica[(2026,)], {"pct": 74.07})
        self.assertEqual(result["grand"]["cells"][(2026,)], {"pct": 100.0})

    def test_tabla_demasiado_grande(self):
        with mock.patch("sheets_reports.services.query_engine.MAX_TABLE_CELLS", 5):
            with self.assertRaises(ResultTooLargeError):
                self.table(pivot="mes")

    def test_enteros_leidos_como_float_se_muestran_enteros(self):
        # Una celda vacía hace que pandas lea la columna "anio" como float (2026.0).
        df = sales_df()
        df.loc[len(df)] = {"categoria": "Ropa", "mes": "Mar", "anio": None, "ventas": 10.0}
        result = run_data_spec(df, spec(pivot="anio"), layout="table")
        self.assertEqual(result["column_keys"], [(2026,), (2025,)])
        chart = run_data_spec(df, spec(dimensions=["anio"]))
        self.assertEqual(list(chart["anio"]), [2026, 2025])
