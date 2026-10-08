"""Ranking: el top N, los mejores o los peores, empates, el % del total y el resto."""
import pandas as pd
from django.test import SimpleTestCase

from sheets_reports.tests.fixtures import agg, compiled, errors_for, fields, sellers_df


def ranking(df=None, style=None, **overrides):
    data = fields(dimensions=["vendedor"], **overrides)
    return compiled("ranking", data, style, df=sellers_df() if df is None else df)


def labels(out):
    return [item["label"] for item in out["items"]]


class RankingOrderTests(SimpleTestCase):
    def test_sin_orden_muestra_los_mas_altos(self):
        out = ranking()
        self.assertEqual(labels(out), ["Eva", "Luis", "Ana"])
        self.assertEqual([i["rank"] for i in out["items"]], [1, 2, 3])
        self.assertEqual(out["order"], "best")

    def test_los_peores_van_de_menor_a_mayor(self):
        out = ranking(sort_by="total_ventas")
        self.assertEqual(labels(out), ["Ana", "Luis", "Eva"])
        self.assertEqual(out["order"], "worst")

    def test_si_lo_bajo_es_mejor_ascendente_son_los_mejores(self):
        out = ranking(sort_by="total_ventas", style={"higher_is_better": False})
        self.assertEqual(labels(out), ["Ana", "Luis", "Eva"])
        self.assertEqual(out["order"], "best")

    def test_limite_y_por_defecto_diez(self):
        self.assertEqual(labels(ranking(limit=2)), ["Eva", "Luis"])
        df = pd.DataFrame({"vendedor": [f"v{i:02}" for i in range(15)], "ventas": range(15)})
        self.assertEqual(len(ranking(df=df)["items"]), 10)


class RankingTiesAndGapsTests(SimpleTestCase):
    def test_empates_comparten_posicion(self):
        df = pd.DataFrame({"vendedor": ["A", "B", "C", "D"], "ventas": [10.0, 8.0, 8.0, 5.0]})
        out = ranking(df=df)
        self.assertEqual([i["rank"] for i in out["items"]], [1, 2, 2, 4])
        # Empatados, por nombre.
        self.assertEqual(labels(out), ["A", "B", "C", "D"])

    def test_sin_valor_no_compite(self):
        df = pd.DataFrame({"vendedor": ["A", "B", "C"], "ventas": [10.0, 5.0, None]})
        out = ranking(df=df, metrics=[agg("promedio", "avg")], sort_by="promedio")
        self.assertEqual(labels(out), ["B", "A"])
        self.assertEqual(out["total_groups"], 2)


class RankingShareTests(SimpleTestCase):
    def test_porcentaje_del_total_y_el_resto(self):
        out = ranking(limit=1)
        self.assertEqual(out["items"][0]["share"], round(600 / 1070 * 100, 2))
        self.assertEqual(out["rest"], {"count": 2, "value": 470.0, "share": round(470 / 1070 * 100, 2)})

    def test_sin_resto_cuando_entran_todos(self):
        self.assertIsNone(ranking()["rest"])

    def test_promedio_no_tiene_porcentaje(self):
        out = ranking(metrics=[agg("promedio", "avg")], limit=1)
        self.assertIsNone(out["items"][0]["share"])
        self.assertEqual(out["rest"]["count"], 2)
        self.assertIsNone(out["rest"]["share"])

    def test_la_condicion_filtra_antes_de_clasificar(self):
        out = ranking(filters=[{"field": "anio", "op": "eq", "value": 2026}])
        self.assertEqual(labels(out), ["Eva", "Luis", "Ana"])
        self.assertEqual([i["value"] for i in out["items"]], [400.0, 300.0, 50.0])
        self.assertEqual(out["items"][0]["share"], round(400 / 750 * 100, 2))


class RankingValidationTests(SimpleTestCase):
    def test_no_admite_pivotes_ni_dos_columnas(self):
        self.assertTrue(errors_for("ranking", fields(pivots=["mes"])))
        self.assertTrue(errors_for("ranking", fields(dimensions=["categoria", "mes"])))

    def test_una_sola_metrica(self):
        self.assertTrue(errors_for("ranking", fields(metrics=[agg(), agg("cantidad", "count", None)])))
