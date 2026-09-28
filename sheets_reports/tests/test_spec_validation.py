from django.test import SimpleTestCase

from sheets_reports.services.spec_validation import build_view_spec, validate_widget_spec
from sheets_reports.tests.fixtures import sales_schema, spec


def errors_for(widget_type, data_spec):
    return validate_widget_spec(widget_type, data_spec, sales_schema(), source="0")


class ValidateWidgetSpecTests(SimpleTestCase):
    def test_spec_valido(self):
        self.assertEqual(errors_for("bar", spec(pivot="mes")), [])
        self.assertEqual(errors_for("table", spec(metrics=[
            {"field": "categoria", "agg": "count", "as": "cantidad"},
            {"field": "ventas", "agg": "avg", "as": "promedio_ventas"},
        ], sort={"by": "cantidad", "dir": "desc"})), [])

    def test_campo_inexistente_en_metrica(self):
        errors = errors_for("bar", spec(metrics=[{"field": "ventaz", "agg": "sum", "as": "total"}]))
        self.assertTrue(errors)
        self.assertIn("'ventaz' no existe", errors[0])

    def test_campo_inexistente_en_dimension_pivote_y_filtro(self):
        self.assertIn("'region' no existe", errors_for("bar", spec(dimensions=["region"]))[0])
        self.assertIn("'semana' no existe", errors_for("bar", spec(pivot="semana"))[0])
        self.assertIn("'pais' no existe", errors_for("bar", spec(
            filters=[{"field": "pais", "op": "eq", "value": "DO"}],
        ))[0])

    def test_pivote_con_varias_metricas_da_mensaje_claro(self):
        errors = errors_for("table", spec(pivot="mes", metrics=[
            {"field": "ventas", "agg": "sum", "as": "total_ventas"},
            {"field": "categoria", "agg": "count", "as": "cantidad"},
        ]))
        self.assertEqual(len(errors), 1)
        self.assertIn("Con pivote solo se permite una métrica", errors[0])
        self.assertIn("«mes»", errors[0])

    def test_sum_sobre_columna_no_numerica(self):
        errors = errors_for("bar", spec(metrics=[{"field": "mes", "agg": "sum", "as": "total"}]))
        self.assertIn("no es numérica", errors[0])

    def test_count_acepta_columna_no_numerica(self):
        self.assertEqual(errors_for("bar", spec(metrics=[{"field": "mes", "agg": "count", "as": "cantidad"}])), [])

    def test_comparacion_de_orden_sobre_columna_no_numerica(self):
        errors = errors_for("bar", spec(filters=[{"field": "mes", "op": "gt", "value": 3}]))
        self.assertIn("no es numérica", errors[0])

    def test_filtro_in_requiere_lista(self):
        self.assertTrue(errors_for("bar", spec(filters=[{"field": "mes", "op": "in", "value": "Ene"}])))

    def test_maximo_cinco_metricas(self):
        metrics = [{"field": "ventas", "agg": "sum", "as": f"m{i}"} for i in range(6)]
        self.assertIn("como máximo 5", errors_for("table", spec(metrics=metrics))[0])

    def test_as_duplicado(self):
        errors = errors_for("table", spec(metrics=[
            {"field": "ventas", "agg": "sum", "as": "total"},
            {"field": "ventas", "agg": "avg", "as": "total"},
        ]))
        self.assertIn("repetidos", errors[0])

    def test_as_invalido(self):
        errors = errors_for("bar", spec(metrics=[{"field": "ventas", "agg": "sum", "as": "Total Ventas"}]))
        self.assertIn("snake_case", errors[0])

    def test_sort_by_invalido(self):
        errors = errors_for("bar", spec(sort={"by": "anio", "dir": "desc"}))
        self.assertIn("sort.by", errors[0])

    def test_pivote_igual_a_dimension(self):
        self.assertIn("pivot", errors_for("bar", spec(pivot="categoria"))[0])

    def test_propiedad_desconocida(self):
        self.assertTrue(errors_for("bar", {**spec(), "code": "import os"}))

    def test_source_distinto(self):
        self.assertTrue(errors_for("bar", spec(source="999")))

    def test_kpi_no_admite_dimension(self):
        self.assertIn("no admite dimensión", errors_for("kpi", spec())[0])
        self.assertEqual(errors_for("kpi", spec(dimensions=[])), [])

    def test_donut_una_metrica_sin_pivote(self):
        self.assertEqual(errors_for("donut", spec(sort={"by": "total_ventas", "dir": "desc"})), [])
        self.assertIn("no admite pivote", errors_for("donut", spec(pivot="mes"))[0])
        self.assertTrue(errors_for("donut", spec(metrics=[
            {"field": "ventas", "agg": "sum", "as": "total_ventas"},
            {"field": "categoria", "agg": "count", "as": "cantidad"},
        ])))
        self.assertIn("se requiere una dimensión", errors_for("donut", spec(dimensions=[]))[0])

    def test_graficos_requieren_dimension(self):
        self.assertIn("se requiere una dimensión", errors_for("bar", spec(dimensions=[]))[0])


class BuildViewSpecTests(SimpleTestCase):
    def test_table_con_pivote_usa_pivotOf(self):
        view = build_view_spec("table", spec(pivot="mes"), {"labels": {"total_ventas": "Ventas"}})
        self.assertEqual(view["columns"], [
            {"header": "Categoria", "field": "categoria"},
            {"header": "Ventas", "pivotOf": "total_ventas"},
        ])

    def test_bar(self):
        view = build_view_spec("bar", spec(pivot="mes"), {"stacked": True})
        self.assertEqual(
            {k: view[k] for k in ("widget", "x", "seriesBy", "metric", "stacked")},
            {"widget": "bar", "x": "categoria", "seriesBy": "mes", "metric": "total_ventas", "stacked": True},
        )

    def test_bar_sin_pivote_nunca_apilado(self):
        self.assertFalse(build_view_spec("bar", spec(), {"stacked": True})["stacked"])

    def test_kpi(self):
        view = build_view_spec("kpi", spec(dimensions=[]), {"labels": {"total_ventas": "Total de ventas"}})
        self.assertEqual((view["widget"], view["metric"], view["label"]), ("kpi", "total_ventas", "Total de ventas"))
