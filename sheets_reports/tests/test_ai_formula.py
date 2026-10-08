"""«Generar con IA» en el constructor de campos calculados: la IA escribe la fórmula, el motor
la valida (con un reintento) y, si no puede, el usuario recibe su motivo."""
from unittest import mock

from django.test import SimpleTestCase

from sheets_reports.services import ai_formula
from sheets_reports.services.ai_formula import FormulaAIError, build_prompt, generate_formula
from sheets_reports.services.ai_spec import SpecGenerationError
from sheets_reports.tests.fixtures import sales_df

ASK = "sheets_reports.services.ai_formula.generate_json"
SHARE = 'SUM(IF([categoria] = "Hogar", [ventas], 0)) / SUM([ventas]) * 100'


class GenerateFormulaTests(SimpleTestCase):
    def test_devuelve_la_formula_con_su_arbol_y_tipo(self):
        with mock.patch(ASK, return_value={"ok": True, "formula": SHARE, "name": "% Hogar"}) as ask:
            out = generate_formula("qué % de las ventas es de Hogar", sales_df())
        self.assertEqual(out["formula"], SHARE)
        self.assertEqual(out["tree"]["kind"], "bin")
        self.assertEqual((out["name"], out["kind"]), ("% Hogar", "aggregated"))
        self.assertEqual(ask.call_count, 1)
        row = {"ok": True, "formula": "[ventas] * 2", "name": "Doble"}
        with mock.patch(ASK, return_value=row):
            self.assertEqual(generate_formula("el doble de las ventas", sales_df())["kind"], "row")

    def test_si_no_puede_dice_por_que(self):
        with mock.patch(ASK, return_value={"ok": False, "reason": "La hoja no tiene una columna de costos."}):
            with self.assertRaisesMessage(FormulaAIError, "La hoja no tiene una columna de costos."):
                generate_formula("el margen de ganancia", sales_df())

    def test_reintenta_una_vez_con_el_error_del_motor(self):
        answers = [{"ok": True, "formula": "SUM([ventas]) / [anio]"}, {"ok": True, "formula": "SUM([ventas]) / SUM([anio])"}]
        with mock.patch(ASK, side_effect=answers) as ask:
            out = generate_formula("ventas por año", sales_df())
        self.assertEqual(out["formula"], "SUM([ventas]) / SUM([anio])")
        retry = ask.call_args_list[1].args[0]
        self.assertIn("mezcla valores agregados", retry)
        with mock.patch(ASK, return_value={"ok": True, "formula": "SUM([nada])"}) as ask:
            with self.assertRaisesMessage(FormulaAIError, "No pude armar una fórmula válida"):
                generate_formula("algo", sales_df())
        self.assertEqual(ask.call_count, ai_formula.ATTEMPTS)

    def test_errores_de_la_ia_llegan_legibles(self):
        with mock.patch(ASK, side_effect=SpecGenerationError("GEMINI_API_KEY no está configurado.")):
            with self.assertRaisesMessage(FormulaAIError, "GEMINI_API_KEY"):
                generate_formula("algo", sales_df())
        with mock.patch(ASK, side_effect=TimeoutError("lento")):
            with self.assertRaisesMessage(FormulaAIError, "no respondió"):
                generate_formula("algo", sales_df())
        with self.assertRaisesMessage(FormulaAIError, "Describe"):
            generate_formula("  ", sales_df())

    def test_el_pedido_trae_columnas_ejemplos_y_funciones_del_catalogo(self):
        text = build_prompt("algo", sales_df())
        self.assertIn('"categoria" (texto)', text)
        self.assertIn('"ventas" (numérica)', text)
        self.assertIn('"Hogar"', text)
        for name in ("IF", "SUM", "AVG", "COUNT_DISTINCT"):
            self.assertIn(name, text)
