import pandas as pd
from django.test import SimpleTestCase

from sheets_reports.services.query_engine import run_data_spec
from sheets_reports.tests.fixtures import sales_df, spec


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
        self.assertEqual(result["rows"]["Hogar"], {"Ene": 100.0, "Feb": 50.0, "Mar": 25.0})
        # Combinación sin filas: una suma vacía es 0.
        self.assertEqual(result["rows"]["Ropa"], {"Ene": 0, "Feb": 80.0, "Mar": 0})

    def test_con_pivote_y_avg_deja_none_en_combinaciones_vacias(self):
        result = run_data_spec(sales_df(), spec(
            pivot="mes", metrics=[{"field": "ventas", "agg": "avg", "as": "promedio"}],
        ))
        self.assertIsNone(result["rows"]["Ropa"]["Ene"])

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
