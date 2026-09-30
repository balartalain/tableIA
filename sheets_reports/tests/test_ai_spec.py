from unittest import mock

import pandas as pd
from django.test import SimpleTestCase

from sheets_reports.services import ai_spec
from sheets_reports.services.ai_spec import (
    SpecGenerationError,
    build_tool_parameters,
    generate_widget_spec,
)
from sheets_reports.services.sheets import get_sheet_schema
from sheets_reports.tests.fixtures import sales_schema

VALID_ARGS = {
    "widget_type": "bar",
    "title": "Ventas por categoría",
    "data_spec": {
        "dimensions": ["categoria"],
        "pivots": [],
        "metrics": [{"type": "agg", "field": "ventas", "agg": "sum", "as": "total_ventas"}],
        "filters": [],
        "having": [],
        "sort": {"by": "total_ventas", "dir": "desc"},
        "limit": None,
        "trend_by": None,
    },
    "view_options": {"stacked": False, "labels": [{"name": "total_ventas", "label": "Ventas"}]},
}

INVALID_ARGS = {
    **VALID_ARGS,
    "data_spec": {**VALID_ARGS["data_spec"], "metrics": [{"type": "agg", "field": "ventaz", "agg": "sum", "as": "total_ventas"}]},
}


@mock.patch.object(ai_spec, "_audit")
class GenerateWidgetSpecTests(SimpleTestCase):
    def test_spec_valido_a_la_primera(self, _audit):
        with mock.patch.object(ai_spec, "_call_model", return_value=("create_widget", VALID_ARGS)) as call:
            result = generate_widget_spec("ventas por categoría", None, sales_schema(), source="0")

        call.assert_called_once()
        self.assertEqual(result["widget_type"], "bar")
        self.assertEqual(result["data_spec"]["source"], "0")
        self.assertEqual(result["view_spec"]["title"], "Ventas por categoría")
        self.assertEqual(result["view_spec"]["labels"], {"total_ventas": "Ventas"})

    def test_reintenta_una_vez_con_el_error(self, _audit):
        responses = [("create_widget", INVALID_ARGS), ("create_widget", VALID_ARGS)]
        with mock.patch.object(ai_spec, "_call_model", side_effect=responses) as call:
            result = generate_widget_spec("ventas por categoría", None, sales_schema(), source="0")

        self.assertEqual(call.call_count, 2)
        retry_contents = call.call_args_list[1].args[0]
        self.assertIn("'ventaz' no existe", retry_contents)
        self.assertEqual(result["data_spec"]["metrics"][0]["field"], "ventas")
        self.assertEqual(_audit.call_count, 2)

    def test_dos_specs_invalidos_devuelven_error_legible(self, _audit):
        with mock.patch.object(ai_spec, "_call_model", return_value=("create_widget", INVALID_ARGS)) as call:
            with self.assertRaises(SpecGenerationError) as ctx:
                generate_widget_spec("ventas por categoría", None, sales_schema(), source="0")

        self.assertEqual(call.call_count, 2)
        self.assertIn("No se pudo generar un widget válido", str(ctx.exception))

    def test_pivote_con_varias_metricas_no_se_reintenta(self, _audit):
        args = {**VALID_ARGS, "data_spec": {**VALID_ARGS["data_spec"], "pivots": ["mes"], "sort": None, "metrics": [
            {"type": "agg", "field": "ventas", "agg": "sum", "as": "total_ventas"},
            {"type": "agg", "agg": "count", "as": "cantidad"},
        ]}}
        with mock.patch.object(ai_spec, "_call_model", return_value=("create_widget", args)) as call:
            with self.assertRaisesMessage(SpecGenerationError, "Con pivote solo se permite una métrica"):
                generate_widget_spec("ventas y cantidad por categoría y mes", None, sales_schema(), source="0")
        call.assert_called_once()

    def test_la_ia_puede_rechazar(self, _audit):
        with mock.patch.object(ai_spec, "_call_model", return_value=("reject_request", {"reason": "No hay columna de región."})):
            with self.assertRaisesMessage(SpecGenerationError, "No hay columna de región."):
                generate_widget_spec("ventas por región", None, sales_schema(), source="0")

    def test_opciones_del_kpi(self, _audit):
        args = {
            "widget_type": "kpi", "title": "Ventas",
            "data_spec": {**VALID_ARGS["data_spec"], "dimensions": [], "sort": None, "metrics": [
                {"type": "agg", "field": "ventas", "agg": "sum", "as": "actual"},
                {"type": "agg", "field": "ventas", "agg": "avg", "as": "promedio"},
            ]},
            "view_options": {"stacked": False, "labels": [],
                             "kpi": {"primary": "actual", "compare": "promedio", "target_value": 900}},
        }
        with mock.patch.object(ai_spec, "_call_model", return_value=("create_widget", args)):
            view = generate_widget_spec("ventas", "kpi", sales_schema(), source="0")["view_spec"]
        self.assertEqual((view["primary"], view["compare"], view["target"]), ("actual", "promedio", 900))

    def test_audita_prompt_y_spec(self, _audit):
        with mock.patch.object(ai_spec, "_call_model", return_value=("create_widget", VALID_ARGS)):
            generate_widget_spec("ventas por categoría", None, sales_schema(), source="0")
        prompt, widget_type, attempt, tool, args, errors = _audit.call_args.args
        self.assertEqual((prompt, attempt, tool, errors), ("ventas por categoría", 1, "create_widget", []))
        self.assertEqual(args, VALID_ARGS)


class ToolSchemaTests(SimpleTestCase):
    def test_enum_de_campos_sale_de_las_columnas_reales(self):
        df = pd.DataFrame({"Carrera": ["A"], "Nota": [90], "Periodo": ["2026-1"]})
        params = build_tool_parameters(get_sheet_schema(df), widget_type=None)
        data_spec = params["properties"]["data_spec"]["properties"]

        expected = ["Carrera", "Nota", "Periodo"]
        self.assertEqual(data_spec["dimensions"]["items"]["enum"], expected)
        agg_metric = data_spec["metrics"]["items"]["anyOf"][0]
        self.assertEqual(agg_metric["properties"]["type"]["enum"], ["agg"])
        self.assertEqual(agg_metric["properties"]["field"]["enum"], expected)
        self.assertEqual(data_spec["pivots"]["items"]["enum"], expected)
        self.assertEqual(data_spec["filters"]["items"]["properties"]["field"]["enum"], expected)
        self.assertNotIn("source", data_spec)

    def test_tipo_fijado(self):
        params = build_tool_parameters(sales_schema(), widget_type="kpi")
        self.assertEqual(params["properties"]["widget_type"]["enum"], ["kpi"])

    def test_sin_claves_no_soportadas(self):
        text = str(build_tool_parameters(sales_schema(), widget_type=None))
        for key in ("'allOf'", "'if'", "'then'", "'$comment'", "'$schema'", "'maxItems'", "'pattern'"):
            self.assertNotIn(key, text)
