"""«Generar con IA»: la lista de widgets que propone la IA, configurados con el asistente del
widget antes de mostrarla; los que no se pueden configurar se quitan."""
from unittest import mock

from django.test import SimpleTestCase

from sheets_reports.services import ai_board
from sheets_reports.services.ai_board import clean_plan, propose_board
from sheets_reports.engine.context import SheetContext
from sheets_reports.engine.formulas import apply_calculated_fields
from sheets_reports.services.ai_spec import SpecGenerationError, build_system_prompt
from sheets_reports.tests.fixtures import execute, sales_ctx, satisfaction_df

# sales_ctx: ID (col_1), categoria (col_2), mes (col_3), anio (col_4), ventas (col_5).
SALES = [{"agg": "sum", "field": "ventas", "alias": "total_ventas"}]
FORMS = {
    "bar": {"fields": {"dimensions": ["categoria"], "metrics": SALES}, "style": {}},
    "donut": {"fields": {"dimensions": ["mes"], "metrics": SALES}, "style": {}},
    "kpi": {"fields": {"metrics": SALES}, "style": {}},
}


def item(widget_type="bar", title="Ventas por categoría", request="suma de ventas por categoria", **extra):
    return {"widget_type": widget_type, "title": title, "description": "Qué muestra",
            "request": request, **extra}


def assistant(prompt, widget_type, ctx, columns=None):
    """El asistente del widget, de mentira: el formulario de su tipo."""
    return {"widget_type": widget_type, "calculated_fields": [], "column_scales": [], **FORMS[widget_type]}


class CleanPlanTests(SimpleTestCase):
    def types(self, raw, **kwargs):
        return [p["widget"].key for p in clean_plan(raw, sales_ctx(), **kwargs)["items"]]

    def test_deja_solo_tipos_que_existen(self):
        plan = clean_plan({"items": [item("area"), item("kpi"), item("bar")], "note": "Sin área: uso barras"},
                          sales_ctx())
        self.assertEqual([p["widget"].key for p in plan["items"]], ["kpi", "bar"])
        self.assertEqual(plan["note"], "Sin área: uso barras")

    def test_un_solo_filtro_y_ninguno_si_ya_hay(self):
        items = [item("filter", request="filtrar por categoria"), item("filter", request="por mes"), item()]
        self.assertEqual(self.types({"items": items}), ["filter", "bar"])
        self.assertEqual(self.types({"items": items}, existing_types=["filter"]), ["bar"])

    def test_cada_widget_lleva_las_columnas_que_usa(self):
        plan = clean_plan({"items": [item(columns=["col_5", "[col_2]", "col_1", "col_99", "col_2"]), item("kpi")]},
                          sales_ctx())
        # Nombres reales, sin la ID, sin las que no existen y sin repetir.
        self.assertEqual(plan["items"][0]["columns"], ["ventas", "categoria"])
        self.assertEqual(plan["items"][1]["columns"], [])

    def test_filtro_resuelve_sus_columnas_desde_columns_o_request(self):
        for planned in (item("filter", request="filtrar", columns=["col_2", "col_3"]),
                        item("filter", request="filtro por col_2 y col_3"),
                        item("filter", request="filtrar por categoria y mes")):
            with self.subTest(planned=planned):
                [p] = clean_plan({"items": [planned]}, sales_ctx())["items"]
                self.assertEqual(p["columns"], ["categoria", "mes"])

    def test_sin_pedido_no_entra(self):
        self.assertEqual(self.types({"items": [item(request=" "), item("donut")]}), ["donut"])

    def test_como_mucho_quince(self):
        self.assertEqual(len(clean_plan({"items": [item()] * 20}, sales_ctx())["items"]), 15)


class ProposeBoardTests(SimpleTestCase):
    def setUp(self):
        # Sin escribir en el log de auditoría real.
        patcher = mock.patch.object(ai_board, "_audit")
        patcher.start()
        self.addCleanup(patcher.stop)

    def propose(self, raw, prompt="tablero de ventas", side_effect=assistant, **kwargs):
        with mock.patch.object(ai_board, "generate_json", return_value=raw) as self.model, \
                mock.patch.object(ai_board, "generate_widget_form", side_effect=side_effect) as self.assistant:
            return propose_board(prompt, sales_ctx(), **kwargs)

    def test_cada_widget_sale_configurado_por_el_asistente(self):
        plan = self.propose({"items": [item("kpi", request="total de ventas", columns=["col_5"]),
                                       item(columns=["col_2", "col_5"])]})
        self.model.assert_called_once()
        calls = sorted((c.args[0], c.args[1], c.kwargs["columns"]) for c in self.assistant.call_args_list)
        self.assertEqual(calls, [("suma de ventas por categoria", "bar", ["categoria", "ventas"]),
                                 ("total de ventas", "kpi", ["ventas"])])
        kpi, bar = plan["items"]
        self.assertEqual((kpi["widget_type"], bar["widget_type"]), ("kpi", "bar"))
        self.assertEqual(bar["fields"]["dimensions"], ["categoria"])
        self.assertEqual(set(bar), {"widget_type", "title", "description", "fields", "style",
                                    "calculated_fields", "column_scales"})
        self.assertEqual(plan["note"], "")

    def test_el_filtro_no_llama_al_asistente(self):
        plan = self.propose({"items": [item("filter", request="filtrar por categoria y mes")]})
        self.assistant.assert_not_called()
        self.assertEqual(plan["items"][0]["fields"], {"dimensions": ["categoria", "mes"]})

    def test_los_que_no_salen_se_quitan_y_solo_se_avisa_si_los_pidio_el_usuario(self):
        def side_effect(prompt, widget_type, ctx, columns=None):
            if widget_type == "donut":
                raise SpecGenerationError("No se pudo generar un widget válido.")
            return assistant(prompt, widget_type, ctx)
        plan = self.propose({"items": [item("kpi"), item("donut", title="Pedido", requested=True),
                                       item("donut", title="Propuesto por la IA", requested=False)],
                             "note": "Ajusté algo."}, side_effect=side_effect)
        self.assertEqual([i["widget_type"] for i in plan["items"]], ["kpi"])
        self.assertEqual(plan["note"], "Ajusté algo. Se quitó «Pedido»: no se pudo configurar "
                                       "(No se pudo generar un widget válido.).")

    def test_si_la_ia_falla_en_un_widget_se_quita(self):
        def side_effect(prompt, widget_type, ctx, columns=None):
            if widget_type == "donut":
                raise RuntimeError("504")
            return assistant(prompt, widget_type, ctx)
        with self.assertLogs("sheets_reports.services.ai_board", "ERROR"):
            plan = self.propose({"items": [item("kpi"), item("donut", title="Dona", requested=True)]},
                                side_effect=side_effect)
        self.assertEqual([i["widget_type"] for i in plan["items"]], ["kpi"])
        self.assertIn("Se quitó «Dona»: no se pudo configurar (la IA no respondió correctamente).", plan["note"])

    def test_columnas_de_cuadricula_que_el_tipo_no_admite_no_llaman_al_asistente(self):
        with mock.patch.object(ai_board, "_grid_problem", return_value="«Dona» no usa columnas de cuadrícula"), \
                self.assertRaisesRegex(SpecGenerationError, "no usa columnas de cuadrícula"):
            self.propose({"items": [item("donut")]})
        self.assistant.assert_not_called()

    def test_un_formulario_invalido_del_asistente_no_entra(self):
        def side_effect(prompt, widget_type, ctx, columns=None):
            return {"fields": {"dimensions": ["categoria"], "metrics": [
                {"agg": "avg", "field": "categoria", "alias": "x"}]}, "style": {}}
        with self.assertRaisesRegex(SpecGenerationError, "Se quitó «Ventas por categoría»"):
            self.propose({"items": [item()]}, side_effect=side_effect)

    def test_un_campo_calculado_del_plan_lo_usan_los_siguientes(self):
        calc = {"name": "Ventas Hogar", "formula": 'SUM(IF([categoria] = "Hogar", [ventas], 0))', "format": "number"}
        uses = [{"agg": "auto", "field": "Ventas Hogar", "alias": "hogar"}]

        def side_effect(prompt, widget_type, ctx, columns=None):
            # Los dos lo proponen igual: el segundo usa el del primero, no lo crea otra vez.
            return {"fields": {"metrics": uses, **({"dimensions": ["mes"]} if widget_type == "bar" else {})},
                    "style": {}, "calculated_fields": [calc]}
        plan = self.propose({"items": [item("kpi"), item("bar")]}, side_effect=side_effect)
        self.assertEqual([len(i["calculated_fields"]) for i in plan["items"]], [1, 0])

    def test_sin_widgets_validos_error_legible(self):
        for raw in (None, [], {"items": []}, {"items": [item("area")]}):
            with self.subTest(raw=raw), self.assertRaisesRegex(SpecGenerationError, "no propuso widgets"):
                self.propose(raw)

    def test_el_prompt_lleva_catalogo_columnas_y_pedido(self):
        self.propose({"items": [item()]}, existing_types=["filter"])
        contents = self.model.call_args.args[0]
        self.assertIn("- ranking:", contents)
        self.assertIn('"categoria"', contents)
        self.assertIn("El tablero ya tiene: filter", contents)
        self.assertIn("NO resumas ni describas el tablero", contents)
        # La misma lectura de una columna de texto que el asistente del widget.
        self.assertIn("la lectura por defecto es contarla", contents)
        self.assertIn("la agregación de cada métrica dicha de forma explícita", contents)
        # Un pedido genérico es un tablero completo, sin kpi que cuenten respuestas de texto, y
        # ninguno de sus widgets cuenta como pedido por el usuario.
        self.assertIn("«Indicadores» no\n  significa solo kpi", contents)
        self.assertIn("nunca el conteo\n  de sus respuestas", contents)
        self.assertIn("En un pedido genérico («un tablero», «los principales indicadores») TODOS son false", contents)
        self.assertTrue(contents.rstrip().endswith("tablero de ventas"))

    def test_propose_board_con_widgets_existentes(self):
        existing = [{"type": "bar", "title": "Ventas por región"}, {"type": "kpi", "title": "Ventas totales"}]
        plan = self.propose({"items": [item()]}, prompt="agrega distribución por vendedor",
                            existing_types=["bar", "kpi"], existing_widgets=existing)
        contents = self.model.call_args.args[0]
        self.assertIn("Widgets ya presentes en el tablero:", contents)
        self.assertIn("Ventas por región (bar)", contents)
        self.assertIn("Ventas totales (kpi)", contents)
        self.assertIn("No dupliques gráficos ni métricas idénticas por iniciativa propia", contents)
        self.assertEqual(len(plan["items"]), 1)

    def test_pedido_vacio(self):
        with self.assertRaises(SpecGenerationError):
            self.propose({"items": [item()]}, prompt="  ")
        self.model.assert_not_called()

    @mock.patch("sheets_reports.services.ai_spec._audit")
    def test_del_plan_a_una_medida_de_porcentaje_de_texto(self, _audit):
        df = satisfaction_df()
        ctx = SheetContext.from_dataframe(df, "0")
        request = ("Porcentaje de Insatisfecho y Completamente insatisfecho sobre las respuestas "
                   "no nulas de respuesta, incluyendo No aplica, por campus")
        planned = item("bar", title="% insatisfechos por campus", request=request, columns=["col_2", "col_3"])
        invalid = {"widget_type": "bar", "fields": {"dimensions": ["col_2"], "metrics": [
            {"field": "col_3", "agg": "avg", "alias": "promedio"}]}, "style": {}}
        formula = ('SUM(IF([col_3] = "Insatisfecho" OR [col_3] = "Completamente insatisfecho", '
                   '1, 0)) / COUNT([col_3]) * 100')
        valid = {"widget_type": "bar", "calculated_fields": [
            {"name": "% Insatisfechos", "formula": formula, "format": "percent"}],
            "fields": {"dimensions": ["col_2"], "metrics": [
                {"field": "% Insatisfechos", "agg": "auto", "alias": "insatisfechos"}]}, "style": {}}
        with mock.patch.object(ai_board, "generate_json", return_value={"items": [planned]}) as planner, \
                mock.patch("sheets_reports.services.ai_spec._call_model", side_effect=[
                    ("create_widget", invalid), ("create_widget", valid)]) as model:
            plan = propose_board("tablero de satisfacción", ctx)
        for prompt in (planner.call_args.args[0], build_system_prompt()):
            self.assertIn("No propongas promedios de texto", prompt)
            self.assertIn("solo el usuario decide excluirlas", prompt)
            self.assertIn("COUNT([Pregunta])", prompt)
            self.assertIn("COUNT([ID])", prompt)
        self.assertEqual(model.call_count, 2)
        self.assertIn("solo se aplica a columnas numéricas", model.call_args.args[0])

        proposal = plan["items"][0]
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
