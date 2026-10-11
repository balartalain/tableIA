"""«Generar con IA»: la lista de widgets que propone la IA, limpia de lo que no se puede crear."""
from unittest import mock

from django.test import SimpleTestCase

from sheets_reports.services import ai_board
from sheets_reports.services.ai_board import clean_plan, propose_board
from sheets_reports.services.ai_spec import SpecGenerationError
from sheets_reports.tests.fixtures import sales_ctx


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
    def test_el_prompt_lleva_catalogo_columnas_y_pedido(self):
        with mock.patch.object(ai_board, "generate_json", return_value={"items": [item()]}) as model:
            plan = propose_board("tablero de ventas", sales_ctx(), existing_types=["filter"])
        contents = model.call_args.args[0]
        self.assertIn("- ranking:", contents)
        self.assertIn('"categoria"', contents)
        self.assertIn("El tablero ya tiene: filter", contents)
        self.assertTrue(contents.rstrip().endswith("tablero de ventas"))
        self.assertEqual(plan["items"][0]["widget_type"], "bar")

    def test_pedido_vacio(self):
        with mock.patch.object(ai_board, "generate_json") as model, self.assertRaises(SpecGenerationError):
            propose_board("  ", sales_ctx())
        model.assert_not_called()
