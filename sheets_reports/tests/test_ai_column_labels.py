"""Pruebas para «Generar nombres con IA» (ai_column_labels y su endpoint)."""
from unittest import mock

from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from sheets_reports.services.ai_column_labels import _ensure_unique_labels, suggest_column_labels
from sheets_reports.services.ai_spec import SpecGenerationError

ASK = "sheets_reports.services.ai_column_labels.generate_json"


class SuggestColumnLabelsServiceTests(SimpleTestCase):
    def test_devuelve_mapeo_limpio(self):
        fake_response = {
            "columns": [
                {"id": "c1", "clean": "Atención al cliente"},
                {"id": "c2", "clean": "Velocidad de entrega"},
                {"id": "c3", "clean": "Fecha"},
            ]
        }
        cols = [
            "¿En qué medida está satisfecho con la atención al cliente?",
            "¿En qué medida está satisfecho con la velocidad de entrega?",
            "Fecha",
        ]
        with mock.patch(ASK, return_value=fake_response) as ask:
            labels = suggest_column_labels(cols)

        self.assertEqual(
            labels,
            {
                "¿En qué medida está satisfecho con la atención al cliente?": "Atención al cliente",
                "¿En qué medida está satisfecho con la velocidad de entrega?": "Velocidad de entrega",
                "Fecha": "Fecha",
            },
        )
        self.assertEqual(ask.call_count, 1)
        self.assertIn('- c2: "¿En qué medida está satisfecho con la velocidad de entrega?"', ask.call_args[0][0])

    def test_nombres_con_espacios_dobles_o_en_los_bordes(self):
        # La respuesta se asocia por identificador, no por el nombre que repita la IA.
        cols = ["Pregunta larga  [Recomendaría la UAPA]", "Región "]
        fake_response = {"columns": [
            {"id": "c1", "clean": "Recomendaría la UAPA"},
            {"id": "c2", "clean": "Región"},
        ]}
        with mock.patch(ASK, return_value=fake_response):
            labels = suggest_column_labels(cols)
        self.assertEqual(labels, {
            "Pregunta larga  [Recomendaría la UAPA]": "Recomendaría la UAPA",
            "Región ": "Región",
        })

    def test_ignora_ids_desconocidos_y_conserva_el_nombre_faltante(self):
        fake_response = {"columns": [{"id": "c9", "clean": "Otra"}, {"id": "c1", "clean": "Ventas netas"}]}
        with mock.patch(ASK, return_value=fake_response):
            labels = suggest_column_labels(["Ventas", "Costo"])
        self.assertEqual(labels, {"Ventas": "Ventas netas", "Costo": "Costo"})

    def test_desambigua_si_la_ia_propone_duplicados(self):
        raw = {
            "Pregunta 1": "Satisfacción",
            "Pregunta 2": "Satisfacción",
            "Pregunta 3": "Satisfacción",
        }
        cols = ["Pregunta 1", "Pregunta 2", "Pregunta 3"]
        unique = _ensure_unique_labels(raw, cols)
        self.assertEqual(unique["Pregunta 1"], "Satisfacción")
        self.assertEqual(unique["Pregunta 2"], "Satisfacción (2)")
        self.assertEqual(unique["Pregunta 3"], "Satisfacción (3)")

    def test_ignora_columna_de_sistema_id_y_vacias(self):
        with mock.patch(ASK, return_value={"columns": [{"id": "c1", "clean": "Ventas"}]}) as ask:
            labels = suggest_column_labels(["id", "", "   ", "Ventas"])

        self.assertEqual(labels, {"Ventas": "Ventas"})
        call_prompt = ask.call_args[0][0]
        self.assertNotIn(': "id"', call_prompt)
        self.assertIn("Ventas", call_prompt)

    def test_retorna_vacio_si_no_hay_columnas_validas(self):
        with mock.patch(ASK) as ask:
            labels = suggest_column_labels(["id", "_id", ""])
        self.assertEqual(labels, {})
        ask.assert_not_called()

    def test_error_en_ia_lanza_spec_generation_error(self):
        with mock.patch(ASK, side_effect=Exception("API failure")):
            with self.assertRaises(SpecGenerationError):
                suggest_column_labels(["Columna muy larga"])


class SuggestColumnLabelsViewTests(TestCase):
    def test_endpoint_exitoso(self):
        fake_labels = {"Columna Larga": "Corta"}
        with mock.patch("sheets_reports.services.ai_column_labels.suggest_column_labels", return_value=fake_labels):
            response = self.client.post(
                reverse("source_suggest_column_labels"),
                data={"columns": ["Columna Larga"]},
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"labels": fake_labels})

    def test_endpoint_error_sin_columnas(self):
        response = self.client.post(
            reverse("source_suggest_column_labels"),
            data={"columns": []},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.json())

    def test_endpoint_error_de_ia(self):
        with mock.patch(
            "sheets_reports.services.ai_column_labels.suggest_column_labels",
            side_effect=SpecGenerationError("La IA no respondió"),
        ):
            response = self.client.post(
                reverse("source_suggest_column_labels"),
                data={"columns": ["Col 1"]},
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json(), {"error": "La IA no respondió"})
