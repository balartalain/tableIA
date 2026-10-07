"""Lo que cada widget le devuelve al frontend para dibujarse (`compile`)."""
from django.test import SimpleTestCase

from sheets_reports.tests.fixtures import agg, compiled, fields, render, sales_df, sellers_df, table_fields


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


class LineCompileTests(SimpleTestCase):
    def test_no_lleva_stacked(self):
        out = compiled("line", fields(dimensions=["mes"], metrics=[agg("total_ventas")]))
        self.assertNotIn("stacked", out)
        self.assertNotIn("horizontal", out)
        self.assertEqual(out["categories"], ["Ene", "Feb", "Mar"])
        self.assertEqual(out["series"][0]["data"], [400.0, 330.0, 25.0])

    def test_mantiene_el_orden_de_la_hoja(self):
        out = compiled("line", fields(dimensions=["mes"], metrics=[agg("total_ventas")]))
        self.assertEqual(out["percent"], [])


class DonutCompileTests(SimpleTestCase):
    def test_series_y_etiquetas(self):
        out = compiled("donut", fields())
        self.assertEqual(out["labels"], ["Hogar", "Electrónica", "Ropa"])
        self.assertEqual(out["series"], [175.0, 500.0, 80.0])
        self.assertNotIn("_apex_options", out)


class KpiCompileTests(SimpleTestCase):
    def kpi(self, *metrics, **overrides):
        return fields(dimensions=[], pivots=[], metrics=list(metrics), **overrides)

    def test_comparacion_con_la_metrica_elegida(self):
        out = compiled("kpi", self.kpi(
            agg("actual", filters=[{"field": "anio", "op": "eq", "value": 2026}]),
            agg("anterior", filters=[{"field": "anio", "op": "eq", "value": 2025}]),
        ), {"compare": "anterior"})
        self.assertEqual(out["value"], 675.0)
        # Sin label personalizado, se muestra «Agregación Campo»
        self.assertEqual(out["compare"]["label"], "Suma Ventas")
        self.assertEqual(out["compare"]["value"], 80.0)
        self.assertEqual(out["compare"]["mode"], "pct")
        self.assertTrue(out["compare"]["better"])

    def test_sin_elegir_comparacion_no_compara(self):
        out = compiled("kpi", self.kpi(
            agg("actual", filters=[{"field": "anio", "op": "eq", "value": 2026}]),
            agg("anterior", filters=[{"field": "anio", "op": "eq", "value": 2025}]),
        ))
        self.assertEqual(out["value"], 675.0)
        self.assertIsNone(out["compare"])

    def test_comparacion_con_alias_inexistente_no_compara(self):
        out = compiled("kpi", self.kpi(
            agg("actual"), agg("anterior"),
        ), {"compare": "inexistente"})
        self.assertIsNone(out["compare"])

    def test_el_numero_principal_puede_ser_otra_metrica(self):
        out = compiled("kpi", self.kpi(
            agg("actual", filters=[{"field": "anio", "op": "eq", "value": 2026}]),
            agg("anterior", filters=[{"field": "anio", "op": "eq", "value": 2025}]),
        ), {"primary": "anterior"})
        self.assertEqual(out["value"], 80.0)
        # Sin label personalizado, se muestra «Agregación Campo»
        self.assertEqual(out["label"], "Suma Ventas")

    def test_comparacion_cuando_menos_es_mejor(self):
        out = compiled("kpi", self.kpi(
            agg("actual", filters=[{"field": "anio", "op": "eq", "value": 2026}]),
            agg("anterior", filters=[{"field": "anio", "op": "eq", "value": 2025}]),
        ), {"compare": "anterior", "higher_is_better": False})
        self.assertFalse(out["compare"]["better"])

    def test_meta_y_semaforo(self):
        out = compiled("kpi", self.kpi(agg("actual")),
                       {"targetMetric": "fixed", "target": 1000, "status_good": 100, "status_warn": 60})
        self.assertEqual(out["target"]["label"], "Meta")
        self.assertEqual(out["target"]["value"], 1000.0)
        self.assertEqual(out["status"], "warn")

    def test_semaforo_por_valor_sin_meta(self):
        out = compiled("kpi", self.kpi(agg("actual")),
                       {"higher_is_better": False, "status_good": 700, "status_warn": 800})
        self.assertIsNone(out["target"])
        self.assertEqual(out["status"], "warn")

    def test_formato_prefijo_abreviacion_y_decimales(self):
        out = compiled("kpi", self.kpi(agg("actual")), {"prefix": "RD$ ", "abbreviate": True})
        self.assertEqual(out["formatted_value"], "RD$ 755")

    def test_los_defaults_del_estilo_vienen_de_backend(self):
        out = render("kpi", self.kpi(agg("actual")))
        self.assertEqual(out["widget_form"]["style"]["decimals"], 0)
        self.assertEqual(out["widget_form"]["style"]["compareMode"], "pct")


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


class TableCompileTests(SimpleTestCase):
    def test_columnas_con_tipo_y_tope_de_filas(self):
        out = compiled("table", table_fields(["categoria", "ventas"]))
        self.assertEqual(out["columns"], [
            {"header": "Categoria", "field": "categoria", "numeric": False},
            {"header": "Ventas", "field": "ventas", "numeric": True},
        ])
        self.assertEqual(out["rows"][0], {"categoria": "Hogar", "ventas": 100.0})
        self.assertFalse(out["truncated"])
        self.assertEqual(out["total_rows"], 6)


class FilterCompileTests(SimpleTestCase):
    def test_un_control_por_columna(self):
        out = compiled("filter", fields(dimensions=["anio", "categoria"], metrics=[]))
        self.assertEqual(out["filters"], [
            {"field": "anio", "label": "Anio", "type": "multi_select",
             "options": [2025, 2026], "truncated": False},
            {"field": "categoria", "label": "Categoria", "type": "multi_select",
             "options": ["Electrónica", "Hogar", "Ropa"], "truncated": False},
        ])

    def test_sin_columnas_elegidas_expone_tod_las_de_la_hoja(self):
        out = compiled("filter", fields(dimensions=[], metrics=[]))
        self.assertEqual([f["field"] for f in out["filters"]], ["categoria", "mes", "anio", "ventas"])


class RenderContractTests(SimpleTestCase):
    def test_todo_widget_devuelve_render_data_y_widget_form(self):
        for key, spec in [
            ("bar", fields()),
            ("line", fields(dimensions=["mes"])),
            ("donut", fields()),
            ("kpi", fields(dimensions=[], metrics=[agg("total_ventas")])),
            ("dynamic_table", fields()),
            ("table", table_fields(["categoria"])),
            ("filter", fields(dimensions=["mes"], metrics=[])),
        ]:
            with self.subTest(widget=key):
                out = render(key, spec)
                self.assertIn("render_data", out)
                self.assertIn("widget_form", out)
                self.assertNotIn("error", out)

    def test_un_error_no_tumba_el_form(self):
        out = render("bar", fields(dimensions=["categoria"],
                                   metrics=[{"field": "no_existe", "agg": "sum", "alias": "x"}]))
        self.assertIn("error", out)
        self.assertIn("widget_form", out)

    def test_los_estilos_se_completan_con_los_defaults(self):
        out = render("bar", fields(), {})
        self.assertEqual(out["widget_form"]["style"]["showGrid"], True)
        self.assertEqual(out["widget_form"]["style"]["barWidth"], 70)

    def test_el_error_de_una_hoja_sin_filas_no_lanza(self):
        out = compiled("bar", fields(), {}, df=sales_df().head(0))
        self.assertEqual(out["categories"], [])
        self.assertEqual(out["series"], [])

    def test_kpi_sobre_una_hoja_vacia(self):
        out = compiled("kpi", fields(dimensions=[], metrics=[agg("total_ventas")]),
                       {}, df=sellers_df().head(0))
        self.assertEqual(out["value"], 0)
        self.assertIsNone(out["compare"])
