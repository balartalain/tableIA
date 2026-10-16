"""«Generar con IA»: la lista de widgets que propone la IA, limpia de lo que no se puede crear."""
from unittest import mock

from django.test import SimpleTestCase

from sheets_reports.services import ai_board
from sheets_reports.services.ai_board import clean_plan, propose_board
from sheets_reports.engine.context import SheetContext
from sheets_reports.engine.formulas import apply_calculated_fields
from sheets_reports.services.ai_spec import SpecGenerationError, build_system_prompt, generate_widget_form
from sheets_reports.tests.fixtures import execute, sales_ctx, satisfaction_df


def item(widget_type="bar", title="Ventas por categoría", request="suma de ventas por categoria", **extra):
    return {"widget_type": widget_type, "title": title, "description": "Qué muestra",
            "request": request, **extra}


class CleanPlanTests(SimpleTestCase):
    def test_deja_solo_tipos_que_existen(self):
        plan = clean_plan({"items": [item("area"), item("kpi"), item("bar")], "note": "Sin área: uso barras"})
        self.assertEqual([i["widget_type"] for i in plan["items"]], ["kpi", "bar"])
        self.assertEqual(plan["note"], "Sin área: uso barras")

    def test_un_solo_filtro_y_ninguno_si_ya_hay(self):
        items = [item("filter", request="filtrar por categoria"), item("filter", request="por mes"), item()]
        plan = clean_plan({"items": items})
        self.assertEqual([i["widget_type"] for i in plan["items"]], ["filter", "bar"])
        plan = clean_plan({"items": items}, existing_types=["filter"])
        self.assertEqual([i["widget_type"] for i in plan["items"]], ["bar"])

    def test_filtro_resuelve_dimensiones_desde_columns_o_request(self):
        ctx = sales_ctx()  # campos: ID (col_1), categoria (col_2), mes (col_3), anio (col_4), ventas (col_5)
        # 1. Con columns explícito
        items = [item("filter", request="filtrar", columns=["col_2", "col_3"])]
        plan = clean_plan({"items": items}, ctx=ctx)
        self.assertEqual(plan["items"][0]["fields"], {"dimensions": ["categoria", "mes"]})

        # 2. Con alias en request
        items2 = [item("filter", request="filtro por col_2 y col_3")]
        plan2 = clean_plan({"items": items2}, ctx=ctx)
        self.assertEqual(plan2["items"][0]["fields"], {"dimensions": ["categoria", "mes"]})

        # 3. Con nombre de columna en request
        items3 = [item("filter", request="filtrar por categoria y mes")]
        plan3 = clean_plan({"items": items3}, ctx=ctx)
        self.assertEqual(plan3["items"][0]["fields"], {"dimensions": ["categoria", "mes"]})

    def test_cada_widget_lleva_las_columnas_que_usa(self):
        ctx = sales_ctx()
        items = [item(columns=["col_5", "[col_2]", "col_1", "col_99", "col_2"]), item("kpi")]
        plan = clean_plan({"items": items}, ctx=ctx)
        # Nombres reales, sin la ID, sin las que no existen y sin repetir.
        self.assertEqual(plan["items"][0]["columns"], ["ventas", "categoria"])
        self.assertNotIn("columns", plan["items"][1])

    def test_sin_pedido_no_entra_y_sin_titulo_usa_el_del_tipo(self):
        plan = clean_plan({"items": [item(request=" "), item("donut", title="")]})
        self.assertEqual([(i["widget_type"], i["title"]) for i in plan["items"]], [("donut", "Gráfico de Dona")])

    def test_como_mucho_quince(self):
        self.assertEqual(len(clean_plan({"items": [item()] * 20})["items"]), 15)

    def test_sin_widgets_error_legible(self):
        for raw in (None, [], {"items": []}, {"items": [item("area")]}):
            with self.subTest(raw=raw), self.assertRaisesRegex(SpecGenerationError, "no propuso widgets"):
                clean_plan(raw)


class ProposeBoardTests(SimpleTestCase):
    @mock.patch("sheets_reports.services.ai_spec._audit")
    def test_del_plan_a_una_medida_de_porcentaje_de_texto(self, _audit):
        df = satisfaction_df()
        ctx = SheetContext.from_dataframe(df, "0")
        request = ("Porcentaje de Insatisfecho y Completamente insatisfecho sobre las respuestas "
                   "no nulas de respuesta, incluyendo No aplica, por campus")
        planned = item("bar", title="% insatisfechos por campus", request=request, columns=["col_2", "col_3"])
        with mock.patch.object(ai_board, "generate_json", return_value={"items": [planned]}) as planner:
            plan = propose_board("tablero de satisfacción", ctx)
        for prompt in (planner.call_args.args[0], build_system_prompt()):
            self.assertIn("No propongas promedios de texto", prompt)
            self.assertIn("solo el usuario decide excluirlas", prompt)
            self.assertIn("COUNT([Pregunta])", prompt)
            self.assertIn("COUNT([ID])", prompt)

        invalid = {"widget_type": "bar", "fields": {"dimensions": ["col_2"], "metrics": [
            {"field": "col_3", "agg": "avg", "alias": "promedio"}]}, "style": {}}
        formula = ('SUM(IF([col_3] = "Insatisfecho" OR [col_3] = "Completamente insatisfecho", '
                   '1, 0)) / COUNT([col_3]) * 100')
        valid = {"widget_type": "bar", "calculated_fields": [
            {"name": "% Insatisfechos", "formula": formula, "format": "percent"}],
            "fields": {"dimensions": ["col_2"], "metrics": [
                {"field": "% Insatisfechos", "agg": "auto", "alias": "insatisfechos"}]}, "style": {}}
        entry = plan["items"][0]
        with mock.patch("sheets_reports.services.ai_spec._call_model", side_effect=[
            ("create_widget", invalid), ("create_widget", valid)]) as assistant:
            proposal = generate_widget_form(entry["request"], entry["widget_type"], ctx, columns=entry["columns"])
        self.assertEqual(assistant.call_count, 2)
        self.assertIn("solo se aplica a columnas numéricas", assistant.call_args.args[0])
        ready = apply_calculated_fields(df, proposal["calculated_fields"])
        out = execute(ready, proposal["fields"])
        self.assertEqual(out["data"], [{"campus": "Norte", "insatisfechos": 40.0},
                                       {"campus": "Sur", "insatisfechos": 0.0}])
        metric = proposal["fields"]["metrics"]
        proposal["fields"]["dimensions"] = []
        self.assertEqual(execute(ready, proposal["fields"])["data"]["values"][metric[0]["alias"]], 20.0)
        proposal["fields"]["filters"] = [{"field": "respuesta", "op": "ne", "value": "No aplica"}]
        self.assertAlmostEqual(execute(ready, proposal["fields"])["data"]["values"][metric[0]["alias"]],
                               100 * 2 / 9)

    def test_el_prompt_lleva_catalogo_columnas_y_pedido(self):
        with mock.patch.object(ai_board, "generate_json", return_value={"items": [item()]}) as model:
            plan = propose_board("tablero de ventas", sales_ctx(), existing_types=["filter"])
        contents = model.call_args.args[0]
        self.assertIn("- ranking:", contents)
        self.assertIn('"categoria"', contents)
        self.assertIn("El tablero ya tiene: filter", contents)
        self.assertTrue(contents.rstrip().endswith("tablero de ventas"))
        self.assertEqual(plan["items"][0]["widget_type"], "bar")

    def test_propose_board_con_widgets_existentes(self):
        existing = [{"type": "bar", "title": "Ventas por región"}, {"type": "kpi", "title": "Ventas totales"}]
        with mock.patch.object(ai_board, "generate_json", return_value={"items": [item()]}) as model:
            plan = propose_board("agrega distribución por vendedor", sales_ctx(), existing_types=["bar", "kpi"], existing_widgets=existing)
        contents = model.call_args.args[0]
        self.assertIn("Widgets ya presentes en el tablero:", contents)
        self.assertIn("Ventas por región (bar)", contents)
        self.assertIn("Ventas totales (kpi)", contents)
        self.assertIn("No dupliques gráficos ni métricas idénticas por iniciativa propia", contents)
        self.assertEqual(len(plan["items"]), 1)

    def test_pedido_vacio(self):
        with mock.patch.object(ai_board, "generate_json") as model, self.assertRaises(SpecGenerationError):
            propose_board("  ", sales_ctx())
        model.assert_not_called()

    def test_build_board_batch_success(self):
        ctx = sales_ctx()
        req_items = [
            {"widget_type": "bar", "title": "Ventas por categoría", "request": "suma ventas por categoria"},
            {"widget_type": "kpi", "title": "Total ventas", "request": "total de ventas"},
        ]
        mock_compiled = {
            "items": [
                {
                    "widget_type": "bar",
                    "fields": {
                        "dimensions": ["categoria"],
                        "metrics": [{"agg": "sum", "field": "ventas", "alias": "total_ventas"}],
                    },
                    "style": {"stacked": False},
                },
                {
                    "widget_type": "kpi",
                    "fields": {
                        "metrics": [{"agg": "sum", "field": "ventas", "alias": "kpi_ventas"}],
                    },
                    "style": {},
                },
            ]
        }
        with mock.patch.object(ai_board, "generate_json", return_value=mock_compiled) as mock_gen:
            compiled = ai_board.build_board_batch(req_items, ctx)

        self.assertEqual(len(compiled), 2)
        self.assertEqual(compiled[0]["widget_type"], "bar")
        self.assertEqual(compiled[0]["fields"]["dimensions"], ["categoria"])
        self.assertEqual(compiled[1]["widget_type"], "kpi")
        self.assertEqual(compiled[1]["fields"]["metrics"][0]["alias"], "kpi_ventas")
        mock_gen.assert_called_once()

    def test_build_board_batch_empty_items(self):
        with mock.patch.object(ai_board, "generate_json") as mock_gen:
            res = ai_board.build_board_batch([], sales_ctx())
        self.assertEqual(res, [])
        mock_gen.assert_not_called()
