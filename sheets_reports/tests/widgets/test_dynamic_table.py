"""Tabla dinámica: lo que `compile` le devuelve al frontend (filas, columnas, totales, formatos)."""
from django.test import SimpleTestCase

from sheets_reports.tests.fixtures import agg, compiled, errors_for, fields, sellers_df


class DynamicTableCompileTests(SimpleTestCase):
    def test_columnas_con_etiqueta_y_campo(self):
        out = compiled("dynamic_table", fields(dimensions=["categoria"]))
        self.assertEqual(out["columns"], [
            {"header": "Categoria", "field": "categoria"},
            {"header": "Suma Ventas", "field": "total_ventas"},
        ])
        self.assertEqual(out["rowFields"], ["categoria"])
        self.assertEqual([r["categoria"] for r in out["rows"]], ["Hogar", "Electrónica", "Ropa"])

    def test_un_hijo_por_valor_del_pivote(self):
        out = compiled("dynamic_table", fields(pivots=["mes"], metrics=[agg("total_ventas")]))
        pivot_col = next(c for c in out["columns"] if c.get("children"))
        self.assertEqual(pivot_col["header"], "Suma Ventas")
        self.assertEqual([c["field"] for c in pivot_col["children"]],
                         ["__pivots.Ene.total_ventas", "__pivots.Feb.total_ventas",
                          "__pivots.Mar.total_ventas"])
        self.assertEqual(out["columns"][0]["field"], "categoria")
        # La tabla se cierra con la columna «Total general» (apagable con showColumnTotals).
        self.assertEqual(out["columns"][-1],
                         {"header": "Total general", "field": "__total.total_ventas", "total": True})
        self.assertEqual(out["totals"]["__total.total_ventas"], 755.0)

    def test_totales_de_las_columnas_numericas(self):
        out = compiled("dynamic_table", fields())
        self.assertEqual(out["totals"], {"categoria": "Total general", "total_ventas": 755.0})

    def test_filas_de_subtotal_por_dimension(self):
        out = compiled("dynamic_table", fields(dimensions=["categoria", "anio"]), df=sellers_df())
        subtotals = [r for r in out["rows"] if r.get("__subtotal")]
        self.assertEqual([r["categoria"] for r in subtotals], ["Total Hogar", "Total Ropa"])
        self.assertTrue(all(r["anio"] is None for r in subtotals))
        self.assertEqual(subtotals[0]["total_ventas"], 600.0)
        self.assertEqual([r["categoria"] for r in out["rows"]],
                         ["Hogar", "Hogar", "Total Hogar", "Ropa", "Ropa", "Total Ropa"])
        self.assertEqual(out["totals"]["categoria"], "Total general")

    def test_columna_de_subtotal_tras_cada_valor_del_pivote(self):
        out = compiled("dynamic_table",
                       fields(dimensions=["categoria"], pivots=["anio", "vendedor"],
                              metrics=[agg("total_ventas")]),
                       df=sellers_df())
        group = out["columns"][1]
        self.assertEqual([c["header"] for c in group["children"]],
                         ["2025", "Total 2025", "2026", "Total 2026"])
        self.assertEqual([c.get("subtotal") for c in group["children"]],
                         [None, True, None, True])
        self.assertEqual(group["children"][1]["field"], "__pivots.2025.total_ventas")
        self.assertEqual(out["columns"][-1],
                         {"header": "Total general", "field": "__total.total_ventas", "total": True})

    def test_varias_metricas_anidadas_bajo_una_subcolumna_por_metrica(self):
        out = compiled("dynamic_table",
                       fields(dimensions=["categoria"], pivots=["anio"],
                              metrics=[agg("total_ventas"), agg("total_plan", "sum", "plan")]),
                       df=sellers_df())
        group = out["columns"][1]
        self.assertEqual(group["header"], "2025")
        self.assertEqual([c["header"] for c in group["children"]], ["Suma Ventas", "Suma Plan"])
        self.assertEqual(group["children"][0]["field"], "__pivots.2025.total_ventas")
        self.assertEqual([c["header"] for c in out["columns"][-1]["children"]],
                         ["Suma Ventas", "Suma Plan"])

    def test_nombre_a_mostrar_de_la_metrica(self):
        out = compiled("dynamic_table", fields(metrics=[
            {"field": "ventas", "agg": "sum", "alias": "t", "label": "Costos totales"},
        ]))
        self.assertEqual(out["columns"][1], {"header": "Costos totales", "field": "t"})
        # Sin label se muestra el agg en español con la columna («Suma Ventas»): nunca el alias.
        self.assertEqual(out["columns"][0], {"header": "Categoria", "field": "categoria"})

    def test_porcentajes(self):
        out = compiled("dynamic_table", fields(metrics=[
            {"field": "ventas", "agg": "sum", "alias": "pct", "window": {"type": "percent_of_total"}},
        ]))
        self.assertEqual(out["percent"], ["pct"])

    def test_porcentajes_con_pivote_apuntan_a_cada_celda_y_al_total(self):
        out = compiled("dynamic_table", fields(dimensions=["categoria"], pivots=["mes"],
                                               metrics=[{"field": "ventas", "agg": "sum",
                                                         "alias": "pct",
                                                         "window": {"type": "percent_of_total"}}]))
        self.assertEqual(out["percent"],
                         ["__pivots.Ene.pct", "__pivots.Feb.pct", "__pivots.Mar.pct",
                          "__total.pct"])
        row = out["rows"][0]
        # Cada celda sobre el total de SU columna: Hogar/Ene es 100 de los 400 de «Ene».
        self.assertEqual(row["__pivots.Ene.pct"], 25.0)
        # La fila completa (175) sobre el total general (755).
        self.assertEqual(row["__total.pct"], 23.18)


class DynamicTableValidationTests(SimpleTestCase):
    def test_necesita_al_menos_una_dimension(self):
        """Sin dimensiones sería un KPI dentro de una tabla (o, con pivote, una tabla girada)."""
        for pivots in ([], ["mes"]):
            with self.subTest(pivots=pivots):
                errors = errors_for("dynamic_table", fields(dimensions=[], pivots=pivots))
                self.assertTrue(any(e.startswith("fields.dimensions") for e in errors), errors)
        self.assertEqual(errors_for("dynamic_table", fields(dimensions=["categoria"], pivots=["mes"])), [])
