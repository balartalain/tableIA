from django.test import SimpleTestCase

from sheets_reports.tests.fixtures import agg, calc, errors_for, spec
from sheets_reports.tests.fixtures import view as build_view_spec


def kpi_spec(*metrics, **overrides):
    return spec(dimensions=[], metrics=list(metrics), **overrides)


def grouped(name="bajo_plan", **overrides):
    return {
        "type": "grouped", "as": name, "group_by": "categoria",
        "inner": [agg("v")], "inner_having": [{"left": "v", "op": "lt", "right": 200}], "result": "count",
        **overrides,
    }


class ValidateWidgetSpecTests(SimpleTestCase):
    def test_spec_valido(self):
        self.assertEqual(errors_for("bar", spec(pivots=["mes"])), [])
        self.assertEqual(errors_for("table", spec(metrics=[
            agg("cantidad", "count"), agg("promedio_ventas", "avg"),
        ], sort={"by": "cantidad", "dir": "desc"})), [])

    def test_campo_inexistente_en_metrica(self):
        errors = errors_for("bar", spec(metrics=[agg("total", field="ventaz")]))
        self.assertIn("'ventaz' no existe", errors[0])

    def test_campo_inexistente_en_dimension_pivote_y_filtro(self):
        self.assertIn("'region' no existe", errors_for("bar", spec(dimensions=["region"]))[0])
        self.assertIn("'semana' no existe", errors_for("bar", spec(pivots=["semana"]))[0])
        self.assertIn("'pais' no existe", errors_for("bar", spec(
            filters=[{"field": "pais", "op": "eq", "value": "DO"}],
        ))[0])

    def test_metrica_sin_type(self):
        self.assertIn("'type' is a required property",
                      errors_for("bar", spec(metrics=[{"as": "total", "agg": "sum", "field": "ventas"}]))[0])

    def test_tabla_con_pivote_admite_varias_metricas(self):
        self.assertEqual(errors_for("table", spec(pivots=["mes"], metrics=[
            agg("total_ventas"), agg("cantidad", "count", show_as="pct_row"),
        ])), [])

    def test_tabla_admite_varias_filas_y_dos_pivotes(self):
        self.assertEqual(errors_for("table", spec(dimensions=["anio", "categoria"], pivots=["mes"])), [])
        self.assertEqual(errors_for("table", spec(dimensions=["categoria"], pivots=["anio", "mes"])), [])

    def test_graficos_admiten_una_fila_y_un_pivote(self):
        self.assertIn("solo se admite una dimensión", errors_for("bar", spec(dimensions=["anio", "categoria"]))[0])
        self.assertIn("un solo pivote", errors_for("bar", spec(pivots=["anio", "mes"]))[0])

    def test_limites_de_filas_y_pivotes(self):
        self.assertTrue(errors_for("table", spec(dimensions=["anio", "categoria", "mes", "ventas"])))
        self.assertTrue(errors_for("table", spec(pivots=["anio", "mes", "ventas"])))

    def test_columnas_repetidas_entre_filas_y_pivotes(self):
        self.assertIn("pivots: no puede ser la misma columna que la dimensión.",
                      errors_for("table", spec(dimensions=["anio", "categoria"], pivots=["mes", "anio"])))
        self.assertIn("dimensions: no se puede repetir una columna.",
                      errors_for("table", spec(dimensions=["anio", "anio"])))

    def test_pivote_con_varias_metricas_da_mensaje_claro(self):
        errors = errors_for("bar", spec(pivots=["mes"], metrics=[agg("total_ventas"), agg("cantidad", "count")]))
        self.assertEqual(len(errors), 1)
        self.assertIn("Con pivote solo se permite una métrica", errors[0])
        self.assertIn("«mes»", errors[0])

    def test_agregaciones_numericas_rechazan_columna_no_numerica(self):
        for name in ("sum", "min", "max", "median"):
            errors = errors_for("bar", spec(metrics=[agg("valor", name, field="mes")]))
            self.assertIn("no es numérica", errors[0], name)

    def test_count_no_lleva_campo(self):
        self.assertEqual(errors_for("bar", spec(metrics=[agg("cantidad", "count")])), [])
        errors = errors_for("bar", spec(metrics=[{"type": "agg", "as": "cantidad", "agg": "count", "field": "mes"}]))
        self.assertIn("no lleva 'field'", errors[0])

    def test_agg_sin_campo(self):
        errors = errors_for("bar", spec(metrics=[{"type": "agg", "as": "total", "agg": "sum"}]))
        self.assertIn("falta 'field'", errors[0])

    def test_count_distinct_acepta_columna_no_numerica(self):
        self.assertEqual(errors_for("bar", spec(metrics=[agg("meses", "count_distinct", field="mes")])), [])

    def test_show_as_solo_acepta_valores_conocidos(self):
        self.assertEqual(errors_for("table", spec(pivots=["mes"], metrics=[agg("pct", show_as="pct_column")])), [])
        self.assertTrue(errors_for("table", spec(pivots=["mes"], metrics=[agg("pct", show_as="pct_fila")])))

    def test_maximo_cinco_metricas(self):
        metrics = [agg(f"m{i}") for i in range(6)]
        self.assertIn("como máximo 5", errors_for("table", spec(metrics=metrics))[0])

    def test_as_duplicado(self):
        errors = errors_for("table", spec(metrics=[agg("total"), agg("total", "avg")]))
        self.assertIn("'total' está repetido", errors[0])

    def test_as_invalido(self):
        errors = errors_for("bar", spec(metrics=[agg("Total Ventas")]))
        self.assertIn("snake_case", errors[0])

    def test_sort_by_invalido(self):
        errors = errors_for("bar", spec(sort={"by": "anio", "dir": "desc"}))
        self.assertIn("sort.by", errors[0])

    def test_pivote_igual_a_dimension(self):
        self.assertIn("pivots", errors_for("bar", spec(pivots=["categoria"]))[0])

    def test_propiedad_desconocida(self):
        self.assertTrue(errors_for("bar", {**spec(), "code": "import os"}))

    def test_faltan_claves(self):
        data_spec = spec()
        del data_spec["having"]
        self.assertIn("'having' is a required property", errors_for("bar", data_spec)[0])

    def test_source_distinto(self):
        self.assertTrue(errors_for("bar", spec(source="999")))

    def test_kpi_no_admite_dimension(self):
        self.assertIn("no admite dimensión", errors_for("kpi", spec())[0])
        self.assertEqual(errors_for("kpi", spec(dimensions=[])), [])

    def test_kpi_admite_hasta_cuatro_metricas(self):
        self.assertEqual(errors_for("kpi", kpi_spec(agg("a"), agg("b"), agg("c"), agg("d"))), [])
        self.assertIn("como máximo 4", errors_for("kpi", kpi_spec(*[agg(f"m{i}") for i in range(5)]))[0])

    def test_donut_una_metrica_sin_pivote(self):
        self.assertEqual(errors_for("donut", spec(sort={"by": "total_ventas", "dir": "desc"})), [])
        self.assertIn("no admite pivote", errors_for("donut", spec(pivots=["mes"]))[0])
        self.assertTrue(errors_for("donut", spec(metrics=[agg("total_ventas"), agg("cantidad", "count")])))
        self.assertIn("se requiere una dimensión", errors_for("donut", spec(dimensions=[]))[0])

    def test_graficos_requieren_dimension(self):
        self.assertIn("se requiere una dimensión", errors_for("bar", spec(dimensions=[]))[0])


class ConditionTests(SimpleTestCase):
    def check(self, *filters):
        return errors_for("bar", spec(filters=list(filters)))

    def test_operadores_validos(self):
        self.assertEqual(self.check(
            {"field": "mes", "op": "in", "value": ["Ene", "Feb"]},
            {"field": "mes", "op": "not_in", "value": ["Mar"]},
            {"field": "ventas", "op": "between", "value": [10, 200]},
            {"field": "categoria", "op": "contains", "value": "hog"},
            {"field": "mes", "op": "not_empty"},
            {"field": "anio", "op": "eq", "relative": "current_year"},
            {"field": "anio", "op": "gte", "relative": "second_max"},
        ), [])

    def test_comparacion_de_orden_sobre_columna_no_numerica(self):
        self.assertIn("no es numérica", self.check({"field": "mes", "op": "gt", "value": 3})[0])

    def test_orden_contra_texto(self):
        self.assertIn("compara contra un número", self.check({"field": "ventas", "op": "gt", "value": "x"})[0])

    def test_in_requiere_lista(self):
        self.assertIn("lista de valores", self.check({"field": "mes", "op": "in", "value": "Ene"})[0])

    def test_between_requiere_dos_numeros_y_columna_numerica(self):
        self.assertIn("dos números", self.check({"field": "ventas", "op": "between", "value": [1]})[0])
        self.assertIn("columnas numéricas", self.check({"field": "mes", "op": "between", "value": [1, 2]})[0])

    def test_value_y_relative_son_excluyentes(self):
        errors = self.check({"field": "anio", "op": "eq", "value": 2026, "relative": "max"})
        self.assertIn("no ambos", errors[0])

    def test_falta_valor(self):
        self.assertIn("falta 'value'", self.check({"field": "anio", "op": "eq"})[0])

    def test_vacio_no_lleva_valor(self):
        self.assertIn("no lleva valor", self.check({"field": "mes", "op": "is_empty", "value": ""})[0])

    def test_relativo_desconocido(self):
        self.assertTrue(self.check({"field": "anio", "op": "eq", "relative": "mañana"}))

    def test_condiciones_de_una_metrica(self):
        ok = spec(metrics=[agg("v2026", filters=[{"field": "anio", "op": "eq", "value": 2026}])])
        self.assertEqual(errors_for("bar", ok), [])
        bad = spec(metrics=[agg("v2026", filters=[{"field": "mes", "op": "gt", "value": 1}])])
        self.assertIn("metrics[0].filters[0]", errors_for("bar", bad)[0])


class CalcAndGroupedTests(SimpleTestCase):
    def test_calc_entre_metricas_anteriores(self):
        data_spec = spec(metrics=[
            agg("ventas"), agg("cantidad", "count"),
            {"type": "calc", "as": "ticket", "op": "div", "left": "ventas", "right": "cantidad"},
            {"type": "calc", "as": "doble", "op": "mul", "left": "ticket", "right": 2},
        ])
        self.assertEqual(errors_for("table", data_spec), [])

    def test_calc_puede_ir_antes_de_las_metricas_que_usa(self):
        # El orden de la lista es de presentación (ej. el usuario arrastró el cálculo arriba).
        self.assertEqual(errors_for("table", spec(metrics=[calc("ticket", "div", "ventas", 2), agg("ventas")])), [])

    def test_calc_con_referencia_inexistente(self):
        errors = errors_for("table", spec(metrics=[calc("ticket", "div", "ventaz", 2), agg("ventas")]))
        self.assertIn("'ventaz' no es una de las métricas de la lista", errors[0])

    def test_calc_en_ciclo(self):
        errors = errors_for("table", spec(metrics=[
            agg("ventas"), calc("a", "add", "b", "ventas"), calc("b", "mul", "a", 2),
        ]))
        self.assertEqual(errors, ["metrics: las métricas calculadas a, b dependen unas de otras en círculo."])
        self.assertIn("en círculo", errors_for("table", spec(metrics=[calc("a", "add", "a", 1)]))[0])

    def test_calc_no_usa_un_ranking(self):
        errors = errors_for("kpi", kpi_spec(
            grouped("top", result="top", value="v"),
            calc("x", "mul", "top", 2),
        ))
        self.assertIn("devuelve un grupo", errors[0])

    def test_grouped_valido_en_kpi(self):
        self.assertEqual(errors_for("kpi", kpi_spec(grouped())), [])
        self.assertEqual(errors_for("kpi", kpi_spec(grouped(result="top", value="v", inner_having=[]))), [])

    def test_grouped_solo_en_kpi(self):
        errors = errors_for("bar", spec(metrics=[grouped()]))
        self.assertEqual(errors, ["metrics[0].type: las métricas «por grupo» no están disponibles en este tipo de widget."])

    def test_grouped_value_debe_ser_interna(self):
        self.assertIn("necesita 'value'", errors_for("kpi", kpi_spec(grouped(result="sum")))[0])
        self.assertIn("no lleva 'value'", errors_for("kpi", kpi_spec(grouped(value="v")))[0])

    def test_grouped_having_referencia_interna(self):
        errors = errors_for("kpi", kpi_spec(grouped(inner_having=[{"left": "plan", "op": "lt", "right": "v"}])))
        self.assertIn("inner_having[0].left: 'plan' no es una de las métricas", errors[0])

    def test_grouped_no_acepta_el_nombre_having(self):
        errors = errors_for("kpi", kpi_spec({**grouped(), "having": []}))
        self.assertTrue(any("'having' was unexpected" in e for e in errors), errors)


class GroupsAndLimitTests(SimpleTestCase):
    def test_having_con_metricas_del_widget(self):
        ok = spec(metrics=[agg("total_ventas"), agg("cantidad", "count")],
                  having=[{"left": "total_ventas", "op": "gt", "right": "cantidad"}])
        self.assertEqual(errors_for("table", ok), [])
        bad = spec(having=[{"left": "plan", "op": "gt", "right": 0}])
        self.assertIn("having[0].left", errors_for("table", bad)[0])

    def test_limit_requiere_orden_por_metrica(self):
        self.assertEqual(errors_for("bar", spec(
            sort={"by": "total_ventas", "dir": "desc"}, limit={"n": 2, "others": True})), [])
        self.assertIn("Top N", errors_for("bar", spec(limit={"n": 2}))[0])
        self.assertIn("Top N", errors_for("bar", spec(sort={"by": "categoria", "dir": "asc"}, limit={"n": 2}))[0])

    def test_kpi_no_admite_having_limit_ni_sort(self):
        errors = errors_for("kpi", kpi_spec(agg("total"), having=[{"left": "total", "op": "gt", "right": 1}],
                                            sort={"by": "total", "dir": "desc"}, limit={"n": 1}))
        self.assertEqual(len(errors), 3)

    def test_trend_by_solo_en_kpi(self):
        self.assertEqual(errors_for("kpi", kpi_spec(agg("total"), trend_by="mes")), [])
        self.assertIn("no muestra tendencia", errors_for("bar", spec(trend_by="mes"))[0])

    def test_trend_by_necesita_una_metrica_con_tendencia(self):
        errors = errors_for("kpi", kpi_spec(grouped(result="top", value="v", inner_having=[]), trend_by="mes"))
        self.assertIn("ninguna métrica tiene tendencia", errors[0])


class BuildViewSpecTests(SimpleTestCase):
    def test_bar(self):
        view = build_view_spec("bar", spec(pivots=["mes"]), {"stacked": True})
        self.assertEqual(
            {k: view[k] for k in ("widget", "x", "seriesBy", "metrics", "stacked")},
            {"widget": "bar", "x": "categoria", "seriesBy": "mes", "metrics": ["total_ventas"], "stacked": True},
        )

    def test_bar_sin_pivote_nunca_apilado(self):
        self.assertFalse(build_view_spec("bar", spec(), {"stacked": True})["stacked"])

    def test_kpi_por_defecto(self):
        view = build_view_spec("kpi", kpi_spec(agg("total_ventas")), {"labels": {"total_ventas": "Total de ventas"}})
        self.assertEqual(view["primary"], "total_ventas")
        self.assertEqual(view["labels"], {"total_ventas": "Total de ventas"})
        self.assertIsNone(view["compare"])
        self.assertIsNone(view["target"])

    def test_kpi_roles_validos(self):
        data_spec = kpi_spec(agg("actual"), agg("anterior"), agg("meta"))
        view = build_view_spec("kpi", data_spec, {"kpi": {
            "primary": "actual", "compare": "anterior", "compare_mode": "abs", "target": "meta",
            "higher_is_better": False, "status": {"basis": "target_pct", "good": 100, "warn": 80},
        }})
        self.assertEqual(
            {k: view[k] for k in ("primary", "compare", "compare_mode", "target", "higher_is_better", "status")},
            {"primary": "actual", "compare": "anterior", "compare_mode": "abs", "target": "meta",
             "higher_is_better": False, "status": {"basis": "target_pct", "good": 100, "warn": 80}},
        )

    def test_kpi_descarta_referencias_invalidas(self):
        view = build_view_spec("kpi", kpi_spec(agg("actual")), {"kpi": {
            "primary": "otra", "compare": "actual", "target": "nada",
            "status": {"basis": "target_pct", "good": 100, "warn": 80},
        }})
        self.assertEqual((view["primary"], view["compare"], view["target"], view["status"]),
                         ("actual", None, None, None))
        self.assertEqual(build_view_spec("kpi", kpi_spec(agg("actual")), {"kpi": {"target": 5000}})["target"], 5000)

    def test_marca_las_metricas_de_porcentaje(self):
        view = build_view_spec("table", spec(metrics=[
            agg("total_ventas"), agg("porcentaje", show_as="pct_column"),
            {"type": "calc", "as": "variacion", "op": "diff_pct", "left": "total_ventas", "right": 100},
            {"type": "calc", "as": "doble", "op": "mul", "left": "total_ventas", "right": 2},
        ]))
        self.assertEqual(view["percent"], ["porcentaje", "variacion"])
