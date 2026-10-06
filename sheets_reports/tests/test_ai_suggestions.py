"""Pedidos sugeridos por la IA para el chat del panel: limpieza de la respuesta y caché."""
from unittest import mock

from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from sheets_reports.services import ai_suggestions
from sheets_reports.services.ai_suggestions import widget_suggestions
from sheets_reports.tests.fixtures import sales_df
from sheets_reports.widgets import WIDGETS

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


@override_settings(CACHES=LOCMEM)
class WidgetSuggestionsTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.kpi = WIDGETS.get("kpi")

    def suggest(self, answer, df=None, widget=None):
        with mock.patch.object(ai_suggestions, "_ask_model", return_value=answer) as model:
            result = widget_suggestions(widget or self.kpi, sales_df() if df is None else df, "abc:0")
        return result, model

    def test_limpia_la_respuesta_y_devuelve_dos(self):
        result, _ = self.suggest(["  «Ventas de Hogar vs total»  ", 7, "", "ventas de hogar vs total",
                                  "x" * 81, "total de ventas por mes", "Una tercera"])
        self.assertEqual(result, ["Ventas de Hogar vs total", "Total de ventas por mes"])

    def test_el_prompt_lleva_el_tipo_las_cabeceras_y_las_primeras_filas(self):
        _, model = self.suggest(["A", "B"])
        contents = model.call_args.args[0]
        self.assertIn("«Tarjeta KPI»", contents)
        self.assertIn("condiciones por métrica", contents)
        self.assertIn('"categoria" (texto)', contents)
        self.assertIn('"ventas" (numérica)', contents)
        self.assertIn("categoria,mes,anio,ventas\nHogar,Ene,2026,100.0", contents)
        self.assertNotIn("Ropa", contents)   # está más abajo de las 3 primeras filas

    def test_la_segunda_vez_sale_de_la_cache(self):
        self.suggest(["A", "B"])
        result, model = self.suggest(["C", "D"])
        model.assert_not_called()
        self.assertEqual(result, ["A", "B"])

    def test_otro_tipo_u_otras_columnas_no_comparten_cache(self):
        self.suggest(["A", "B"])
        result, model = self.suggest(["C", "D"], widget=WIDGETS.get("bar"))
        model.assert_called_once()
        self.assertEqual(result, ["C", "D"])
        changed = sales_df()
        changed.loc[0, "ventas"] = 999
        result, model = self.suggest(["E", "F"], df=changed)
        model.assert_called_once()
        self.assertEqual(result, ["E", "F"])

    def test_filas_posteriores_a_las_primeras_no_cambian_la_cache(self):
        self.suggest(["A", "B"])
        later = sales_df()
        later.loc[len(later) - 1, "ventas"] = 999
        result, model = self.suggest(["C", "D"], df=later)
        model.assert_not_called()
        self.assertEqual(result, ["A", "B"])

    def test_si_la_ia_falla_devuelve_vacio_sin_cachear(self):
        with mock.patch.object(ai_suggestions, "_ask_model", side_effect=RuntimeError("caída")), \
                self.assertLogs(ai_suggestions.logger, "ERROR"):
            self.assertEqual(widget_suggestions(self.kpi, sales_df(), "abc:0"), [])
        result, model = self.suggest(["A", "B"])
        model.assert_called_once()
        self.assertEqual(result, ["A", "B"])
