import datetime
from unittest import mock

import pandas as pd
from django.test import SimpleTestCase

from sheets_reports.services.query_engine import ResultTooLargeError, resolve_relative, run_data_spec
from sheets_reports.tests.fixtures import agg, sales_df, sellers_df, spec


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
            {"type": "agg", "agg": "count", "as": "cantidad"},
            {"type": "agg", "field": "ventas", "agg": "avg", "as": "promedio_ventas"},
            {"type": "agg", "field": "ventas", "agg": "sum", "as": "total_ventas"},
        ]))

        rows = {r["categoria"]: r for r in result.to_dict(orient="records")}
        self.assertEqual(list(result.columns), ["categoria", "cantidad", "promedio_ventas", "total_ventas"])
        self.assertEqual(rows["Hogar"]["cantidad"], 3)
        self.assertAlmostEqual(rows["Hogar"]["promedio_ventas"], 175.0 / 3)
        self.assertEqual(rows["Electrónica"]["cantidad"], 2)
        self.assertEqual(rows["Electrónica"]["total_ventas"], 500.0)

    def test_con_pivote_devuelve_estructura_anidada(self):
        result = run_data_spec(sales_df(), spec(pivots=["mes"]))

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
            pivots=["mes"], metrics=[{"type": "agg", "field": "ventas", "agg": "avg", "as": "promedio"}],
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
        result = run_data_spec(sales_df(), spec(pivots=["mes"], sort={"by": "total_ventas", "dir": "asc"}))
        self.assertEqual(result["dimension_values"], ["Ropa", "Hogar", "Electrónica"])

    def test_kpi_sin_dimension(self):
        result = run_data_spec(sales_df(), spec(
            dimensions=[],
            metrics=[{"type": "agg", "field": "ventas", "agg": "sum", "as": "total_ventas"}],
            filters=[{"field": "anio", "op": "eq", "value": 2026}],
        ))
        self.assertEqual(result, {"total_ventas": 675.0})

    def test_kpi_count(self):
        result = run_data_spec(sales_df(), spec(
            dimensions=[], metrics=[{"type": "agg", "agg": "count", "as": "cantidad"}],
        ))
        self.assertEqual(result, {"cantidad": 6})

    def test_filtros_que_no_dejan_filas(self):
        result = run_data_spec(sales_df(), spec(filters=[{"field": "anio", "op": "eq", "value": 1999}]))
        self.assertTrue(result.empty)
        self.assertEqual(list(result.columns), ["categoria", "total_ventas"])

    def test_pct_column_de_conteo_sin_pivote_suma_100(self):
        result = run_data_spec(sales_df(), spec(metrics=[agg("porcentaje", "count", show_as="pct_column")]))
        self.assertEqual(
            result.to_dict(orient="records"),
            [{"categoria": "Hogar", "porcentaje": 50.0},
             {"categoria": "Electrónica", "porcentaje": 33.33},
             {"categoria": "Ropa", "porcentaje": 16.67}],
        )

    def test_pct_column_de_suma_sin_pivote(self):
        result = run_data_spec(sales_df(), spec(metrics=[agg("porcentaje", show_as="pct_column")]))
        self.assertEqual(list(result["porcentaje"]), [23.18, 66.23, 10.6])

    def test_pct_row_con_pivote_cada_fila_suma_100(self):
        result = run_data_spec(sales_df(), spec(
            pivots=["mes"], metrics=[{"type": "agg", "agg": "count", "as": "porcentaje", "show_as": "pct_row"}],
        ))
        self.assertEqual(cells(result, "Hogar", "porcentaje"), {"Ene": 33.33, "Feb": 33.33, "Mar": 33.33})
        self.assertEqual(cells(result, "Ropa", "porcentaje"), {"Ene": 0.0, "Feb": 100.0, "Mar": 0.0})
        self.assertEqual(result["row_totals"]["Ropa"], {"porcentaje": 100.0})
        # La fila de totales: cuánto pesa cada mes en el total general.
        self.assertEqual(result["column_totals"]["Feb"], {"porcentaje": 50.0})
        self.assertEqual(result["grand_totals"], {"porcentaje": 100.0})

    def test_pct_column_con_pivote_cada_columna_suma_100(self):
        result = run_data_spec(sales_df(), spec(
            pivots=["mes"], metrics=[{"type": "agg", "agg": "count", "as": "porcentaje", "show_as": "pct_column"}],
        ))
        self.assertEqual(cells(result, "Hogar", "porcentaje"), {"Ene": 50.0, "Feb": 33.33, "Mar": 100.0})
        self.assertEqual(result["column_totals"]["Feb"], {"porcentaje": 100.0})
        self.assertEqual(result["row_totals"]["Hogar"], {"porcentaje": 50.0})

    def test_pct_total_con_pivote_todas_las_celdas_suman_100(self):
        result = run_data_spec(sales_df(), spec(
            pivots=["mes"], metrics=[{"type": "agg", "agg": "count", "as": "porcentaje", "show_as": "pct_total"}],
        ))
        self.assertEqual(cells(result, "Hogar", "porcentaje"), {"Ene": 16.67, "Feb": 16.67, "Mar": 16.67})
        self.assertEqual(result["row_totals"]["Hogar"], {"porcentaje": 50.0})
        total = sum(v["porcentaje"] for row in result["rows"].values() for v in row.values())
        self.assertAlmostEqual(total, 100, delta=0.1)

    def test_nuevas_agregaciones(self):
        result = run_data_spec(sales_df(), spec(metrics=[
            {"type": "agg", "field": "mes", "agg": "count_distinct", "as": "meses"},
            {"type": "agg", "field": "ventas", "agg": "min", "as": "minimo"},
            {"type": "agg", "field": "ventas", "agg": "max", "as": "maximo"},
            {"type": "agg", "field": "ventas", "agg": "median", "as": "mediana"},
        ]))
        hogar = result.to_dict(orient="records")[0]
        self.assertEqual(hogar, {"categoria": "Hogar", "meses": 3, "minimo": 25.0, "maximo": 100.0, "mediana": 50.0})
        self.assertEqual(result.attrs["totals"], {"meses": 3, "minimo": 25.0, "maximo": 300.0, "mediana": 90.0})

    def test_sin_pivote_totales_y_pct_column(self):
        result = run_data_spec(sales_df(), spec(metrics=[
            {"type": "agg", "field": "ventas", "agg": "sum", "as": "total_ventas"},
            {"type": "agg", "field": "ventas", "agg": "sum", "as": "porcentaje", "show_as": "pct_column"},
        ]))
        self.assertEqual(list(result["porcentaje"]), [23.18, 66.23, 10.6])
        self.assertEqual(result.attrs["totals"], {"total_ventas": 755.0, "porcentaje": 100.0})

    def test_kpi_pct_es_filtrado_sobre_sin_filtrar(self):
        count = run_data_spec(sales_df(), spec(
            dimensions=[], metrics=[agg("porcentaje", "count", show_as="pct_total")],
            filters=[{"field": "categoria", "op": "eq", "value": "Hogar"}],
        ))
        self.assertEqual(count, {"porcentaje": 50.0})
        total = run_data_spec(sales_df(), spec(
            dimensions=[], metrics=[agg("porcentaje", show_as="pct_total")],
            filters=[{"field": "anio", "op": "eq", "value": 2026}],
        ))
        self.assertEqual(total, {"porcentaje": 89.4})

    def test_pct_con_total_cero_es_none(self):
        result = run_data_spec(sales_df().assign(ventas=0.0), spec(
            dimensions=[], metrics=[agg("porcentaje", show_as="pct_total")],
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
        result = self.table(pivots=["anio", "mes"])
        self.assertEqual(result["column_keys"], [
            (2026, "Ene"), (2026, "Feb"), (2026, "Mar"), (2026,), (2025, "Feb"), (2025,),
        ])
        hogar = result["rows"][0]["cells"]
        self.assertEqual(hogar[(2026, "Ene")], {"total_ventas": 100.0})
        self.assertEqual(hogar[(2026,)], {"total_ventas": 175.0})
        self.assertEqual(hogar[(2025, "Feb")], {"total_ventas": 0})
        self.assertEqual(result["grand"]["cells"][(2026,)], {"total_ventas": 675.0})

    def test_porcentajes_en_subtotales_usan_el_total_del_mismo_nivel(self):
        result = self.table(dimensions=["categoria"], pivots=["anio", "mes"], metrics=[
            {"type": "agg", "field": "ventas", "agg": "sum", "as": "pct", "show_as": "pct_column"},
        ])
        electronica = result["rows"][1]["cells"]
        self.assertEqual(electronica[(2026,)], {"pct": 74.07})
        self.assertEqual(result["grand"]["cells"][(2026,)], {"pct": 100.0})

    def test_tabla_demasiado_grande(self):
        with mock.patch("sheets_reports.services.query_engine.MAX_TABLE_CELLS", 5):
            with self.assertRaises(ResultTooLargeError):
                self.table(pivots=["mes"])

    def test_enteros_leidos_como_float_se_muestran_enteros(self):
        # Una celda vacía hace que pandas lea la columna "anio" como float (2026.0).
        df = sales_df()
        df.loc[len(df)] = {"categoria": "Ropa", "mes": "Mar", "anio": None, "ventas": 10.0}
        result = run_data_spec(df, spec(pivots=["anio"]), layout="table")
        self.assertEqual(result["column_keys"], [(2026,), (2025,)])
        chart = run_data_spec(df, spec(dimensions=["anio"]))
        self.assertEqual(list(chart["anio"]), [2026, 2025])


class ConditionTests(SimpleTestCase):
    def kpi(self, *filters, df=None):
        data_spec = spec(dimensions=[], metrics=[agg("total")], filters=list(filters))
        return run_data_spec(sales_df() if df is None else df, data_spec)["total"]

    def test_operadores_nuevos(self):
        self.assertEqual(self.kpi({"field": "mes", "op": "not_in", "value": ["Ene"]}), 355.0)
        self.assertEqual(self.kpi({"field": "ventas", "op": "between", "value": [50, 200]}), 430.0)
        self.assertEqual(self.kpi({"field": "categoria", "op": "contains", "value": "ELEC"}), 500.0)

    def test_vacios(self):
        df = sales_df()
        df.loc[0, "mes"] = None
        df.loc[1, "mes"] = "  "
        self.assertEqual(self.kpi({"field": "mes", "op": "is_empty"}, df=df), 400.0)
        self.assertEqual(self.kpi({"field": "mes", "op": "not_empty"}, df=df), 355.0)

    def test_relativos_de_los_datos(self):
        self.assertEqual(self.kpi({"field": "anio", "op": "eq", "relative": "max"}), 675.0)
        self.assertEqual(self.kpi({"field": "anio", "op": "eq", "relative": "second_max"}), 80.0)
        self.assertEqual(self.kpi({"field": "anio", "op": "eq", "relative": "min"}), 80.0)

    def test_relativos_del_reloj(self):
        today = datetime.date(2026, 2, 15)
        with mock.patch("sheets_reports.services.query_engine.datetime") as dt:
            dt.date.today.return_value = today
            self.assertEqual(self.kpi({"field": "anio", "op": "eq", "relative": "current_year"}), 675.0)
            self.assertEqual(self.kpi({"field": "anio", "op": "eq", "relative": "previous_year"}), 80.0)

    def test_resolve_relative(self):
        today = datetime.date(2026, 9, 30)
        self.assertEqual(resolve_relative(pd.Series([1]), "current_month", today), 9)
        self.assertEqual(resolve_relative(pd.Series(["2025-2", "2026-1", "2025-1"]), "max"), "2026-1")
        self.assertIsNone(resolve_relative(pd.Series([2026]), "second_max"))
        self.assertIsNone(resolve_relative(pd.Series([], dtype=float), "max"))

    def test_relativo_se_resuelve_sobre_toda_la_hoja(self):
        # En la tendencia, "el último año" es el de la hoja (2026), no el de cada punto: Ropa
        # solo tiene filas de 2025 y por eso no suma nada.
        result = run_data_spec(sales_df(), spec(dimensions=[], trend_by="categoria", metrics=[
            agg("ultimo", filters=[{"field": "anio", "op": "eq", "relative": "max"}]),
        ]))
        self.assertEqual(result["__trend"]["categories"], ["Electrónica", "Hogar", "Ropa"])
        self.assertEqual(result["__trend"]["series"]["ultimo"], [500.0, 175.0, 0])

    def test_relativo_sin_valor_no_deja_filas(self):
        self.assertEqual(self.kpi({"field": "anio", "op": "eq", "relative": "second_max"},
                                  df=sales_df()[sales_df()["anio"] == 2026]), 0)


class MetricFiltersAndCalcTests(SimpleTestCase):
    def test_filtros_por_metrica_en_kpi(self):
        result = run_data_spec(sales_df(), spec(dimensions=[], metrics=[
            agg("v2026", filters=[{"field": "anio", "op": "eq", "value": 2026}]),
            agg("v2025", filters=[{"field": "anio", "op": "eq", "value": 2025}]),
            {"type": "calc", "as": "variacion", "op": "diff_pct", "left": "v2026", "right": "v2025"},
        ]))
        self.assertEqual(result, {"v2026": 675.0, "v2025": 80.0, "variacion": 743.75})

    def test_filtros_por_metrica_por_grupo(self):
        result = run_data_spec(sales_df(), spec(metrics=[
            agg("total"), agg("feb", filters=[{"field": "mes", "op": "eq", "value": "Feb"}]),
        ]))
        self.assertEqual(result.to_dict(orient="records"), [
            {"categoria": "Hogar", "total": 175.0, "feb": 50.0},
            {"categoria": "Electrónica", "total": 500.0, "feb": 200.0},
            {"categoria": "Ropa", "total": 80.0, "feb": 80.0},
        ])

    def test_calc_por_grupo_y_en_totales(self):
        result = run_data_spec(sales_df(), spec(metrics=[
            agg("total"), agg("cantidad", "count"),
            {"type": "calc", "as": "ticket", "op": "div", "left": "total", "right": "cantidad"},
        ], sort={"by": "ticket", "dir": "desc"}))
        self.assertEqual(list(result["categoria"]), ["Electrónica", "Ropa", "Hogar"])
        self.assertEqual(list(result["ticket"])[0], 250.0)
        self.assertAlmostEqual(result.attrs["totals"]["ticket"], 755.0 / 6)

    def test_calc_division_entre_cero(self):
        result = run_data_spec(sales_df(), spec(dimensions=[], metrics=[
            agg("total"), agg("nada", filters=[{"field": "anio", "op": "eq", "value": 1999}]),
            {"type": "calc", "as": "ratio", "op": "ratio_pct", "left": "total", "right": "nada"},
        ]))
        self.assertIsNone(result["ratio"])

    def test_calc_en_tabla_dinamica(self):
        result = run_data_spec(sales_df(), spec(pivots=["anio"], metrics=[
            agg("total"), agg("cantidad", "count"),
            {"type": "calc", "as": "ticket", "op": "div", "left": "total", "right": "cantidad"},
        ], sort={"by": "ticket", "dir": "asc"}), layout="table")
        self.assertEqual([r["key"] for r in result["rows"]], [("Hogar",), ("Ropa",), ("Electrónica",)])
        hogar = result["rows"][0]
        self.assertAlmostEqual(hogar["cells"][(2026,)]["ticket"], 175.0 / 3)
        self.assertIsNone(hogar["cells"][(2025,)]["ticket"])
        self.assertAlmostEqual(result["grand"]["totals"]["ticket"], 755.0 / 6)


def seller_metrics():
    return [agg("ventas"), agg("plan", field="plan")]


class GroupedMetricTests(SimpleTestCase):
    def kpi(self, **grouped):
        metric = {"type": "grouped", "as": "g", "group_by": "vendedor", "inner": seller_metrics(),
                  "having": [], **grouped}
        return run_data_spec(sellers_df(), spec(dimensions=[], metrics=[metric]))["g"]

    def test_cuenta_grupos_que_no_cumplen(self):
        below = [{"left": "ventas", "op": "lt", "right": "plan"}]
        self.assertEqual(self.kpi(having=below, result="count"), 2)
        self.assertEqual(self.kpi(having=below, result="pct_groups"), 66.67)

    def test_condicion_contra_constante(self):
        self.assertEqual(self.kpi(having=[{"left": "ventas", "op": "gte", "right": 300}], result="count"), 2)

    def test_top_y_bottom(self):
        self.assertEqual(self.kpi(result="top", value="ventas"), {"label": "Eva", "value": 600.0})
        self.assertEqual(self.kpi(result="bottom", value="ventas"), {"label": "Ana", "value": 150.0})

    def test_top_con_empate_es_el_primero_de_la_hoja(self):
        df = sellers_df().assign(ventas=100.0)
        metric = {"type": "grouped", "as": "g", "group_by": "vendedor", "inner": [agg("ventas")],
                  "having": [], "result": "top", "value": "ventas"}
        self.assertEqual(run_data_spec(df, spec(dimensions=[], metrics=[metric]))["g"]["label"], "Ana")

    def test_resumen_de_los_que_cumplen(self):
        below = [{"left": "ventas", "op": "lt", "right": "plan"}]
        self.assertEqual(self.kpi(having=below, result="sum", value="ventas"), 470.0)
        self.assertEqual(self.kpi(result="avg", value="ventas"), 1070.0 / 3)

    def test_sin_grupos_que_cumplan(self):
        never = [{"left": "ventas", "op": "gt", "right": 99999}]
        self.assertEqual(self.kpi(having=never, result="count"), 0)
        self.assertIsNone(self.kpi(having=never, result="top", value="ventas"))
        self.assertIsNone(self.kpi(having=never, result="avg", value="ventas"))

    def test_interna_calculada_y_filtros_propios(self):
        inner = [*seller_metrics(), {"type": "calc", "as": "cumpl", "op": "ratio_pct", "left": "ventas", "right": "plan"}]
        self.assertEqual(self.kpi(inner=inner, result="top", value="cumpl"), {"label": "Eva", "value": 150.0})
        in_2026 = [{"field": "anio", "op": "eq", "value": 2026}]
        self.assertEqual(self.kpi(inner=inner, filters=in_2026, result="bottom", value="cumpl"),
                         {"label": "Ana", "value": 50.0})


class HavingAndLimitTests(SimpleTestCase):
    def run_sellers(self, layout="auto", **overrides):
        return run_data_spec(sellers_df(), spec(dimensions=["vendedor"], metrics=seller_metrics(), **overrides),
                             layout=layout)

    def test_having_deja_solo_los_grupos_que_cumplen(self):
        result = self.run_sellers(having=[{"left": "ventas", "op": "lt", "right": "plan"}])
        self.assertEqual(list(result["vendedor"]), ["Ana", "Luis"])
        self.assertEqual(result.attrs["totals"], {"ventas": 470.0, "plan": 620.0})

    def test_top_n(self):
        result = self.run_sellers(sort={"by": "ventas", "dir": "desc"}, limit={"n": 2})
        self.assertEqual(list(result["vendedor"]), ["Eva", "Luis"])

    def test_top_n_con_otros_al_final(self):
        result = self.run_sellers(sort={"by": "ventas", "dir": "asc"}, limit={"n": 1, "others": True})
        self.assertEqual(result.to_dict(orient="records"), [
            {"vendedor": "Ana", "ventas": 150.0, "plan": 220.0},
            {"vendedor": "Otros", "ventas": 920.0, "plan": 800.0},
        ])

    def test_otros_con_promedio_se_calcula_desde_los_datos(self):
        result = run_data_spec(sellers_df(), spec(
            dimensions=["vendedor"], metrics=[agg("promedio", "avg")],
            sort={"by": "promedio", "dir": "desc"}, limit={"n": 1, "others": True},
        ))
        self.assertEqual(result.to_dict(orient="records"), [
            {"vendedor": "Eva", "promedio": 300.0}, {"vendedor": "Otros", "promedio": 117.5},
        ])

    def test_limit_en_grafico_con_pivote(self):
        result = run_data_spec(sellers_df(), spec(
            dimensions=["vendedor"], pivots=["anio"], metrics=[agg("ventas")],
            sort={"by": "ventas", "dir": "desc"}, limit={"n": 1, "others": True},
        ))
        self.assertEqual(result["dimension_values"], ["Eva", "Otros"])
        self.assertEqual(result["row_totals"]["Otros"], {"ventas": 470.0})

    def test_having_y_limit_en_tabla(self):
        result = self.run_sellers(layout="table", sort={"by": "ventas", "dir": "desc"},
                                  limit={"n": 1, "others": True})
        self.assertEqual([r["key"] for r in result["rows"]], [("Eva",), ("Otros",)])
        self.assertEqual(result["grand"]["totals"], {"ventas": 1070.0, "plan": 1020.0})


class TrendTests(SimpleTestCase):
    def test_kpi_con_tendencia(self):
        result = run_data_spec(sales_df(), spec(dimensions=[], metrics=[agg("total")], trend_by="mes"))
        self.assertEqual(result["total"], 755.0)
        self.assertEqual(result["__trend"], {"categories": ["Ene", "Feb", "Mar"], "series": {"total": [400.0, 330.0, 25.0]}})

    def test_tendencia_de_un_porcentaje_usa_el_universo_de_cada_punto(self):
        result = run_data_spec(sales_df(), spec(
            dimensions=[], metrics=[agg("pct", "count", show_as="pct_total")],
            filters=[{"field": "categoria", "op": "eq", "value": "Hogar"}], trend_by="mes",
        ))
        self.assertEqual(result["__trend"]["series"]["pct"], [50.0, 33.33, 100.0])

    def trend(self, *filters, by):
        """Serie de la tendencia por `by` de una sola métrica filtrada con `filters`."""
        result = run_data_spec(sales_df(), spec(
            dimensions=[], trend_by=by, metrics=[agg("total", filters=list(filters))],
        ))
        return result["__trend"]

    def test_tendencia_ignora_el_filtro_sobre_su_propia_columna(self):
        # El bucket ya fija el año: "el último año" no puede dejar el resto de los puntos en cero.
        self.assertEqual(self.trend({"field": "anio", "op": "eq", "relative": "max"}, by="anio"),
                         {"categories": [2025, 2026], "series": {"total": [80.0, 675.0]}})
        # Igual con un eq literal, que tampoco distingue dentro de un bucket de un solo año.
        self.assertEqual(self.trend({"field": "anio", "op": "eq", "value": 2026}, by="anio")["series"],
                         {"total": [80.0, 675.0]})

    def test_tendencia_conserva_los_filtros_de_otras_columnas(self):
        self.assertEqual(self.trend({"field": "categoria", "op": "eq", "value": "Electrónica"}, by="anio")["series"],
                         {"total": [0.0, 500.0]})

    def test_tendencia_conserva_ne_en_su_propia_columna(self):
        # ne sí recorta un punto, así que no se toca: 2025 queda fuera a propósito.
        self.assertEqual(self.trend({"field": "anio", "op": "ne", "value": 2025}, by="anio")["series"],
                         {"total": [0.0, 675.0]})


class TrendOrderTests(SimpleTestCase):
    """La tendencia va de lo más antiguo a lo más reciente, sin importar el orden de la hoja."""

    def trend(self, column, values, sales=None):
        df = pd.DataFrame({column: values, "ventas": sales or [float(i + 1) for i in range(len(values))]})
        result = run_data_spec(df, spec(dimensions=[], trend_by=column, metrics=[agg("total")]))
        return result["__trend"]

    def test_anios_en_orden_descendente(self):
        out = self.trend("anio", [2026, 2025, 2024], sales=[30.0, 20.0, 10.0])
        self.assertEqual(out, {"categories": [2024, 2025, 2026], "series": {"total": [10.0, 20.0, 30.0]}})

    def test_fechas_en_texto(self):
        out = self.trend("fecha", ["2024-03-01", "2024-01-15", "2023-12-31"])
        self.assertEqual(out["categories"], ["2023-12-31", "2024-01-15", "2024-03-01"])
        self.assertEqual(out["series"]["total"], [3.0, 2.0, 1.0])

    def test_fechas_dia_primero(self):
        self.assertEqual(self.trend("fecha", ["02/03/2024", "15/01/2024"])["categories"], ["15/01/2024", "02/03/2024"])

    def test_nombres_de_mes(self):
        self.assertEqual(self.trend("mes", ["Mar", "Ene", "Feb"])["categories"], ["Ene", "Feb", "Mar"])
        self.assertEqual(self.trend("mes", ["marzo", "Enero", "FEBRERO"])["categories"], ["Enero", "FEBRERO", "marzo"])
        self.assertEqual(self.trend("mes", ["Dec", "Jan", "Sept."])["categories"], ["Jan", "Sept.", "Dec"])

    def test_periodos_en_orden_natural(self):
        out = self.trend("periodo", ["2025-10", "2025-2", "2024-12", "2026-T1"])
        self.assertEqual(out["categories"], ["2024-12", "2025-2", "2025-10", "2026-T1"])

    def test_se_quedan_los_mas_recientes(self):
        with mock.patch("sheets_reports.services.query_engine.MAX_TREND_POINTS", 3):
            out = self.trend("anio", [2026, 2020, 2025, 2021, 2024])
        self.assertEqual(out["categories"], [2024, 2025, 2026])
