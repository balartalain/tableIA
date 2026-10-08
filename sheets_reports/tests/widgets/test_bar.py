"""Gráfico de barras: lo que `compile` le devuelve al frontend."""
from django.test import SimpleTestCase

from sheets_reports.tests.fixtures import agg, compiled, fields


class BarCompileTests(SimpleTestCase):
    def test_sin_pivote_una_serie_por_metrica(self):
        out = compiled("bar", fields(metrics=[
            agg("total_ventas"), {"agg": "count", "alias": "cantidad"},
        ]))
        self.assertEqual(out["categories"], ["Hogar", "Electrónica", "Ropa"])
        self.assertEqual(out["series"], [
            {"name": "Suma Ventas", "data": [175.0, 500.0, 80.0]},
            {"name": "Conteo", "data": [3, 2, 1]},
        ])
        self.assertFalse(out["stacked"])
        self.assertEqual(out["percent"], [])

    def test_sin_dimension_una_barra_por_metrica(self):
        out = compiled("bar", fields(dimensions=[], metrics=[
            agg("total_ventas"), {"agg": "count", "alias": "cantidad", "label": "Ventas registradas"},
        ]))
        self.assertEqual(out["categories"], ["Suma Ventas", "Ventas registradas"])
        self.assertEqual(out["series"], [{"name": "Total", "data": [755.0, 6.0]}])
        self.assertTrue(out["ungrouped"])
        self.assertEqual(out["percent"], [])

    def test_sin_dimension_porcentaje_solo_si_todas_lo_son(self):
        share = {"agg": "sum", "field": "ventas", "alias": "p", "format": "percent"}
        mixed = compiled("bar", fields(dimensions=[], metrics=[share, agg()]))
        both = compiled("bar", fields(dimensions=[], metrics=[share, {**share, "alias": "q"}]))
        self.assertEqual((mixed["percent"], both["percent"]), ([], ["Total"]))

    def test_con_dimension_no_es_sin_agrupar(self):
        self.assertNotIn("ungrouped", compiled("bar", fields()))

    def test_con_pivote_una_serie_por_valor(self):
        out = compiled("bar", fields(dimensions=["categoria"], pivots=["mes"],
                                     metrics=[agg("total_ventas")]), {"stacked": True})
        self.assertEqual(out["categories"], ["Hogar", "Electrónica", "Ropa"])
        self.assertEqual([s["name"] for s in out["series"]], ["Ene", "Feb", "Mar"])
        self.assertEqual(out["series"][0]["data"], [100.0, 300.0, 0])
        self.assertTrue(out["stacked"])

    def test_orientacion_horizontal_y_cuadricula(self):
        out = compiled("bar", fields(), {"horizontal": True, "showGrid": True})
        self.assertTrue(out["horizontal"])

    def test_lineas_de_referencia_validas(self):
        out = compiled("bar", fields(), {"reference_lines": [
            {"kind": "value", "value": 400, "label": "Meta"},
            {"kind": "value"},
            {"kind": "mediana"},
        ]})
        self.assertEqual(len(out["referenceLines"]["yaxis"]), 1)
        self.assertEqual(out["referenceLines"]["yaxis"][0]["y"], 400)

    def test_porcentajes_se_marcan_para_el_formato(self):
        # El frontend reconoce las series por nombre, no por alias.
        out = compiled("bar", fields(metrics=[
            {"field": "ventas", "agg": "sum", "alias": "pct",
             "window": {"type": "percent_of_total"}},
            agg("total_ventas"),
        ]))
        self.assertEqual(out["percent"], ["Suma Ventas"])
        self.assertEqual([s["name"] for s in out["series"]], ["Suma Ventas", "Suma Ventas"])

    def test_con_pivote_porcentaje_de_la_fila(self):
        # Cada categoría reparte su 100 % entre los meses: Hogar = 100 + 50 + 25 = 175.
        out = compiled("bar", fields(dimensions=["categoria"], pivots=["mes"], metrics=[
            {"field": "ventas", "agg": "sum", "alias": "pct", "window": {"type": "percent_of_row"}},
        ]), {"stacked": True})
        self.assertEqual(out["series"], [
            {"name": "Ene", "data": [57.14, 60.0, 0.0]},
            {"name": "Feb", "data": [28.57, 40.0, 100.0]},
            {"name": "Mar", "data": [14.29, 0.0, 0.0]},
        ])
        self.assertEqual(out["percent"], ["Ene", "Feb", "Mar"])

    def test_con_pivote_porcentaje_del_total_por_valor_del_pivote(self):
        # Como en la tabla dinámica: cada celda sobre el total de su columna (Ene = 400).
        out = compiled("bar", fields(dimensions=["categoria"], pivots=["mes"], metrics=[
            {"field": "ventas", "agg": "sum", "alias": "pct", "window": {"type": "percent_of_total"}},
        ]))
        self.assertEqual(out["series"][0], {"name": "Ene", "data": [25.0, 75.0, 0.0]})
        self.assertEqual(out["percent"], ["Ene", "Feb", "Mar"])
