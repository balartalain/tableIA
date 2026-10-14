"""Escala de medida de las columnas: lo que detectan las reglas, lo que se guarda en la fuente y
lo que propone la IA («Clasificar con IA»)."""
import json
from unittest import mock

import pandas as pd
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from sheets_reports.models import DataSource
from sheets_reports.services.ai_scales import classify_scales
from sheets_reports.services.sheets import apply_column_config
from sheets_reports.tests.fixtures import make_board
from sheets_reports.utils.scales import effective_scales, known_order, profile_column

SHEET = "sheets_reports.services.sheets.get_sheet_dataframe"


def survey_df():
    return pd.DataFrame({
        "ID": range(1, 9),
        "edad": [18, 22, 25, 30, 35, 41, 50, 64],
        "ingreso": [1200.5, 1500.2, 980.0, 2100.7, 3300.1, 1800.9, 2500.3, 4100.8],
        "satisfaccion": ["Muy satisfecha", "Satisfecho", "Neutral", "Insatisfecho",
                         "Satisfecho", "Muy satisfecho", "Neutral", "Satisfecho"],
        "sede": ["Norte", "Sur", "Norte", "Este", "Sur", "Norte", "Este", "Sur"],
        "talla": ["S", "M", "L", "M", "S", "L", "M", "S"],
        "comentario": ["bien", "muy bien", "regular", "nada", "excelente", "ok", "mal", "genial"],
        "becado": [0, 1, 0, 1, 1, 0, 0, 1],
    })


class ProfileTests(SimpleTestCase):
    def test_reglas_de_cada_escala(self):
        scales = effective_scales(survey_df())
        self.assertEqual({name: p["scale"] for name, p in scales.items()}, {
            "ID": "none", "edad": "discrete", "ingreso": "continuous", "satisfaccion": "ordinal",
            "sede": "nominal", "talla": "nominal", "comentario": "none", "becado": "nominal",
        })
        self.assertEqual(scales["satisfaccion"]["order"],
                         ["Insatisfecho", "Neutral", "Satisfecho", "Muy satisfecha", "Muy satisfecho"])
        # Sin escala conocida podría tener orden (S < M < L): la IA o el usuario lo confirman.
        self.assertTrue(scales["talla"]["uncertain"])

    def test_discreta_y_continua(self):
        self.assertEqual(profile_column(pd.Series([1, 2, 3, 2, 1, 3]))["scale"], "discrete")
        self.assertEqual(profile_column(pd.Series(range(100)))["scale"], "continuous")
        self.assertEqual(profile_column(pd.Series([2025, 2026, 2025]))["scale"], "discrete")

    def test_escalas_conocidas_y_numero_al_inicio(self):
        self.assertEqual(known_order(["Siempre", "Nunca", "A veces"]), ["Nunca", "A veces", "Siempre"])
        self.assertEqual(known_order(["De acuerdo", "Totalmente en desacuerdo", "Neutral"]),
                         ["Totalmente en desacuerdo", "Neutral", "De acuerdo"])
        self.assertEqual(known_order(["10 - Excelente", "2 - Malo", "5 - Regular"]),
                         ["2 - Malo", "5 - Regular", "10 - Excelente"])
        self.assertIsNone(known_order(["Norte", "Sur"]))

    def test_las_respuestas_de_no_aplica_no_son_un_nivel(self):
        values = ["Completamente satisfecho", "Satisfecho", "No utilizo este medio", "Insatisfecho",
                  "Completamente insatisfecho"]
        self.assertEqual(known_order(values), ["Completamente insatisfecho", "Insatisfecho", "Satisfecho",
                                               "Completamente satisfecho"])
        self.assertEqual(known_order(["Siempre", "La mayoría de las veces", "Pocas veces", "Nunca", "No aplica"]),
                         ["Nunca", "Pocas veces", "La mayoría de las veces", "Siempre"])
        # Cuentan como vacías, sin avisar de valores fuera del orden.
        from sheets_reports.tests.fixtures import compiled, table_fields
        df = pd.DataFrame({"nivel": ["Bajo", "Medio", "Alto", "No aplica", "Bajo"], "x": [1.5, 2.5, 3.5, 9.5, 1.5]})
        out = compiled("correlation", table_fields(columns=("nivel", "x")), df=df)
        self.assertNotIn("unmatched", out["variables"][0])
        self.assertEqual((out["n"][0][1], out["matrix"][0][1]), (4, 1.0))

    def test_fechas_y_envio_no_se_analizan(self):
        df = pd.DataFrame({"f": ["2026-01-01", "2026-02-01"], "envio": ["a", "b"]})
        scales = effective_scales(df, date_columns={"f"}, submission="envio")
        self.assertEqual(scales["f"]["scale"], "none")
        self.assertEqual(scales["envio"]["scale"], "none")

    def test_la_guardada_manda(self):
        scales = effective_scales(survey_df(), {"talla": {"scale": "ordinal", "order": ["S", "M", "L"]}})
        self.assertEqual(scales["talla"], {**scales["talla"], "scale": "ordinal", "order": ["S", "M", "L"],
                                           "saved": True, "uncertain": False})

    def test_la_configuracion_de_la_fuente_deja_las_escalas(self):
        df = apply_column_config(survey_df(), [
            {"name": "talla", "type": "text", "include": True, "label": "Talla",
             "scale": "ordinal", "order": ["S", "M", "L"]},
            {"name": "sede", "type": "text", "include": True},
        ])
        self.assertEqual(df.attrs["column_scales"], {"Talla": {"scale": "ordinal", "order": ["S", "M", "L"]}})


@mock.patch(SHEET, side_effect=lambda *a, **k: survey_df())
class ScalesViewsTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("admin", "a@a.com", "x")
        self.client.force_login(self.user)
        self.dashboard, self.source = make_board(self.user)

    def put(self, scales):
        return self.client.put(f"/api/sources/{self.source.id}/scales/", json.dumps({"scales": scales}),
                               content_type="application/json")

    def test_el_schema_trae_las_escalas(self, _df):
        schema = self.client.get(f"/api/sources/{self.source.id}/schema/").json()
        self.assertEqual(schema["scales"]["satisfaccion"]["scale"], "ordinal")
        self.assertNotIn("ID", schema["scales"])

    def test_guarda_la_escala_y_crea_la_configuracion(self, _df):
        r = self.put({"talla": {"scale": "ordinal", "order": ["S", "M", "L"]}})
        self.assertEqual(r.status_code, 200, r.content)
        columns = {c["name"]: c for c in DataSource.objects.get(id=self.source.id).columns}
        self.assertEqual(columns["talla"]["order"], ["S", "M", "L"])
        self.assertEqual(columns["edad"], {"name": "edad", "type": "number", "include": True})
        schema = self.client.get(f"/api/sources/{self.source.id}/schema/").json()
        self.assertEqual(schema["scales"]["talla"]["scale"], "ordinal")
        self.assertTrue(schema["scales"]["talla"]["saved"])
        # Cambiar a nominal quita el orden.
        self.assertEqual(self.put({"talla": {"scale": "nominal"}}).status_code, 200)
        columns = {c["name"]: c for c in DataSource.objects.get(id=self.source.id).columns}
        self.assertNotIn("order", columns["talla"])

    def test_valida(self, _df):
        self.assertEqual(self.put({"talla": {"scale": "ordinal", "order": ["S", "M"]}}).status_code, 400)
        self.assertEqual(self.put({"talla": {"scale": "rara"}}).status_code, 400)
        self.assertEqual(self.put({"nope": {"scale": "nominal"}}).status_code, 400)
        self.assertEqual(self.put({"ID": {"scale": "nominal"}}).status_code, 400)
        self.assertEqual(self.put({"sede": {"scale": "nominal", "order": ["Norte"]}}).status_code, 400)

    def test_el_editor_de_la_fuente_conserva_las_escalas(self, _df):
        self.put({"talla": {"scale": "ordinal", "order": ["S", "M", "L"]}})
        columns = self.client.get(f"/api/sources/{self.source.id}/columns/").json()["columns"]
        talla = next(c for c in columns if c["name"] == "talla")
        self.assertEqual((talla["scale"], talla["order"]), ("ordinal", ["S", "M", "L"]))

    def test_clasificar_con_ia_no_guarda(self, _df):
        answer = {"columns": [{"field": "talla", "scale": "ordinal", "order": ["s", "M", "L"], "reason": "tallas"}]}
        with mock.patch("sheets_reports.services.ai_scales.generate_json", return_value=answer):
            r = self.client.post(f"/api/sources/{self.source.id}/scales/ai/", json.dumps({"columns": ["talla"]}),
                                 content_type="application/json")
        self.assertEqual(r.status_code, 200, r.content)
        # El orden queda escrito como en la hoja.
        self.assertEqual(r.json()["scales"], {"talla": {"scale": "ordinal", "order": ["S", "M", "L"], "reason": "tallas"}})
        self.assertEqual(DataSource.objects.get(id=self.source.id).columns, [])


class ClassifyScalesTests(SimpleTestCase):
    def test_descarta_un_orden_incompleto_y_columnas_desconocidas(self):
        df = survey_df()
        answer = {"columns": [
            {"field": "talla", "scale": "ordinal", "order": ["S", "M"], "reason": "x"},
            {"field": "sede", "scale": "nominal", "reason": "lugares"},
            {"field": "otra", "scale": "nominal", "reason": "x"},
        ]}
        with mock.patch("sheets_reports.services.ai_scales.generate_json", return_value=answer) as ai:
            out = classify_scales(df, ["talla", "sede"], effective_scales(df))
        self.assertEqual(out, {"sede": {"scale": "nominal", "reason": "lugares"}})
        prompt = ai.call_args.args[0]
        self.assertIn('"talla" (texto; detectada: nominal', prompt)
        self.assertIn('valores: ["S", "M", "L"]', prompt)
