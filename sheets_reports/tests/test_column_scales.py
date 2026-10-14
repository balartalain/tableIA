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
from sheets_reports.utils.scales import dummy_count, effective_scales, known_order, order_gaps, profile_column

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

    def test_las_respuestas_textuales_no_se_descartan_al_detectar_la_escala(self):
        values = ["Completamente satisfecho", "Satisfecho", "No utilizo este medio", "Insatisfecho",
                  "Completamente insatisfecho"]
        self.assertIsNone(known_order(values))
        self.assertIsNone(known_order(["Siempre", "Nunca", "No aplica"]))
        for answer in ("No aplica", "N/A", "No utilizo este medio", "No he recibido este servicio", "Empty"):
            with self.subTest(answer=answer):
                profile = profile_column(pd.Series(["Bajo", "Alto", answer, None]))
                self.assertEqual(profile["scale"], "nominal")
                self.assertEqual(profile["stats"]["missing"], 25.0)
                self.assertIn(answer, profile["stats"]["values"])
                self.assertEqual(dummy_count(profile), 3)
                self.assertEqual(order_gaps(pd.Series(["Bajo", "Alto", answer, None]), ["Bajo", "Alto"]),
                                 [answer])

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

    def put(self, scales, ignored_values=None):
        body = {"scales": scales}
        if ignored_values is not None:
            body["ignored_values"] = ignored_values
        return self.client.put(f"/api/sources/{self.source.id}/scales/", json.dumps(body),
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

    def test_el_orden_guardado_debe_incluir_no_aplica(self, _df):
        df = survey_df()
        df.loc[0, "talla"] = "No aplica"
        _df.side_effect = lambda *a, **k: df.copy()
        response = self.put({"talla": {"scale": "ordinal", "order": ["S", "M", "L"]}})
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("No aplica", response.json()["error"])
        # Con ignored_values no exige incluir No aplica
        response_ign = self.put({"talla": {"scale": "ordinal", "order": ["S", "M", "L"]}}, ignored_values="No aplica")
        self.assertEqual(response_ign.status_code, 200, response_ign.content)
        order = ["S", "M", "L", "No aplica"]
        response = self.put({"talla": {"scale": "ordinal", "order": order}})
        self.assertEqual(response.status_code, 200, response.content)
        schema = self.client.get(f"/api/sources/{self.source.id}/schema/").json()
        self.assertEqual(schema["scales"]["talla"]["order"], order)

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
    def test_la_ia_no_puede_omitir_respuestas_textuales_del_orden(self):
        df = pd.DataFrame({"nivel": ["Bajo", "Alto", "No aplica", "N/A", "Empty", None]})
        complete = ["Bajo", "Alto", "No aplica", "N/A", "Empty"]
        for order in (["Bajo", "Alto"], complete):
            with self.subTest(order=order):
                answer = {"columns": [{"field": "nivel", "scale": "ordinal", "order": order, "reason": "orden"}]}
                with mock.patch("sheets_reports.services.ai_scales.generate_json", return_value=answer):
                    out = classify_scales(df, ["nivel"], effective_scales(df))
                self.assertEqual(out, {"nivel": {"scale": "ordinal", "order": complete, "reason": "orden"}}
                                 if order == complete else {})

    def test_con_ignored_values_la_ia_excluye_no_aplica_del_orden(self):
        df = pd.DataFrame({"nivel": ["Bajo", "Alto", "No aplica", "N/A", None]})
        answer = {"columns": [{"field": "nivel", "scale": "ordinal", "order": ["Bajo", "Alto", "No aplica"], "reason": "orden"}]}
        with mock.patch("sheets_reports.services.ai_scales.generate_json", return_value=answer) as ai:
            out = classify_scales(df, ["nivel"], effective_scales(df), ignored_values="No aplica, N/A")
        self.assertEqual(out, {"nivel": {"scale": "ordinal", "order": ["Bajo", "Alto"], "reason": "orden"}})
        prompt = ai.call_args.args[0]
        col_line = next(line for line in prompt.splitlines() if '"nivel"' in line)
        self.assertIn('valores: ["Bajo", "Alto"]', col_line)
        self.assertNotIn("No aplica", col_line)

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
