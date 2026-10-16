"""La IA solo propone el `WidgetForm`: prompt y schema salen de los registros, y la
propuesta pasa siempre por `form_errors`, con reparaciones acotadas si falla."""
import copy
import json
from unittest import mock

import pandas as pd
from django.test import SimpleTestCase

from sheets_reports.engine.context import SheetContext
from sheets_reports.engine.formulas import apply_calculated_fields
from sheets_reports.services import ai_spec
from sheets_reports.services.ai_spec import (
    SpecGenerationError,
    build_system_prompt,
    build_tool_parameters,
    generate_widget_form,
)
from sheets_reports.tests.fixtures import agg, compiled, examples_ctx, sales_ctx, sales_df
from sheets_reports.widgets import WIDGETS

VALID_ARGS = {
    "widget_type": "bar",
    "title": "Ventas por categoría",
    "fields": {
        "dimensions": ["categoria"],
        "pivots": [],
        "metrics": [agg("total_ventas")],
        "filters": [],
        "sort_by": "-total_ventas",
        "limit": None,
    },
    "style": {"stacked": False},
}

INVALID_ARGS = {
    **VALID_ARGS,
    "fields": {**VALID_ARGS["fields"], "metrics": [agg("total_ventas", field="ventaz")]},
}


HOGAR = 'SUM(IF([categoria] = "Hogar", [ventas], 0)) / SUM([ventas]) * 100'


def calculated_ctx() -> SheetContext:
    """La hoja de ventas con un campo por fila y uno agregado de nombre poco claro."""
    df = apply_calculated_fields(sales_df(), [
        {"name": "Doble", "formula": "[ventas] * 2"},
        {"name": "KPI 1", "formula": HOGAR, "format": "percent"}])
    return SheetContext.from_dataframe(df, "0")

@mock.patch.object(ai_spec, "_audit")
class GenerateWidgetFormTests(SimpleTestCase):
    def test_propuesta_valida_a_la_primera(self, _audit):
        with mock.patch.object(ai_spec, "_call_model", return_value=("create_widget", VALID_ARGS)) as call:
            result = generate_widget_form("ventas por categoría", None, sales_ctx())

        call.assert_called_once()
        self.assertEqual(result["widget_type"], "bar")
        self.assertEqual(result["title"], "")   # el título lo pone el usuario
        self.assertEqual(result["fields"]["metrics"][0]["field"], "ventas")

    def test_con_el_widget_actual_la_ia_lo_ajusta_en_vez_de_crear(self, _audit):
        current = {"title": "Ventas", "fields": {"dimensions": ["categoria"], "metrics": [agg()]},
                   "style": {"stacked": True}}
        history = [{"role": "user", "text": "ventas por categoría"},
                   {"role": "assistant", "proposal": {"title": "Ventas", "fields": {"dimensions": ["categoria"]}}},
                   {"role": "user", "text": ""},  # vacío: no aporta contexto
                   "basura"]
        responses = [("create_widget", INVALID_ARGS), ("create_widget", VALID_ARGS)]
        with mock.patch.object(ai_spec, "_call_model", side_effect=responses) as call:
            generate_widget_form("ahora por mes", "bar", sales_ctx(), current=current, history=history)

        for attempt in call.call_args_list:  # también en el reintento con los errores
            contents = attempt.args[0]
            self.assertIn(ai_spec.MODIFY_PROMPT, contents)
            self.assertIn('current_widget:\n{"fields": {"dimensions": ["categoria"]', contents)
            self.assertIn("- Usuario: ventas por categoría", contents)
            self.assertIn('- Tú propusiste: {"fields": {"dimensions": ["categoria"]}}', contents)
            self.assertNotIn("basura", contents)
            self.assertIn("Pedido del usuario:\nahora por mes", contents)

    def test_sin_widget_actual_ni_historial_el_mensaje_no_cambia(self, _audit):
        with mock.patch.object(ai_spec, "_call_model", return_value=("create_widget", VALID_ARGS)) as call:
            generate_widget_form("ventas por categoría", None, sales_ctx())
        contents = call.call_args.args[0]
        self.assertNotIn("current_widget", contents)
        self.assertNotIn("Conversación previa", contents)

    def test_el_historial_se_recorta_a_los_ultimos_mensajes(self, _audit):
        history = [{"role": "user", "text": f"pedido {i}"} for i in range(ai_spec.MAX_HISTORY_MESSAGES + 5)]
        with mock.patch.object(ai_spec, "_call_model", return_value=("create_widget", VALID_ARGS)) as call:
            generate_widget_form("otro", None, sales_ctx(), history=history)
        contents = call.call_args.args[0]
        self.assertNotIn("- Usuario: pedido 0\n", contents)
        self.assertIn(f"- Usuario: pedido {ai_spec.MAX_HISTORY_MESSAGES + 4}", contents)

    def test_un_titulo_devuelto_igual_se_descarta(self, _audit):
        args = {**VALID_ARGS, "title": "Inventado", "style": {"stacked": True, "title": "Otro"}}
        current = {"title": "Mi título", "fields": {"dimensions": ["categoria"]}, "style": {"title": "Mi título"}}
        with mock.patch.object(ai_spec, "_call_model", return_value=("create_widget", args)) as call:
            result = generate_widget_form("apílalo", "bar", sales_ctx(), current=current)
        self.assertEqual(result["title"], "")
        self.assertEqual(result["style"], {"stacked": True})
        self.assertNotIn("Mi título", call.call_args.args[0])   # tampoco lo ve en current_widget

    def test_reintenta_una_vez_pasandole_el_error(self, _audit):
        responses = [("create_widget", INVALID_ARGS), ("create_widget", VALID_ARGS)]
        with mock.patch.object(ai_spec, "_call_model", side_effect=responses) as call:
            result = generate_widget_form("ventas por categoría", None, sales_ctx())

        self.assertEqual(call.call_count, 2)
        retry_contents = call.call_args_list[1].args[0]
        self.assertIn("'ventaz' no existe", retry_contents)
        self.assertEqual(result["fields"]["metrics"][0]["field"], "ventas")
        self.assertEqual(_audit.call_count, 2)

    def test_columnas_que_solo_difieren_en_espacios_usan_el_nombre_real(self, _audit):
        # Encabezado de formulario con doble espacio: la IA lo devuelve con uno solo.
        real = "Probabilidad de  recomendar la UAPA"
        df = pd.DataFrame({"Nivel": ["Grado"], real: [9]})
        ctx = SheetContext.from_dataframe(df, "0")
        args = {"widget_type": "bar", "fields": {
            "dimensions": ["nivel"],
            "metrics": [{"agg": "avg", "alias": "promedio", "field": "Probabilidad de recomendar la UAPA",
                         "filters": [{"field": " Nivel ", "op": "eq", "value": "Grado"}]}],
            "sort_by": "-Probabilidad de recomendar la UAPA",
        }, "style": {}}
        with mock.patch.object(ai_spec, "_call_model", return_value=("create_widget", args)) as call:
            result = generate_widget_form("promedio por nivel", None, ctx)

        call.assert_called_once()
        fields = result["fields"]
        self.assertEqual(fields["dimensions"], ["Nivel"])
        self.assertEqual(fields["metrics"][0]["field"], real)
        self.assertEqual(fields["metrics"][0]["filters"][0]["field"], "Nivel")
        self.assertEqual(fields["sort_by"], f"-{real}")

    def test_la_ia_devuelve_alias_y_se_resuelven_al_nombre_real(self, _audit):
        # Pregunta de Google Forms con corchetes y caracteres especiales
        pregunta = "¿Qué tan satisfecho estás? [Atención al cliente]"
        sede = "Sede / Departamento"
        df = pd.DataFrame({sede: ["Central"], pregunta: [10]})
        ctx = SheetContext.from_dataframe(df, "0")
        self.assertEqual(ctx.alias_for(sede), "col_1")
        self.assertEqual(ctx.alias_for(pregunta), "col_2")

        # El mensaje que ve la IA incluye los alias
        message = ai_spec._user_message("algo", None, ctx)
        self.assertIn('- col_1: "Sede / Departamento" (texto, nominal)', message)
        self.assertIn('- col_2: "¿Qué tan satisfecho estás? [Atención al cliente]" (numérica, discreta', message)

        # La IA responde usando los alias col_1 y col_2
        args = {"widget_type": "bar", "fields": {
            "dimensions": ["col_1"],
            "metrics": [{"agg": "avg", "alias": "promedio_atencion", "field": "col_2",
                         "filters": [{"field": "col_1", "op": "eq", "value": "Central"}]}],
            "sort_by": "-col_2",
        }, "style": {}}
        with mock.patch.object(ai_spec, "_call_model", return_value=("create_widget", args)) as call:
            result = generate_widget_form("promedio por sede", None, ctx)

        call.assert_called_once()
        fields = result["fields"]
        self.assertEqual(fields["dimensions"], [sede])
        self.assertEqual(fields["metrics"][0]["field"], pregunta)
        self.assertEqual(fields["metrics"][0]["filters"][0]["field"], sede)
        self.assertEqual(fields["sort_by"], f"-{pregunta}")

    def test_la_ia_devuelve_alias_en_formula_de_campo_calculado(self, _audit):
        pregunta = "¿Qué tan satisfecho estás? [Atención]"
        df = pd.DataFrame({"sede": ["Central"], pregunta: [10]})
        ctx = SheetContext.from_dataframe(df, "0")
        args = {"widget_type": "bar", "calculated_fields": [
            {"name": "Doble", "formula": "SUM([col_2]) * 2", "format": "number"}],
            "fields": {"dimensions": ["col_1"],
                       "metrics": [{"field": "Doble", "agg": "auto", "alias": "doble"}]}, "style": {}}
        with mock.patch.object(ai_spec, "_call_model", return_value=("create_widget", args)) as call:
            result = generate_widget_form("el doble de la atención", None, ctx)
        call.assert_called_once()
        self.assertEqual(result["calculated_fields"],
                         [{"name": "Doble", "formula": f"SUM([{pregunta}]) * 2", "format": "number"}])
        self.assertEqual(result["fields"]["dimensions"], ["sede"])

    def test_la_ia_propone_un_campo_calculado_y_lo_usa(self, _audit):
        args = {"widget_type": "bar", "calculated_fields": [
            {"name": "Doble", "formula": "SUM(ventas) * 2", "format": "number"}],
            "fields": {"dimensions": ["categoria"],
                       "metrics": [{"field": "Doble", "agg": "auto", "alias": "doble"}]}, "style": {}}
        with mock.patch.object(ai_spec, "_call_model", return_value=("create_widget", args)) as call:
            result = generate_widget_form("el doble de las ventas", None, sales_ctx())
        call.assert_called_once()
        self.assertEqual(result["calculated_fields"],
                         [{"name": "Doble", "formula": "SUM(ventas) * 2", "format": "number"}])
        self.assertEqual(result["fields"]["metrics"][0]["agg"], "auto")

    def test_la_ia_ve_la_formula_de_los_campos_calculados(self, _audit):
        """Aunque el nombre no lo diga («KPI 1»), la IA sabe qué calcula cada campo."""
        message = ai_spec._user_message("algo", None, calculated_ctx())
        self.assertIn('"Doble" (numérica, continua: 50 a 600, 6 valores distintos) — campo calculado por fila: [ventas] * 2', message)
        self.assertIn('"KPI 1" (porcentaje): ' + HOGAR, message)

    def test_no_acepta_un_campo_igual_a_uno_que_ya_existe(self, _audit):
        """La misma fórmula escrita distinto que «KPI 1»: error, y el reintento usa el existente."""
        duplicate = {"widget_type": "bar", "calculated_fields": [
            {"name": "Participación", "format": "percent",
             "formula": 'SUM(IF(categoria = "Hogar", ventas, 0)) / SUM(ventas) * 100'}],
            "fields": {"dimensions": ["mes"], "metrics": [{"field": "Participación", "agg": "auto", "alias": "p"}]},
            "style": {}}
        reuse = {**duplicate, "calculated_fields": [],
                 "fields": {"dimensions": ["mes"], "metrics": [{"field": "KPI 1", "agg": "auto", "alias": "p"}]}}
        with mock.patch.object(ai_spec, "_call_model", side_effect=[("create_widget", duplicate),
                                                                   ("create_widget", reuse)]) as call:
            result = generate_widget_form("participación de Hogar por mes", None, calculated_ctx())
        self.assertEqual(call.call_count, 2)
        self.assertIn("ya existe el campo 'KPI 1' con esa fórmula", call.call_args_list[1].args[0])
        self.assertEqual(result["fields"]["metrics"][0]["field"], "KPI 1")

    def test_admite_hasta_diez_campos_calculados(self, _audit):
        fields = [
            {"name": f"Satisfacción {i}", "format": "percent",
             "formula": 'SUM(IF(categoria = "Hogar", ventas, 0)) / SUM(ventas) * 100'}
            for i in range(1, 6)
        ]
        names, errors = ai_spec._calculated_fields_errors(fields, sales_ctx())
        self.assertEqual(errors, [])
        self.assertEqual(len(names), 5)

        # Exceder el límite de 10
        demasiados = [
            {"name": f"Campo {i}", "format": "percent",
             "formula": 'SUM(IF(categoria = "Hogar", ventas, 0)) / SUM(ventas) * 100'}
            for i in range(1, 12)
        ]
        _, errors_demasiados = ai_spec._calculated_fields_errors(demasiados, sales_ctx())
        self.assertIn("como mucho 10", errors_demasiados[0])

    def test_dos_propuestas_invalidas_dan_error_legible(self, _audit):
        with mock.patch.object(ai_spec, "_call_model", return_value=("create_widget", INVALID_ARGS)) as call:
            with self.assertRaises(SpecGenerationError) as ctx:
                generate_widget_form("ventas por categoría", None, sales_ctx())

        self.assertEqual(call.call_count, 2)
        self.assertIn("No se pudo generar un widget válido", str(ctx.exception))

    def test_pivote_con_varias_metricas_da_error(self, _audit):
        args = {**VALID_ARGS, "fields": {**VALID_ARGS["fields"], "pivots": ["mes"], "metrics": [
            agg("total_ventas"), {"agg": "count", "alias": "cantidad"},
        ]}}
        with mock.patch.object(ai_spec, "_call_model", return_value=("create_widget", args)) as call:
            with self.assertRaisesMessage(SpecGenerationError, "UNA métrica"):
                generate_widget_form("ventas y cantidad por categoría y mes", None, sales_ctx())
        self.assertEqual(call.call_count, 2)

    def test_la_tendencia_solo_por_una_columna_de_tiempo(self, _audit):
        from sheets_reports.services.ai_spec import form_errors
        kpi = {"widget_type": "kpi", "fields": {"metrics": [agg()], "filters": []}, "style": {}}

        def errors(trend_by):
            form = {**kpi, "fields": {**kpi["fields"], "trend_by": trend_by}}
            return form_errors(form, sales_ctx(), "kpi", require_title=False)

        self.assertEqual(errors("mes"), [])
        self.assertEqual(errors("anio"), [])
        self.assertTrue(any("no es una columna de tiempo" in e for e in errors("categoria")))

    def test_el_porcentaje_del_total_y_el_acumulado_solo_con_suma_o_conteo(self, _audit):
        from sheets_reports.services.ai_spec import form_errors

        def errors(agg, window, field="ventas"):
            metric = {"agg": agg, "alias": "m", "window": {"type": window}}
            if field:
                metric["field"] = field
            form = {"widget_type": "bar", "fields": {"dimensions": ["mes"], "metrics": [metric]},
                    "style": {}}
            return form_errors(form, sales_ctx(), "bar", require_title=False)

        self.assertEqual(errors("sum", "percent_of_total"), [])
        self.assertEqual(errors("count", "running_total", field=None), [])
        self.assertEqual(errors("avg", "pct_change"), [])
        for agg, window in (("avg", "percent_of_total"), ("max", "running_total")):
            with self.subTest(agg=agg, window=window):
                self.assertTrue(any("solo vale con agg" in e for e in errors(agg, window)))

    def test_la_ia_puede_rechazar(self, _audit):
        with mock.patch.object(ai_spec, "_call_model",
                               return_value=("reject_request", {"reason": "No hay columna de región."})):
            with self.assertRaisesMessage(SpecGenerationError, "No hay columna de región."):
                generate_widget_form("ventas por región", None, sales_ctx())

    def test_tipo_fijado(self, _audit):
        with mock.patch.object(ai_spec, "_call_model", return_value=("create_widget", VALID_ARGS)) as call:
            with self.assertRaisesMessage(SpecGenerationError, "kpi"):
                generate_widget_form("ventas", "kpi", sales_ctx())
        self.assertEqual(call.call_args.args[2], "kpi")

    def test_estilo_con_claves_que_no_existen_se_rechaza(self, _audit):
        args = {**VALID_ARGS, "style": {"etiquetas": True}}
        with mock.patch.object(ai_spec, "_call_model", return_value=("create_widget", args)):
            with self.assertRaisesMessage(SpecGenerationError, "etiquetas"):
                generate_widget_form("ventas por categoría", None, sales_ctx())

    def test_audita_prompt_y_propuesta(self, _audit):
        with mock.patch.object(ai_spec, "_call_model", return_value=("create_widget", VALID_ARGS)):
            generate_widget_form("ventas por categoría", None, sales_ctx())
        prompt, widget_type, attempt, tool, args, errors = _audit.call_args.args
        self.assertEqual((prompt, attempt, tool, errors), ("ventas por categoría", 1, "create_widget", []))
        self.assertEqual(args, VALID_ARGS)

    def test_una_hoja_vacia_no_llama_a_la_ia(self, _audit):
        ctx = SheetContext.from_dataframe(pd.DataFrame(), "0")
        with self.assertRaisesMessage(SpecGenerationError, "no tiene columnas"):
            generate_widget_form("algo", None, ctx)
        _audit.assert_not_called()


@mock.patch.object(ai_spec, "_audit")
class CorrelationGenerationTests(SimpleTestCase):
    agreement = "Plataforma: [Me resulto fácil aprender a usar esta plataforma.]"

    def mixed_df(self):
        return pd.DataFrame({
            "ID": range(1, 7),
            "satisfaccion": ["Insatisfecho", "Completamente insatisfecho", "Satisfecho",
                             "Completamente satisfecho", "No aplica", "Satisfecho"],
            "aprendizaje": ["Muy bajo", "Bajo", "Alto", "Muy alto", "Bajo", "Alto"],
            self.agreement: ["Totalmente de acuerdo", "De acuerdo", "Desacuerdo",
                             "Totalmente en desacuerdo", "No aplica", "De acuerdo"],
            **{f"monto_{i}": [1.5, 2.5, 3.5, 4.5, 5.5, 6.5] for i in range(67)},
        })

    def proposals(self, ctx):
        style = {"method": "spearman", "lowerOnly": True}
        too_many = {"widget_type": "correlation", "style": style,
                    "fields": {"columns": [{"field": ctx.alias_for(c)} for c in ctx.fields if c != "ID"]}}
        selected = ["satisfaccion", "aprendizaje", self.agreement]
        wrong_order = {"widget_type": "correlation", "style": style,
                       "fields": {"columns": [{"field": ctx.alias_for(c)} for c in selected]},
                       "column_scales": [
                           {"field": ctx.alias_for("satisfaccion"), "scale": "nominal"},
                           {"field": ctx.alias_for("aprendizaje"), "scale": "ordinal",
                            "order": ["Muy bajo", "Bajo", "Alto", "Muy alto"]},
                           {"field": ctx.alias_for(self.agreement), "scale": "ordinal",
                            "order": ["Muy bajo", "Bajo", "Alto", "Muy alto"]},
                       ]}
        repaired = {**wrong_order, "column_scales": []}
        return too_many, wrong_order, repaired

    def test_repara_una_escala_mezclada_tras_reducir_70_columnas(self, _audit):
        df = self.mixed_df()
        ctx = SheetContext.from_dataframe(df, "0")
        original_scales = copy.deepcopy(ctx.scales)
        responses = [("create_widget", args) for args in self.proposals(ctx)]
        with mock.patch.object(ai_spec, "_call_model", side_effect=responses) as call:
            result = generate_widget_form("relaciona satisfacción, aprendizaje y plataforma", "correlation", ctx)
        self.assertEqual(call.call_count, 3)
        self.assertIn("entre 2 y 12", call.call_args_list[1].args[0])
        repair = call.call_args_list[2].args[0]
        self.assertIn(self.agreement, repair)
        for value in ("Totalmente de acuerdo", "De acuerdo", "Desacuerdo", "Totalmente en desacuerdo", "No aplica"):
            self.assertIn(value, repair)
        self.assertIn("Escalas efectivas y valores reales", repair)
        self.assertIn('"scale": "ordinal"', repair)
        self.assertIn("entre 2 y 12", repair)  # recuerda el límite corregido en el segundo intento
        self.assertIn("column_scales[2]", _audit.call_args_list[1].args[-1][0])
        self.assertEqual(_audit.call_args_list[2].args[-1], [])
        self.assertEqual(result["column_scales"], [])
        self.assertEqual(result["style"], {"method": "spearman", "lowerOnly": True})
        self.assertEqual(ctx.scales, original_scales)
        out = compiled("correlation", result["fields"], result["style"], df=df)
        self.assertEqual(len(out["variables"]), 3)  # 1 de satisfacción, 1 de aprendizaje, 1 de acuerdo (todas ordinales al ignorar "No aplica")
        self.assertEqual(sum(v.get("value") == "No aplica" for v in out["variables"]), 0)
        self.assertEqual(out["rows"], 6)

    def test_no_reintenta_mas_si_repite_el_mismo_error_de_escala(self, _audit):
        ctx = SheetContext.from_dataframe(self.mixed_df(), "0")
        _, wrong, _ = self.proposals(ctx)
        with mock.patch.object(ai_spec, "_call_model", return_value=("create_widget", wrong)) as call:
            with self.assertRaisesMessage(SpecGenerationError, "no incluye:") as error:
                generate_widget_form("relaciona las tres variables", "correlation", ctx)
        self.assertEqual(call.call_count, 2)
        self.assertIn("Totalmente de acuerdo", str(error.exception))
        self.assertIn("No aplica", str(error.exception))

    def test_la_reparacion_adicional_tambien_debe_validarse_y_es_la_ultima(self, _audit):
        ctx = SheetContext.from_dataframe(self.mixed_df(), "0")
        too_many, wrong, _ = self.proposals(ctx)
        with mock.patch.object(ai_spec, "_call_model", side_effect=[
            ("create_widget", args) for args in (too_many, wrong, wrong)]) as call:
            with self.assertRaisesMessage(SpecGenerationError, "column_scales[2]"):
                generate_widget_form("relaciona las tres variables", "correlation", ctx)
        self.assertEqual(call.call_count, 3)

    def test_omite_correcciones_identicas_a_las_escalas_efectivas(self, _audit):
        df = pd.DataFrame({"nivel": ["Bajo", "Medio", "Alto"], "monto": [1.5, 2.5, 3.5]})
        df.attrs["column_scales"] = {"nivel": {"scale": "ordinal", "order": ["Bajo", "Medio", "Alto"]}}
        ctx = SheetContext.from_dataframe(df, "0")
        args = {"widget_type": "correlation", "fields": {"columns": [{"field": "col_1"}, {"field": "col_2"}]},
                "column_scales": [{"field": "col_1", "scale": "ordinal", "order": ["bajo", "medio", "alto"]},
                                  {"field": "col_2", "scale": "continuous"}], "style": {}}
        with mock.patch.object(ai_spec, "_call_model", return_value=("create_widget", args)) as call:
            result = generate_widget_form("relaciona nivel y monto", "correlation", ctx)
        call.assert_called_once()
        self.assertIn("escala guardada en la fuente", call.call_args.args[0])
        self.assertEqual(result["column_scales"], [])
        self.assertEqual(ctx.order_of("nivel"), ["Bajo", "Medio", "Alto"])
        out = compiled("correlation", result["fields"], df=df)
        self.assertEqual(out["matrix"][0][1], 1.0)

    def test_conserva_una_correccion_real_y_valida(self, _audit):
        df = pd.DataFrame({"talla": ["S", "M", "L"], "peso": [50.5, 70.5, 90.5]})
        ctx = SheetContext.from_dataframe(df, "0")
        args = {"widget_type": "correlation", "fields": {"columns": [{"field": "col_1"}, {"field": "col_2"}]},
                "column_scales": [{"field": "col_1", "scale": "ordinal", "order": ["S", "M", "L"]}], "style": {}}
        with mock.patch.object(ai_spec, "_call_model", return_value=("create_widget", args)):
            result = generate_widget_form("usa el orden S, M, L y correlaciona con peso", "correlation", ctx)
        self.assertEqual(result["column_scales"], [{"field": "talla", "scale": "ordinal", "order": ["S", "M", "L"]}])
        self.assertEqual(ctx.scale_of("talla"), "nominal")  # la generación no escribe en la fuente


class ToolSchemaTests(SimpleTestCase):
    def test_las_columnas_van_como_texto_sin_enum(self):
        # Con muchas columnas de nombre largo el enum supera el límite de estados de Gemini.
        df = pd.DataFrame({"Carrera": ["A"], "Nota": [90]})
        params = build_tool_parameters(SheetContext.from_dataframe(df, "0"), widget_type=None)
        fields_properties = params["properties"]["fields"]["properties"]
        metric_properties = fields_properties["metrics"]["items"]["properties"]

        for schema in (fields_properties["dimensions"]["items"],
                       fields_properties["pivots"]["items"],
                       fields_properties["filters"]["items"]["properties"]["field"],
                       fields_properties["columns"]["items"]["properties"]["field"],
                       fields_properties["trend_by"],
                       metric_properties["field"],
                       metric_properties["filters"]["items"]["properties"]["field"]):
            self.assertEqual(schema["type"], "string")
            self.assertNotIn("enum", schema)
        text = json.dumps(params, ensure_ascii=False)
        self.assertNotIn("Carrera", text)
        self.assertNotIn("source", fields_properties)

    def test_agregaciones_y_ventanas_que_conoce_el_motor(self):
        properties = build_tool_parameters(sales_ctx(), None)["properties"]["fields"] \
            ["properties"]["metrics"]["items"]["properties"]
        self.assertNotIn("mean", properties["agg"]["enum"])
        self.assertIn("count_distinct", properties["agg"]["enum"])
        self.assertEqual(properties["window"]["properties"]["type"]["enum"], [
            "percent_of_total", "percent_of_row", "running_total", "pct_change",
        ])

    def test_el_nombre_a_mostrar_es_una_propiedad_de_la_metrica(self):
        properties = build_tool_parameters(sales_ctx(), None)["properties"]["fields"] \
            ["properties"]["metrics"]["items"]["properties"]
        self.assertIn("label", properties)
        self.assertIn("Nombre a mostrar", properties["label"]["description"])
        self.assertNotIn("maxLength", properties["label"])   # Gemini no lo admite

    def test_tipo_fijado_restringe_el_enum_y_el_estilo(self):
        params = build_tool_parameters(sales_ctx(), widget_type="kpi")
        self.assertEqual(params["properties"]["widget_type"]["enum"], ["kpi"])
        style = params["properties"]["style"]
        self.assertIn("decimals", style["properties"])
        self.assertNotIn("stacked", style["properties"])

        bar = build_tool_parameters(sales_ctx(), widget_type="bar")
        self.assertEqual(bar["properties"]["widget_type"]["enum"], ["bar"])
        self.assertIn("stacked", bar["properties"]["style"]["properties"])

    def test_tipo_fijado_solo_ofrece_los_campos_que_admite(self):
        """Un dona no puede recibir una tendencia (solo la admite el KPI) ni pivotes."""
        def fields_of(widget_type):
            return set(build_tool_parameters(sales_ctx(), widget_type)
                       ["properties"]["fields"]["properties"])

        self.assertEqual(fields_of("donut"), {"dimensions", "metrics", "filters", "sort_by"})
        self.assertEqual(fields_of("kpi"), {"metrics", "filters", "trend_by"})
        self.assertEqual(fields_of("table"), {"columns", "filters", "sort_by"})
        self.assertIn("limit", fields_of("ranking"))
        self.assertIn("trend_by", fields_of(None))   # sin tipo fijado se ofrece todo

    def test_condiciones_por_metrica_solo_si_admite_varias_metricas(self):
        def metric_properties(widget_type):
            return set(build_tool_parameters(sales_ctx(), widget_type)["properties"]["fields"]
                       ["properties"]["metrics"]["items"]["properties"])

        self.assertNotIn("filters", metric_properties("donut"))
        for widget_type in ("kpi", "bar", "line", "dynamic_table", None):
            with self.subTest(widget_type=widget_type):
                self.assertIn("filters", metric_properties(widget_type))

    def test_el_schema_solo_ofrece_las_ventanas_del_widget(self):
        def metric_properties(widget_type):
            return (build_tool_parameters(sales_ctx(), widget_type)["properties"]["fields"]
                    ["properties"]["metrics"]["items"]["properties"])

        def window_types(widget_type):
            return metric_properties(widget_type)["window"]["properties"]["type"]["enum"]

        self.assertEqual(window_types("line"), ["percent_of_total", "running_total", "pct_change"])
        self.assertEqual(window_types("dynamic_table"), ["percent_of_total", "percent_of_row"])
        self.assertIn("pct_change", window_types(None))
        for widget_type in ("kpi", "donut"):
            with self.subTest(widget_type=widget_type):
                self.assertNotIn("window", metric_properties(widget_type))

    def test_los_ejemplos_de_la_ia_solo_usan_campos_que_su_tipo_ofrece(self):
        for key, widget in WIDGETS.items():
            offered = set(build_tool_parameters(examples_ctx(), key)
                          ["properties"]["fields"]["properties"])
            for _prompt, args in widget.ai_examples:
                with self.subTest(widget=key, prompt=_prompt):
                    used = {k for k, v in args["fields"].items() if v not in (None, [], "")}
                    self.assertLessEqual(used, offered)

    def test_la_ia_no_genera_el_titulo(self):
        for widget_type in (None, "bar", "kpi"):
            with self.subTest(widget_type=widget_type):
                params = build_tool_parameters(sales_ctx(), widget_type)
                self.assertNotIn("title", params["properties"])
                self.assertNotIn("title", params["properties"]["style"].get("properties", {}))
        prompt = build_system_prompt()
        self.assertNotIn("- style.title:", prompt)
        self.assertNotIn('"title":', prompt)   # ni en los ejemplos

    def test_con_columnas_asignadas_la_ia_ve_solo_esas_y_el_reintento_todas(self):
        responses = [("create_widget", INVALID_ARGS), ("create_widget", VALID_ARGS)]
        with mock.patch.object(ai_spec, "_call_model", side_effect=responses) as call:
            generate_widget_form("ventas por categoría", "bar", sales_ctx(),
                                 columns=["categoria", "ventas", "no_existe"])
        first, retry = (attempt.args[0] for attempt in call.call_args_list)
        self.assertIn("usa solo estas", first)
        self.assertIn('"categoria" (texto, nominal)', first)
        self.assertIn('"ventas" (numérica, continua', first)
        self.assertIn('"ID" (numérica)', first)   # la de sistema siempre está
        self.assertNotIn('"mes"', first)
        self.assertNotIn('"anio"', first)
        # Si el plan omitió una columna, el reintento la tiene.
        self.assertIn('"mes" (texto, ordinal', retry)
        self.assertNotIn("usa solo estas", retry)

    def test_con_el_tipo_fijado_el_prompt_solo_trae_ese_widget(self):
        from sheets_reports.services.ai_spec import system_prompt_for
        full, kpi = system_prompt_for(None), system_prompt_for("kpi")
        self.assertEqual(full, build_system_prompt())
        self.assertLess(len(kpi), len(full) * 0.7)
        self.assertIn("- kpi:", kpi)
        self.assertNotIn("- bar:", kpi)
        self.assertNotIn("- dynamic_table:", kpi)

    def test_las_listas_de_valores_repetidas_se_escriben_una_vez(self):
        from sheets_reports.services.ai_spec import columns_context
        from sheets_reports.services.sheets import get_field_samples
        df = pd.DataFrame({
            "Aulas": ["Satisfecho", "Insatisfecho"], "Biblioteca": ["Insatisfecho", "Satisfecho"],
            "Sede": ["Norte", "Sur"],
        })
        ctx = SheetContext.from_dataframe(df, "0", samples=get_field_samples(df))
        context = columns_context(ctx)
        self.assertEqual(context.count('"Satisfecho"'), 1)
        self.assertIn('- V1: ["Satisfecho", "Insatisfecho"]', context)
        self.assertIn('"Aulas" (texto, ordinal: Insatisfecho < Satisfecho) — valores de ejemplo: los de V1', context)
        self.assertIn('"Biblioteca" (texto, ordinal: Insatisfecho < Satisfecho) — valores de ejemplo: los de V1', context)
        self.assertIn('"Sede" (texto, nominal) — valores de ejemplo: ["Norte", "Sur"]', context)

    def test_sin_tipo_el_estilo_es_objeto_abierto(self):
        params = build_tool_parameters(sales_ctx(), widget_type=None)
        self.assertEqual(params["properties"]["widget_type"]["enum"],
                         [w.key for w in WIDGETS if w.ai_enabled])
        self.assertNotIn("properties", params["properties"]["style"])

    def test_sin_claves_que_gemini_no_soporta(self):
        text = json.dumps(build_tool_parameters(sales_ctx(), widget_type="bar"))
        for key in ('"allOf":', '"if":', '"then":', '"$comment":', '"$schema":', '"maxItems":', '"pattern":',
                    '"minimum":', '"maximum":', '"default":'):
            self.assertNotIn(key, text)

    def test_lo_que_exige_la_tool(self):
        params = build_tool_parameters(sales_ctx(), widget_type=None)
        self.assertEqual(params["required"], ["widget_type", "fields"])
        self.assertNotIn("style", params["required"])


class PromptTests(SimpleTestCase):
    def test_describe_a_los_widgets_que_la_ia_puede_proponer(self):
        prompt = build_system_prompt()
        for widget in WIDGETS:
            self.assertEqual(f"- {widget.key}: " in prompt, widget.ai_enabled, widget.key)
        self.assertIn("- kpi: uno o pocos números", prompt)
        self.assertIn("Admite: sin dimensiones, sin pivotes, métricas de 1 a 4, sin orden", prompt)

    def test_declara_los_controles_de_estilo_de_cada_tipo(self):
        prompt = build_system_prompt()
        self.assertIn("- style.stacked: boolean (Apilado)", prompt)
        self.assertIn("- bar:", prompt)
        self.assertIn("select de \"percent\", \"value\"", prompt)

    def test_explica_que_los_alias_llevan_un_nombre_a_mostrar(self):
        prompt = build_system_prompt()
        self.assertIn("`label` (nombre a mostrar", prompt)

    def test_los_ejemplos_son_forms_validos(self):
        ctx = examples_ctx()
        from sheets_reports.services.ai_spec import form_errors
        for widget in WIDGETS:
            for prompt, args in widget.ai_examples:
                with self.subTest(widget=widget.key, prompt=prompt):
                    self.assertEqual(form_errors(args, ctx, None), [])

    def test_widgets_sin_la_ia_no_aparecen(self):
        class SoloUnWidget:
            key = "no_debe_aparecer"
            ai_doc = ""
            capabilities: dict = {}
            style_schema: list = []
            ai_examples: list = []

        prompt = build_system_prompt(widgets=[SoloUnWidget])
        self.assertNotIn("- bar:", prompt)
        self.assertIn("- no_debe_aparecer:", prompt)

    def test_capabilities_text_en_una_linea(self):
        self.assertEqual(ai_spec.capabilities_text(WIDGETS.get("kpi")),
                         "sin dimensiones, sin pivotes, métricas de 1 a 4, sin orden, sin límite, "
                         "filtros, tendencia, condiciones por métrica, sin ventanas")
        self.assertEqual(ai_spec.capabilities_text(WIDGETS.get("donut")),
                         "dimensiones 1, sin pivotes, métricas 1, orden, sin límite, filtros, sin ventanas")
        self.assertNotIn("ventanas", ai_spec.capabilities_text(WIDGETS.get("table")))


class ColumnResolutionTests(SimpleTestCase):
    def test_resuelve_columnas_largas_con_acentos_y_puntuacion_final(self):
        col_real = "Indique su nivel de satisfacción con los siguientes aspectos [Dominio de los facilitadores de las asignaturas impartidas.]"
        df = pd.DataFrame({
            col_real: ["Excelente", "Bueno"],
            "ventas": [100, 200],
        })
        ctx = SheetContext.from_dataframe(df, "0")

        # La IA propone el nombre sin acento y sin el punto final
        col_ia = "Indique su nivel de satisfaccion con los siguientes aspectos [Dominio de los facilitadores de las asignaturas impartidas]"
        form = {
            "widget_type": "bar",
            "title": "",
            "fields": {
                "dimensions": [col_ia],
                "pivots": [],
                "metrics": [{"agg": "count", "field": None, "alias": "conteo"}],
                "filters": [],
                "sort_by": "-conteo",
            },
            "style": {},
        }

        errors = ai_spec.form_errors(form, ctx, "bar", require_title=False)
        self.assertEqual(errors, [])
        # Verifica que se haya resuelto al nombre real de la hoja
        self.assertEqual(form["fields"]["dimensions"][0], col_real)

    @mock.patch("sheets_reports.services.ai_spec.gemini_client")
    def test_generate_with_code_execution(self, mock_client_fn):
        mock_client = mock.Mock()
        mock_client_fn.return_value = mock_client
        mock_part_code = mock.Mock(text=None, inline_data=None, executable_code=mock.Mock(code="print(123)"))
        mock_part_img = mock.Mock(text=None, inline_data=mock.Mock(mime_type="image/png", data=b"png_bytes"), executable_code=None)
        mock_part_text = mock.Mock(text="Resultado: 123", inline_data=None, executable_code=None)
        mock_candidate = mock.Mock(content=mock.Mock(parts=[mock_part_code, mock_part_img, mock_part_text]))
        mock_client.models.generate_content.return_value = mock.Mock(candidates=[mock_candidate], usage_metadata=None)

        res = ai_spec.generate_with_code_execution("fake.csv", "calcular")
        self.assertIn("123", res["text"])
        self.assertEqual(res["code"], ["print(123)"])
        self.assertEqual(len(res["images"]), 1)
        self.assertTrue(res["images"][0].startswith("data:image/png;base64,"))

    @mock.patch("sheets_reports.services.ai_spec.gemini_client")
    def test_generate_with_code_execution_deduplicate_images_and_clean_markdown_images(self, mock_client_fn):
        mock_client = mock.Mock()
        mock_client_fn.return_value = mock_client
        mock_part_img1 = mock.Mock(text=None, inline_data=mock.Mock(mime_type="image/png", data=b"same_bytes"), executable_code=None)
        mock_part_img2 = mock.Mock(text=None, inline_data=mock.Mock(mime_type="image/png", data=b"same_bytes"), executable_code=None)
        mock_part_text = mock.Mock(text="Aquí el gráfico:\n![Gráfico](data:image/png;base64,123)\nConclusión final.", inline_data=None, executable_code=None)
        mock_candidate = mock.Mock(content=mock.Mock(parts=[mock_part_img1, mock_part_img2, mock_part_text]))
        mock_client.models.generate_content.return_value = mock.Mock(candidates=[mock_candidate], usage_metadata=None)

        res = ai_spec.generate_with_code_execution("fake.csv", "grafico de pastel")
        # Verifica desduplicación de imágenes
        self.assertEqual(len(res["images"]), 1)
        # Verifica que la sintaxis de imagen markdown se eliminó del texto
        self.assertNotIn("![Gráfico]", res["text"])
        self.assertIn("Aquí el gráfico:", res["text"])
        self.assertIn("Conclusión final.", res["text"])

    @mock.patch("sheets_reports.services.ai_spec.gemini_client")
    def test_generate_with_code_execution_filters_blank_canvas_images(self, mock_client_fn):
        import base64, struct, zlib
        def make_png(solid=True):
            ihdr = struct.pack('>IIBBBBB', 10, 10, 8, 2, 0, 0, 0)
            ihdr_crc = zlib.crc32(b'IHDR' + ihdr)
            ihdr_chunk = struct.pack('>I', 13) + b'IHDR' + ihdr + struct.pack('>I', ihdr_crc)
            raw = bytearray()
            for y in range(10):
                raw.append(0)
                for x in range(10):
                    raw.extend([255, 255, 255] if solid else [x * 20, y * 20, (x + y) * 10])
            comp = zlib.compress(bytes(raw))
            idat_crc = zlib.crc32(b'IDAT' + comp)
            idat_chunk = struct.pack('>I', len(comp)) + b'IDAT' + comp + struct.pack('>I', idat_crc)
            iend_crc = zlib.crc32(b'IEND')
            iend_chunk = struct.pack('>I', 0) + b'IEND' + struct.pack('>I', iend_crc)
            return b'\x89PNG\r\n\x1a\n' + ihdr_chunk + idat_chunk + iend_chunk

        blank_png = make_png(solid=True)
        chart_png = make_png(solid=False)

        mock_client = mock.Mock()
        mock_client_fn.return_value = mock_client
        mock_part_blank = mock.Mock(text=None, inline_data=mock.Mock(mime_type="image/png", data=blank_png), executable_code=None)
        mock_part_chart = mock.Mock(text=None, inline_data=mock.Mock(mime_type="image/png", data=chart_png), executable_code=None)
        mock_part_text = mock.Mock(text="Aquí está el gráfico de pastel.", inline_data=None, executable_code=None)
        mock_candidate = mock.Mock(content=mock.Mock(parts=[mock_part_blank, mock_part_chart, mock_part_text]))
        mock_client.models.generate_content.return_value = mock.Mock(candidates=[mock_candidate], usage_metadata=None)

        res = ai_spec.generate_with_code_execution("fake.csv", "grafico de pastel")
        # Debe haber descartado el lienzo en blanco preliminar y conservado solo la imagen con datos
        self.assertEqual(len(res["images"]), 1)
        self.assertEqual(res["images"][0], f"data:image/png;base64,{base64.b64encode(chart_png).decode()}")

    @mock.patch("sheets_reports.services.ai_spec.gemini_client")
    def test_generate_widget_insight(self, mock_client_fn):
        mock_client = mock.Mock()
        mock_client_fn.return_value = mock_client
        mock_response = mock.Mock(text="### Hallazgos clave\n- Las ventas aumentaron un **25%**.", usage_metadata=None)
        mock_client.models.generate_content.return_value = mock_response

        res = ai_spec.generate_widget_insight("Ventas por Región", "bar", "Norte: 100, Sur: 200")
        self.assertIn("Hallazgos clave", res)
        self.assertIn("25%", res)
        mock_client.models.generate_content.assert_called_once()
