"""Pruebas para el chatbot analítico sobre la fuente de datos (ai_data_chat y endpoint)."""
from unittest import mock
import pandas as pd
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from sheets_reports.models import Dashboard, DataSource
from sheets_reports.services import ai_data_chat


class AskDataChatServiceTests(SimpleTestCase):
    @mock.patch("sheets_reports.services.ai_data_chat.load_source")
    @mock.patch("sheets_reports.services.ai_data_chat.generate_with_code_execution")
    def test_ask_data_chat_success(self, mock_gen, mock_load_source):
        mock_load_source.return_value = pd.DataFrame({"sede": ["Norte", "Sur"], "ventas": [100, 200]})
        fake_res = {
            "text": "El total de ventas es 300.",
            "images": ["data:image/png;base64,fake"],
            "code": ["print(df['ventas'].sum())"],
        }
        mock_gen.return_value = fake_res

        mock_source = mock.Mock(label="Ventas")
        res = ai_data_chat.ask_data_chat(mock_source, "¿Cuál es el total de ventas?")

        self.assertIn("300", res["text"])
        self.assertEqual(len(res["code"]), 1)
        self.assertIn("print", res["code"][0])
        self.assertEqual(len(res["images"]), 1)
        self.assertTrue(res["images"][0].startswith("data:image/png;base64,"))

    @mock.patch("sheets_reports.services.ai_data_chat.load_source")
    def test_ask_data_chat_empty_df(self, mock_load_source):
        mock_load_source.return_value = pd.DataFrame()
        mock_source = mock.Mock(label="Vacia")
        res = ai_data_chat.ask_data_chat(mock_source, "Resumen")
        self.assertIn("vacía", res["text"])
        self.assertEqual(res["images"], [])


class DashboardDataChatViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("admin", "a@a.com", "pass")
        self.client.force_login(self.user)
        self.dashboard = Dashboard.objects.create(owner=self.user, nombre="Tablero Chat")
        self.source = DataSource.objects.create(dashboard=self.dashboard, sheet_id="sheet123")

    def test_endpoint_exitoso(self):
        fake_res = {"text": "Respuesta correcta", "images": [], "code": ["df.mean()"]}
        with mock.patch("sheets_reports.services.ai_data_chat.ask_data_chat", return_value=fake_res):
            r = self.client.post(
                reverse("dashboard_data_chat", kwargs={"dashboard_id": self.dashboard.id}),
                data={"prompt": "¿Cuál es la media?"},
                content_type="application/json",
            )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), fake_res)

    def test_endpoint_prompt_vacio_retorna_400(self):
        r = self.client.post(
            reverse("dashboard_data_chat", kwargs={"dashboard_id": self.dashboard.id}),
            data={"prompt": ""},
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 400)
        self.assertIn("error", r.json())

    def test_endpoint_sin_fuentes_retorna_error(self):
        d_vacio = Dashboard.objects.create(owner=self.user, nombre="Sin fuentes")
        r = self.client.post(
            reverse("dashboard_data_chat", kwargs={"dashboard_id": d_vacio.id}),
            data={"prompt": "¿Hay datos?"},
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 400)
        self.assertIn("fuentes", r.json()["error"].lower())
