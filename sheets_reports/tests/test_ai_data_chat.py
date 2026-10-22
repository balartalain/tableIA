"""Pruebas para el chatbot analítico sobre la fuente de datos (ai_data_chat y endpoint)."""
from unittest import mock
import pandas as pd
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from sheets_reports.models import Dashboard, DataSource
from sheets_reports.services import ai_data_chat


class AskDataChatServiceTests(SimpleTestCase):
    @mock.patch("sheets_reports.services.ai_data_chat.gemini_upload_file")
    @mock.patch("sheets_reports.services.ai_data_chat.load_source")
    @mock.patch("sheets_reports.services.ai_data_chat.generate_with_code_execution")
    def test_ask_data_chat_success(self, mock_gen, mock_load_source, mock_upload):
        mock_file = mock.Mock()
        mock_file.name = "files/test123"
        mock_upload.return_value = mock_file

        mock_load_source.return_value = pd.DataFrame({"sede": ["Norte", "Sur"], "ventas": [100, 200]})
        fake_res = {
            "text": "El total de ventas es 300.",
            "images": ["data:image/png;base64,fake"],
            "code": ["print(df['ventas'].sum())"],
        }
        mock_gen.return_value = fake_res

        mock_source = mock.Mock(label="Ventas", gemini_file_id="", pk=1)
        res = ai_data_chat.ask_data_chat(mock_source, "¿Cuál es el total de ventas?")

        self.assertIn("300", res["text"])
        self.assertEqual(len(res["code"]), 1)
        self.assertIn("print", res["code"][0])
        self.assertEqual(len(res["images"]), 1)
        self.assertTrue(res["images"][0].startswith("data:image/png;base64,"))
        mock_upload.assert_called_once()
        self.assertEqual(mock_source.gemini_file_id, "files/test123")
        mock_source.save.assert_called_once_with(update_fields=["gemini_file_id"])

    @mock.patch("sheets_reports.services.ai_data_chat.gemini_upload_file")
    @mock.patch("sheets_reports.services.ai_data_chat.gemini_get_file")
    @mock.patch("sheets_reports.services.ai_data_chat.load_source")
    @mock.patch("sheets_reports.services.ai_data_chat.generate_with_code_execution")
    def test_ask_data_chat_reuses_cached_gemini_file_id(self, mock_gen, mock_load_source, mock_get, mock_upload):
        mock_existing_file = mock.Mock()
        mock_existing_file.name = "files/existing123"
        mock_get.return_value = mock_existing_file

        mock_load_source.return_value = pd.DataFrame({"sede": ["Norte"], "ventas": [100]})
        mock_gen.return_value = {"text": "OK", "images": [], "code": []}

        mock_source = mock.Mock(label="Ventas", gemini_file_id="files/existing123", pk=1)
        res = ai_data_chat.ask_data_chat(mock_source, "¿Total?")

        self.assertEqual(res["text"], "OK")
        mock_get.assert_called_once_with("files/existing123")
        mock_upload.assert_not_called()
        self.assertEqual(mock_gen.call_args[1]["file_ref"], mock_existing_file)

    @mock.patch("sheets_reports.services.ai_data_chat.gemini_upload_file")
    @mock.patch("sheets_reports.services.ai_data_chat.gemini_get_file")
    @mock.patch("sheets_reports.services.ai_data_chat.load_source")
    @mock.patch("sheets_reports.services.ai_data_chat.generate_with_code_execution")
    def test_ask_data_chat_reuploads_when_expired(self, mock_gen, mock_load_source, mock_get, mock_upload):
        mock_get.return_value = None
        mock_new_file = mock.Mock()
        mock_new_file.name = "files/new_after_expire"
        mock_upload.return_value = mock_new_file

        mock_load_source.return_value = pd.DataFrame({"sede": ["Norte"], "ventas": [100]})
        mock_gen.return_value = {"text": "OK", "images": [], "code": []}

        mock_source = mock.Mock(label="Ventas", gemini_file_id="files/expired_id", pk=1)
        ai_data_chat.ask_data_chat(mock_source, "¿Total?")

        mock_get.assert_called_once_with("files/expired_id")
        mock_upload.assert_called_once()
        self.assertEqual(mock_source.gemini_file_id, "files/new_after_expire")
        mock_source.save.assert_called_once_with(update_fields=["gemini_file_id"])

    @mock.patch("sheets_reports.services.ai_data_chat.gemini_delete_file")
    def test_delete_source_gemini_file(self, mock_delete):
        mock_source = mock.Mock(gemini_file_id="files/to_delete", pk=1)
        ai_data_chat.delete_source_gemini_file(mock_source)

        mock_delete.assert_called_once_with("files/to_delete")
        self.assertEqual(mock_source.gemini_file_id, "")
        mock_source.save.assert_called_once_with(update_fields=["gemini_file_id"])

    @mock.patch("sheets_reports.services.ai_data_chat.gemini_delete_file")
    @mock.patch("sheets_reports.services.ai_data_chat.gemini_upload_file")
    @mock.patch("sheets_reports.services.ai_data_chat.load_source")
    def test_resync_sube_el_nuevo_y_borra_el_viejo(self, mock_load_source, mock_upload, mock_delete):
        calls = mock.Mock()
        calls.attach_mock(mock_upload, "upload")
        calls.attach_mock(mock_delete, "delete")
        mock_new_file = mock.Mock()
        mock_new_file.name = "files/nuevo"
        mock_upload.return_value = mock_new_file
        mock_load_source.return_value = pd.DataFrame({"ventas": [100]})

        mock_source = mock.Mock(gemini_file_id="files/viejo", pk=1)
        self.assertTrue(ai_data_chat.resync_source_file(mock_source))

        self.assertEqual(mock_source.gemini_file_id, "files/nuevo")
        mock_delete.assert_called_once_with("files/viejo")
        self.assertEqual([c[0] for c in calls.mock_calls], ["upload", "delete"])

    @mock.patch("sheets_reports.services.ai_data_chat.gemini_delete_file")
    @mock.patch("sheets_reports.services.ai_data_chat.gemini_upload_file")
    @mock.patch("sheets_reports.services.ai_data_chat.load_source")
    def test_resync_fuente_vacia_borra_el_archivo(self, mock_load_source, mock_upload, mock_delete):
        mock_load_source.return_value = pd.DataFrame()
        mock_source = mock.Mock(gemini_file_id="files/viejo", pk=1)
        self.assertFalse(ai_data_chat.resync_source_file(mock_source))
        mock_upload.assert_not_called()
        mock_delete.assert_called_once_with("files/viejo")
        self.assertEqual(mock_source.gemini_file_id, "")

    @mock.patch("sheets_reports.services.ai_data_chat.load_source")
    def test_ask_data_chat_empty_df(self, mock_load_source):
        mock_load_source.return_value = pd.DataFrame()
        mock_source = mock.Mock(label="Vacia")
        res = ai_data_chat.ask_data_chat(mock_source, "Resumen")
        self.assertIn("vacía", res["text"])
        self.assertEqual(res["images"], [])

    @mock.patch("sheets_reports.services.ai_data_chat.gemini_upload_file")
    @mock.patch("sheets_reports.services.ai_data_chat.load_source")
    @mock.patch("sheets_reports.services.ai_data_chat.generate_with_code_execution")
    def test_ask_data_chat_includes_column_scales(self, mock_gen, mock_load_source, mock_upload):
        mock_file = mock.Mock()
        mock_file.name = "files/test_scales"
        mock_upload.return_value = mock_file

        comments = [f"Comentario sobre la experiencia {i}: la atención fue {'buena' if i % 2 == 0 else 'mala y lenta'}" for i in range(20)]
        df = pd.DataFrame({
            "comentario": comments,
            "ventas": list(range(10, 210, 10))
        })
        mock_load_source.return_value = df
        mock_gen.return_value = {"text": "Análisis cualitativo", "images": [], "code": []}

        mock_source = mock.Mock(label="Encuesta", gemini_file_id="", pk=1, gid="0")
        ai_data_chat.ask_data_chat(mock_source, "¿Aspectos negativos?")

        called_prompt = mock_gen.call_args[1]["prompt"]
        self.assertIn("comentario", called_prompt)
        self.assertIn("texto libre", called_prompt)
        self.assertIn("ventas", called_prompt)

    @mock.patch("sheets_reports.services.ai_data_chat.gemini_upload_file")
    @mock.patch("sheets_reports.services.ai_data_chat.load_source")
    @mock.patch("sheets_reports.services.ai_data_chat.generate_with_code_execution")
    def test_el_contexto_describe_solo_las_columnas_del_csv(self, mock_gen, mock_load_source, mock_upload):
        """El CSV que se sube no tiene las columnas virtuales de las cuadrículas: el contexto
        tampoco las describe, pero sí las columnas reales de la cuadrícula."""
        from sheets_reports.tests.test_grids import ASPECT, VALUE, survey_source_df
        df = survey_source_df()
        mock_load_source.return_value = df
        mock_upload.return_value = mock.Mock()
        mock_gen.return_value = {"text": "ok", "images": [], "code": []}

        ai_data_chat.ask_data_chat(mock.Mock(label="Encuesta", gemini_file_id="", pk=1, gid="0"), "¿Cómo evalúan?")

        prompt = mock_gen.call_args[1]["prompt"]
        self.assertNotIn(ASPECT, prompt)
        self.assertNotIn(VALUE, prompt)
        self.assertNotIn("Cuadrículas y preguntas de casillas", prompt)
        self.assertIn("Evaluación [Contenido]", prompt)
        self.assertNotIn(ASPECT, df.to_csv(index=False))


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

    @mock.patch("sheets_reports.services.ai_data_chat.delete_source_gemini_file")
    @mock.patch("sheets_reports.services.sheets.refresh_sheet")
    @mock.patch("sheets_reports.services.google_drive.tab_status", return_value="ok")
    def test_source_refresh_clears_gemini_file_id(self, _tab_status, _refresh_sheet, mock_delete_file):
        self.source.gemini_file_id = "files/existing123"
        self.source.save()

        def fake_delete(s):
            s.gemini_file_id = ""
            s.save(update_fields=["gemini_file_id"])

        mock_delete_file.side_effect = fake_delete

        r = self.client.post(reverse("source_refresh", kwargs={"source_id": self.source.id}))
        self.assertEqual(r.status_code, 200)
        mock_delete_file.assert_called_once_with(self.source)
        self.source.refresh_from_db()
        self.assertEqual(self.source.gemini_file_id, "")

    def test_board_view_renders_chatbot(self):
        r = self.client.get(reverse("board_view", kwargs={"dashboard_id": self.dashboard.id}))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "data-chat.js")
        self.assertContains(r, "dataChat()")

    def test_el_editor_no_muestra_el_chatbot(self):
        """El chatbot es solo de la vista (board_view), no del editor."""
        r = self.client.get(reverse("board_editor", kwargs={"dashboard_id": self.dashboard.id}))
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, "data-chat.js")
        self.assertNotContains(r, "dataChat()")

    def test_endpoint_funciona_sin_autenticacion(self):
        self.client.logout()
        fake_res = {"text": "Respuesta pública", "images": [], "code": []}
        with mock.patch("sheets_reports.services.ai_data_chat.ask_data_chat", return_value=fake_res):
            r = self.client.post(
                reverse("dashboard_data_chat", kwargs={"dashboard_id": self.dashboard.id}),
                data={"prompt": "¿Ventas totales?"},
                content_type="application/json",
            )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), fake_res)
