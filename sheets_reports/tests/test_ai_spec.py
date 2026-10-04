"""La IA solo propone el `WidgetForm`: prompt y schema salen de los registros, y la
propuesta pasa siempre por `form_errors` (un reintento con los errores, luego falla)."""
import json
from unittest import mock

import pandas as pd
from django.test import SimpleTestCase

from sheets_reports.dsl.context import SheetContext
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
        self.assertEqual(result["title"], "Ventas por categoría")
        self.assertEqual(result["fields"]["metrics"][0]["field"], "ventas")

    def test_reintenta_una_vez_pasandole_el_error(self, _audit):
        responses = [("create_widget", INVALID_ARGS), ("create_widget", VALID_ARGS)]
        with mock.patch.object(ai_spec, "_call_model", side_effect=responses) as call:
            result = generate_widget_form("ventas por categoría", None, sales_ctx())

        self.assertEqual(call.call_count, 2)
        retry_contents = call.call_args_list[1].args[0]
        self.assertIn("'ventaz' no existe", retry_contents)
        self.assertEqual(result["fields"]["metrics"][0]["field"], "ventas")
        self.assertEqual(_audit.call_count, 2)

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
    def test_enum_de_campos_sale_de_las_columnas_reales(self):
        df = pd.DataFrame({"Carrera": ["A"], "Nota": [90]})
        params = build_tool_parameters(SheetContext.from_dataframe(df, "0"), widget_type=None)
        fields_properties = params["properties"]["fields"]["properties"]

        expected = ["Carrera", "Nota"]
        self.assertEqual(fields_properties["dimensions"]["items"]["enum"], expected)
        self.assertEqual(fields_properties["pivots"]["items"]["enum"], expected)
        self.assertEqual(fields_properties["filters"]["items"]["properties"]["field"]["enum"], expected)
        self.assertEqual(fields_properties["columns"]["items"]["properties"]["field"]["enum"], expected)
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
        self.assertEqual(params["required"], ["widget_type", "title", "fields"])
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
        self.assertIn("- style.stacked: checkbox (Apilado)", prompt)
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
                         "sin dimensiones, sin pivotes, métricas de 1 a 4, sin orden, sin límite, filtros")
        self.assertEqual(ai_spec.capabilities_text(WIDGETS.get("donut")),
                         "dimensiones 1, sin pivotes, métricas 1, orden, límite, filtros")
