"""Política vista → datos: las opciones que nombran algo del data_spec se reconcilian con él y
nunca invalidan un spec."""
from django.test import SimpleTestCase

from sheets_reports.tests.fixtures import agg, spec, view


def kpi_view(*metrics, **roles):
    return view("kpi", spec(dimensions=[], metrics=list(metrics)), {"kpi": roles})


class ReconcileTests(SimpleTestCase):
    def test_primary_inexistente_pasa_a_la_primera_metrica(self):
        self.assertEqual(kpi_view(agg("a"), agg("b"), primary="z")["primary"], "a")

    def test_compare_inexistente_o_no_numerica(self):
        self.assertIsNone(kpi_view(agg("a"), compare="z")["compare"])
        top = {"type": "grouped", "as": "top", "group_by": "categoria", "inner": [agg("v")],
               "inner_having": [], "result": "top", "value": "v"}
        self.assertIsNone(kpi_view(agg("a"), top, compare="top")["compare"])

    def test_compare_mode_sin_compare_es_none(self):
        self.assertIsNone(kpi_view(agg("a"), compare_mode="abs")["compare_mode"])
        self.assertEqual(kpi_view(agg("a"), agg("b"), compare="b")["compare_mode"], "pct")
        self.assertEqual(kpi_view(agg("a"), agg("b"), compare="b", compare_mode="abs")["compare_mode"], "abs")

    def test_target_inexistente(self):
        self.assertIsNone(kpi_view(agg("a"), target="z")["target"])
        self.assertEqual(kpi_view(agg("a"), target=500)["target"], 500)

    def test_semaforo_por_meta_sin_meta(self):
        status = {"basis": "target_pct", "good": 100, "warn": 80}
        self.assertIsNone(kpi_view(agg("a"), status=status)["status"])
        self.assertEqual(kpi_view(agg("a"), target=10, status=status)["status"], status)

    def test_stacked_sin_pivote(self):
        self.assertFalse(view("bar", spec(), {"stacked": True})["stacked"])
        self.assertTrue(view("bar", spec(pivots=["mes"]), {"stacked": True})["stacked"])

    def test_labels_de_lo_que_ya_no_existe_se_descartan(self):
        labels = {"total_ventas": "Ventas", "categoria": "Categoría", "vieja": "Ya no está"}
        self.assertEqual(view("dynamic_table", spec(), {"labels": labels})["labels"],
                         {"total_ventas": "Ventas", "categoria": "Categoría"})

    def test_tendencia_y_comparacion_son_independientes(self):
        # Los cuatro estados son válidos: ninguna regla cruza trend_by (datos) con compare (vista).
        for trend_by in (None, "mes"):
            for compare in (None, "b"):
                data_spec = spec(dimensions=[], metrics=[agg("a"), agg("b")], trend_by=trend_by)
                self.assertEqual(view("kpi", data_spec, {"kpi": {"compare": compare}})["compare"], compare)
