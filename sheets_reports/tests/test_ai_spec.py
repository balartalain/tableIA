"""La IA solo propone el `WidgetForm`: prompt y schema salen de los registros, y la
propuesta pasa siempre por `form_errors` (un reintento con los errores, luego falla)."""
import json
from unittest import mock

import pandas as pd
from django.test import SimpleTestCase

from sheets_reports.engine.context import SheetContext
from sheets_reports.services import ai_spec
from sheets_reports.services.ai_spec import (
    SpecGenerationError,
    build_system_prompt,
    build_tool_parameters,
    generate_widget_form,
)
from sheets_reports.tests.fixtures import agg, examples_ctx, sales_ctx
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

        self.assertEqual(fields_of("donut"), {"dimensions", "metrics", "filters", "sort_by", "limit"})
        self.assertEqual(fields_of("kpi"), {"metrics", "filters", "trend_by"})
        self.assertEqual(fields_of("table"), {"columns", "filters", "sort_by", "limit"})
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
                         "dimensiones 1, sin pivotes, métricas 1, orden, límite, filtros, sin ventanas")
        self.assertNotIn("ventanas", ai_spec.capabilities_text(WIDGETS.get("table")))
