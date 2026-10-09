"""Validación de un `WidgetForm` contra la hoja y las capacidades del tipo (`form_errors`),
y de las condiciones de filtro (`condition_errors`)."""
from django.test import SimpleTestCase

from sheets_reports.engine.context import SheetContext
from sheets_reports.engine.steps.filter import condition_errors, distinct_values
from sheets_reports.tests.fixtures import agg, errors_for, fields, sales_ctx, sales_df, table_fields


def kpi_fields(*metrics, **overrides):
    return fields(dimensions=[], pivots=[], metrics=list(metrics), **overrides)


class FormErrorsTests(SimpleTestCase):
    def test_form_valido(self):
        self.assertEqual(errors_for("bar", fields()), [])
        self.assertEqual(errors_for("bar", fields(pivots=["mes"])), [])
        self.assertEqual(errors_for("kpi", kpi_fields(agg("actual"), agg("promedio", "avg"))), [])
        self.assertEqual(errors_for("dynamic_table", fields(
            dimensions=["anio", "categoria"], pivots=["mes"],
            metrics=[agg("total"), {"agg": "count", "alias": "filas"}],
        )), [])

    def test_condiciones_por_metrica_solo_con_varias_metricas(self):
        electronica = [{"field": "categoria", "op": "eq", "value": "Electrónica"}]
        self.assertEqual(errors_for("kpi", kpi_fields(
            agg("total"), agg("electronica", filters=electronica))), [])
        self.assertIn("metrics[0].filters: con una sola métrica usa fields.filters.",
                      errors_for("donut", fields(metrics=[agg("total", filters=electronica)])))

    def test_un_solo_numero_no_admite_ventanas(self):
        for w_type in ("pct_change", "percent_of_total"):
            with self.subTest(window=w_type):
                errors = errors_for("kpi", kpi_fields(agg("total"), agg("v", window={"type": w_type})))
                self.assertEqual(len(errors), 1)
                self.assertIn("metrics[1].window: este widget da un solo número", errors[0])
                self.assertIn("campo calculado agregado", errors[0])
                self.assertIn("'latest' y 'previous'", errors[0])
        variacion = agg("variacion", window={"type": "pct_change"})
        self.assertEqual(errors_for("line", fields(dimensions=["mes"], metrics=[variacion])), [])

    def test_ventanas_que_admite_cada_widget(self):
        def with_window(w_type, **extra):
            return fields(metrics=[agg("v", window={"type": w_type})], **extra)

        self.assertEqual(errors_for("line", with_window("running_total", dimensions=["mes"])), [])
        self.assertEqual(errors_for("bar", with_window("pct_change", dimensions=["mes"])), [])
        self.assertIn("metrics[0].window: este widget no admite 'window'; quítalo.",
                      errors_for("donut", with_window("pct_change")))
        # Con pivote solo los porcentajes, que se calculan por celda.
        self.assertEqual(errors_for("bar", with_window("percent_of_row", pivots=["mes"])), [])
        self.assertEqual(errors_for("bar", with_window("percent_of_total", pivots=["mes"])), [])
        self.assertIn("con pivotes 'running_total' no se aplica; usa percent_of_total, percent_of_row",
                      errors_for("bar", with_window("running_total", pivots=["mes"]))[0])
        self.assertIn("con pivotes 'running_total' no se aplica; usa percent_of_total",
                      errors_for("line", with_window("running_total", pivots=["mes"]))[0])
        self.assertIn("'running_total' no está disponible en este widget",
                      errors_for("dynamic_table", with_window("running_total"))[0])

    def test_porcentaje_de_la_fila_necesita_pivotes(self):
        row = fields(metrics=[agg("v", window={"type": "percent_of_row"})])
        self.assertIn("'percent_of_row' reparte cada fila", errors_for("dynamic_table", row)[0])
        self.assertEqual(errors_for("dynamic_table", {**row, "pivots": ["mes"]}), [])

    def test_clave_de_datos_desconocida(self):
        self.assertIn("fields: 'orden' no es un campo de datos válido.",
                      errors_for("bar", {**fields(), "orden": ["ventas"]}))

    def test_las_columnas_solo_las_usa_la_tabla(self):
        self.assertIn("fields.columns: este widget no admite columnas.",
                      errors_for("bar", {**fields(), "columns": ["ventas"]}))

    def test_las_columnas_de_la_tabla(self):
        self.assertEqual(errors_for("table", table_fields(["categoria", "ventas"])), [])
        self.assertEqual(errors_for("table", table_fields([{"field": "ventas", "label": "Monto"}])), [])
        self.assertIn("la columna 'region' no existe",
                      errors_for("table", table_fields(["region"]))[0])
        self.assertIn("está repetida", errors_for("table", table_fields(["mes", "mes"]))[0])
        self.assertIn("'label' (nombre a mostrar) debe ser texto de hasta 80 caracteres",
                      errors_for("table", table_fields([{"field": "ventas", "label": "x" * 81}]))[0])
        self.assertIn("columnas: entre 1 y 50", errors_for("table", table_fields([]))[0])
        # La tabla no agrupa ni resume.
        self.assertIn("dimensiones",
                      errors_for("table", {**table_fields(), "dimensions": ["mes"]})[0])
        self.assertIn("métricas", errors_for("table", {**table_fields(), "metrics": [agg()]})[0])

    def test_columna_inexistente(self):
        self.assertIn("la columna 'region' no existe", errors_for("bar", fields(dimensions=["region"]))[0])
        self.assertIn("la columna 'semana' no existe", errors_for("bar", fields(pivots=["semana"]))[0])
        self.assertIn("'ventaz' no existe", errors_for("bar", fields(metrics=[agg("total", field="ventaz")]))[0])
        self.assertIn("'pais' no existe", errors_for("bar", fields(
            filters=[{"field": "pais", "op": "eq", "value": "DO"}]))[0])

    def test_rangos_de_las_capacidades(self):
        self.assertIn("dimensiones", errors_for("kpi", fields(dimensions=["categoria"]))[0])
        self.assertIn("métricas", errors_for("donut", fields(metrics=[agg("a"), agg("b", "count")]))[0])
        self.assertIn("pivotes: exactamente 0",
                      errors_for("donut", fields(pivots=["mes"]))[0])
        # La barra se dibuja sobre UNA dimensión: la segunda no tiene eje donde caer.
        self.assertIn("dimensiones: entre 0 y 1",
                      errors_for("bar", fields(dimensions=["anio", "categoria"]))[0])
        self.assertEqual(errors_for("bar", fields(dimensions=["categoria"])), [])
        # La línea también usa UN solo eje de categorías.
        self.assertIn("dimensiones: exactamente 1",
                      errors_for("line", fields(dimensions=["anio", "categoria"]))[0])
        self.assertEqual(errors_for("line", fields(dimensions=["mes"])), [])
        self.assertIn("este widget no admite orden", errors_for("kpi", fields(
            dimensions=[], metrics=[agg("total")], sort_by="total_ventas"))[0])

    def test_barras_sin_dimension_comparan_totales(self):
        two = [agg("total_ventas"), {"agg": "count", "alias": "cantidad"}]
        self.assertEqual(errors_for("bar", fields(dimensions=[], metrics=two)), [])
        self.assertIn("al menos 2 métricas", errors_for("bar", fields(dimensions=[]))[0])
        errors = errors_for("bar", fields(dimensions=[], pivots=["mes"], metrics=[agg()]))
        self.assertTrue(any("sin dimensión no hay grupos que cruzar" in e for e in errors), errors)
        windowed = [agg(window={"type": "percent_of_total"}), {"agg": "count", "alias": "cantidad"}]
        errors = errors_for("bar", fields(dimensions=[], metrics=windowed))
        self.assertTrue(any("sin agrupar hay un solo valor" in e for e in errors), errors)
        # La línea necesita su eje: sin dimensión se sigue rechazando.
        self.assertIn("dimensiones", errors_for("line", fields(dimensions=[], metrics=two))[0])

    def test_pivote_y_dimension_no_se_solapan(self):
        errors = errors_for("dynamic_table", fields(dimensions=["categoria"], pivots=["categoria"]))
        self.assertIn("no puede estar también en las dimensiones", errors[0])

    def test_un_pivote_con_varias_metricas_en_los_graficos(self):
        errors = errors_for("bar", fields(pivots=["mes"], metrics=[agg("a"), {"agg": "count", "alias": "b"}]))
        self.assertIn("con pivotes los gráficos admiten UNA métrica", errors[0])

    def test_agregacion_inexistente_y_alias(self):
        self.assertIn("agg 'promediar' no existe", errors_for("bar",
                      fields(metrics=[{"field": "ventas", "agg": "promediar", "alias": "x"}]))[0])
        self.assertIn("falta 'alias'", errors_for("bar", fields(metrics=[{"field": "ventas", "agg": "sum"}]))[0])
        self.assertIn("alias 'Total Ventas'", errors_for("bar",
                      fields(metrics=[{"field": "ventas", "agg": "sum", "alias": "Total Ventas"}]))[0])
        self.assertIn("repetido", errors_for("dynamic_table",
                      fields(metrics=[agg("total"), agg("total", "avg")]))[0])

    def test_nombre_a_mostrar_de_la_metrica(self):
        # El alias sigue estricto (snake_case); el «nombre a mostrar» es texto libre.
        self.assertEqual(errors_for("bar", fields(metrics=[
            {"field": "ventas", "agg": "sum", "alias": "t", "label": "Costos totales"}])), [])
        self.assertIn("hasta 80 caracteres", errors_for("bar", fields(metrics=[
            {"field": "ventas", "agg": "sum", "alias": "t", "label": "x" * 81}]))[0])
        self.assertIn("hasta 80 caracteres", errors_for("bar", fields(metrics=[
            {"field": "ventas", "agg": "sum", "alias": "t", "label": 42}]))[0])

    def test_agregaciones_numericas_no_sobre_texto(self):
        self.assertIn("solo se aplica a columnas numéricas", errors_for("bar",
                      fields(metrics=[{"field": "categoria", "agg": "sum", "alias": "x"}]))[0])
        self.assertEqual(errors_for("dynamic_table", fields(dimensions=["categoria"], metrics=[
            {"field": "categoria", "agg": "count_distinct", "alias": "categorias"},
        ])), [])

    def test_count_sin_campo_cuenta_filas(self):
        self.assertEqual(errors_for("bar", fields(metrics=[{"agg": "count", "alias": "filas"}])), [])

    def test_campos_calculados_que_propone_la_ia(self):
        share = {"name": "Participación", "formula": "SUM(ventas) / SUM(anio) * 100", "format": "percent"}
        uses = fields(metrics=[{"field": "Participación", "agg": "auto", "alias": "p"}])
        self.assertEqual(errors_for("dynamic_table", uses, calculated_fields=[share]), [])
        # Sin proponerlo, el campo no existe.
        self.assertIn("solo se usa con un campo calculado agregado", errors_for("dynamic_table", uses)[0])
        cases = {
            "por fila": ({**share, "formula": "ventas * 2"}, "debe ser agregada"),
            "mezcla": ({**share, "formula": "SUM(ventas) / anio"}, "mezcla"),
            "nombre de columna": ({**share, "name": "ventas"}, "ya existe una columna"),
            "formato": ({**share, "format": "moneda"}, "format debe ser"),
        }
        for case, (item, message) in cases.items():
            with self.subTest(case=case):
                errors = errors_for("dynamic_table", uses, calculated_fields=[item])
                self.assertTrue(any(message in e for e in errors), errors)
        # Las métricas de cálculo entre métricas ya no existen.
        old = fields(metrics=[agg("total"), {"type": "formula", "alias": "d", "expression": "total * 2"}])
        self.assertIn("metrics[1]", errors_for("dynamic_table", old)[0])

    def test_orden_y_limite(self):
        self.assertIn("'nada' no es una columna ni un alias",
                      errors_for("dynamic_table", fields(sort_by="nada"))[0])
        self.assertIn("debe ser un número entre 1",
                      errors_for("ranking", fields(limit=99999))[0])
        for widget_type in ("bar", "line", "donut", "dynamic_table"):
            with self.subTest(widget=widget_type):
                self.assertIn("fields.limit: este widget no admite límite.",
                              errors_for(widget_type, fields(limit=5)))

    def test_filtros_propios(self):
        self.assertEqual(errors_for("bar", fields(
            filters=[{"field": "anio", "op": "eq", "value": 2026}])), [])
        self.assertIn("este widget no admite filtros", errors_for("filter", fields(
            dimensions=["mes"], metrics=[],
            filters=[{"field": "anio", "op": "eq", "value": 2026}]))[0])


class StyleSchemaErrorsTests(SimpleTestCase):
    def test_clave_de_estilo_fuera_del_schema(self):
        self.assertIn("style: 'etiquetas' no es un control de este widget.",
                      errors_for("bar", fields(), {"etiquetas": True}))

    def test_select_numero_checkbox_texto(self):
        self.assertIn("style.color_scheme", errors_for("bar", fields(), {"color_scheme": "arcoiris"})[0])
        self.assertIn("style.pageSize", errors_for("table", table_fields(["mes"]), {"pageSize": "diez"})[0])
        self.assertIn("style.showPagination", errors_for("table", table_fields(["mes"]),
                                                        {"showPagination": 1})[0])
        self.assertIn("style.title", errors_for("table", table_fields(["mes"]), {"title": 7})[0])
        self.assertIn("style.seriesOrder", errors_for("bar", fields(), {"seriesOrder": "A,B"})[0])

    def test_estilo_valido_no_da_errores(self):
        self.assertEqual(errors_for("bar", fields(), {"stacked": True, "showGrid": False}), [])
        self.assertEqual(errors_for("bar", fields(), {"seriesOrder": ["B", "A"]}), [])
        self.assertEqual(errors_for("table", table_fields(["mes"]), {"pageSize": 25}), [])


class TitleAndTypeTests(SimpleTestCase):
    def test_titulo_obligatorio(self):
        self.assertIn("title:", errors_for("bar", fields(), title="  ")[0])

    def test_tipo_desconocido(self):
        from sheets_reports.services.ai_spec import form_errors
        errors = form_errors({"widget_type": "tabla", "title": "x", "fields": {}, "style": {}},
                             sales_ctx(), None)
        self.assertIn("widget_type", errors[0])

    def test_tipo_distinto_del_que_se_pide(self):
        from sheets_reports.services.ai_spec import form_errors
        errors = form_errors({"widget_type": "bar", "title": "x", "fields": fields(), "style": {}},
                             sales_ctx(), "kpi")
        self.assertEqual(errors, ["widget_type: debe ser 'kpi'."])


class ConditionTests(SimpleTestCase):
    def errors(self, cond, ctx=None):
        return condition_errors([cond], ctx or sales_ctx(), path="filters")

    def test_condicion_valida(self):
        self.assertEqual(self.errors({"field": "anio", "op": "eq", "value": 2026}), [])
        self.assertEqual(self.errors({"field": "categoria", "op": "in", "value": ["Hogar", "Ropa"]}), [])
        self.assertEqual(self.errors({"field": "mes", "op": "is_empty"}), [])

    def test_columna_inexistente(self):
        self.assertIn("'pais' no existe", self.errors({"field": "pais", "op": "eq", "value": "DO"})[0])

    def test_operador_desconocido(self):
        self.assertIn("op", self.errors({"field": "anio", "op": "like", "value": "2"})[0])

    def test_in_requiere_lista(self):
        self.assertIn("lista de valores", self.errors({"field": "categoria", "op": "in", "value": "Hogar"})[0])

    def test_between_requiere_dos_numeros_y_columna_numerica(self):
        self.assertIn("dos números", self.errors({"field": "ventas", "op": "between", "value": [1]})[0])
        self.assertIn("numéricas", self.errors({"field": "categoria", "op": "between", "value": [1, 2]})[0])

    def test_orden_contra_texto(self):
        self.assertIn("no es numérica", self.errors({"field": "categoria", "op": "lt", "value": 5})[0])

    def test_periodos_solo_en_columnas_de_tiempo(self):
        for relative in ("latest", "previous", "earliest"):
            with self.subTest(relative=relative):
                errors = self.errors({"field": "categoria", "op": "eq", "relative": relative})
                self.assertIn(f"'{relative}' solo aplica a columnas de tiempo", errors[0])
                self.assertEqual(self.errors({"field": "mes", "op": "eq", "relative": relative}), [])
                self.assertEqual(self.errors({"field": "anio", "op": "eq", "relative": relative}), [])
        # Los del reloj no dependen de la columna.
        self.assertEqual(self.errors({"field": "anio", "op": "eq", "relative": "current_year"}), [])

    def test_value_y_relative_son_excluyentes(self):
        self.assertIn("no ambos", self.errors(
            {"field": "anio", "op": "eq", "value": 2026, "relative": "latest"})[0])

    def test_valor_faltante(self):
        self.assertIn("falta 'value'", self.errors({"field": "anio", "op": "eq"})[0])
        self.assertIn("no lleva valor", self.errors({"field": "anio", "op": "is_empty", "value": 1})[0])

    def test_relativo_desconocido(self):
        self.assertIn("relative", self.errors({"field": "anio", "op": "eq", "relative": "el_anteanoche"})[0])

    def test_tope_de_valores_en_lista(self):
        big = [{"field": "categoria", "op": "in", "value": [f"v{i}" for i in range(300)]}]
        self.assertTrue(condition_errors(big, sales_ctx(), path="filters", max_in_values=200))

    def test_distinct_values_normaliza_como_las_compara_el_filtro(self):
        import pandas as pd
        self.assertEqual(
            distinct_values(pd.Series([" Hogar", "Hogar ", "Ropa", None, "  ", "Árbol"])),
            ["Árbol", "Hogar", "Ropa"],
        )
        self.assertEqual(distinct_values(pd.Series([2026.0, None, 2025.0])), [2025, 2026])


class SheetContextTests(SimpleTestCase):
    def test_columnas_numericas_y_muestras(self):
        ctx = sales_ctx()
        self.assertEqual(list(ctx.fields), ["categoria", "mes", "anio", "ventas"])
        self.assertTrue(ctx.is_numeric("ventas"))
        self.assertFalse(ctx.is_numeric("categoria"))

    def test_context_desde_un_dataframe_cualquiera(self):
        ctx = SheetContext.from_dataframe(sales_df().head(1), "7")
        self.assertEqual(ctx.source, "7")
