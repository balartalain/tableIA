"""Pruebas para el chatbot analítico sobre la fuente de datos (ai_data_chat y endpoint)."""
from unittest import mock

import pandas as pd
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from sheets_reports.models import Dashboard, DataSource
from sheets_reports.services import ai_data_chat

BASE = "sheets_reports.services.ai_data_chat"
ANSWER = {"text": "OK", "images": [], "code": []}


def uploaded(name):
    file = mock.Mock()
    file.name = name
    return file


def source(**extra):
    return mock.Mock(**{"label": "Ventas", "pk": 1, "id": 1, "gid": "0", "columns": [], **extra})


class AskDataChatServiceTests(SimpleTestCase):
    """La pregunta elige sus columnas y solo esas (con la ID y todas las filas) se suben."""

    def ask(self, df, chosen, prompt="¿Total por sede?", answer=ANSWER, **source_extra):
        self.sent = {}

        def upload(path, display_name=None):
            self.sent["csv"] = open(path, encoding="utf-8").read()
            return uploaded("files/x")
        with mock.patch(f"{BASE}.source_frame", return_value=df), \
                mock.patch(f"{BASE}.generate_json", return_value={"columns": chosen}) as self.selector, \
                mock.patch(f"{BASE}.gemini_upload_file", side_effect=upload) as self.upload, \
                mock.patch(f"{BASE}.gemini_delete_file") as self.delete, \
                mock.patch(f"{BASE}.generate_with_code_execution",
                           **({"side_effect": answer} if isinstance(answer, Exception) else {"return_value": answer})
                           ) as self.analysis:
            return ai_data_chat.ask_data_chat(source(**source_extra), prompt)

    def test_sube_solo_las_columnas_elegidas_con_todas_las_filas_y_lo_borra(self):
        df = pd.DataFrame({"ID": [1, 2, 3], "sede": ["Norte", "Sur", "Norte"], "ventas": [100, 200, 300],
                           "comentario": ["a", "b", "c"]})
        res = self.ask(df, ["col_2", "col_3", "col_2", "col_1", "col_99"])
        self.assertEqual(res, ANSWER)
        # Sin repetir, sin la ID elegida dos veces y sin las que no existen.
        self.assertEqual(self.sent["csv"], "ID,sede,ventas\n1,Norte,100\n2,Sur,200\n3,Norte,300\n")
        self.assertEqual(self.analysis.call_args.kwargs["file_ref"].name, "files/x")
        self.delete.assert_called_once_with("files/x")
        prompt = self.analysis.call_args.kwargs["prompt"]
        self.assertIn('"ventas"', prompt)
        self.assertNotIn('"comentario"', prompt)

    def test_usa_los_nombres_a_mostrar_de_la_fuente(self):
        df = pd.DataFrame({"ID": [1], "¿En qué sede estudia usted actualmente?": ["Norte"], "ventas": [100]})
        self.ask(df, ["col_2"], columns=[{"name": "¿En qué sede estudia usted actualmente?", "label": "Sede"},
                                          {"name": "ventas", "label": ""}])
        self.assertEqual(self.sent["csv"], "ID,Sede\n1,Norte\n")
        selection = self.selector.call_args.args[0]
        self.assertIn('"Sede"', selection)
        self.assertNotIn("¿En qué sede", selection)

    def test_si_dos_nombres_a_mostrar_chocan_conservan_el_encabezado(self):
        df = pd.DataFrame({"ID": [1], "a": [1], "b": [2]})
        self.ask(df, ["col_2", "col_3"], columns=[{"name": "a", "label": "X"}, {"name": "b", "label": "X"}])
        self.assertEqual(self.sent["csv"], "ID,a,b\n1,1,2\n")

    def test_la_seleccion_ve_los_valores_de_ejemplo(self):
        df = pd.DataFrame({"ID": [1, 2], "sede": ["Norte", "Sur"]})
        self.ask(df, ["col_2"])
        self.assertIn("Norte", self.selector.call_args.args[0])
        self.assertEqual(self.selector.call_args.kwargs["call"], "data_chat_columns")

    def test_el_contexto_lleva_tipo_y_ejemplos_pero_no_la_escala(self):
        """La escala de cada columna la deduce Gemini con los datos."""
        df = pd.DataFrame({"ID": [1, 2, 3], "sede": ["Norte", "Sur", "Norte"], "ventas": [100, 200, 300]})
        self.ask(df, ["col_2", "col_3"])
        for prompt in (self.selector.call_args.args[0], self.analysis.call_args.kwargs["prompt"]):
            self.assertIn('"sede" (texto)', prompt)
            self.assertIn('"ventas" (numérica)', prompt)
            self.assertIn("Norte", prompt)
            for scale in ("nominal", "ordinal", "continua", "discreta"):
                self.assertNotIn(scale, prompt)

    def test_demasiadas_columnas_pide_acotar_sin_consultar(self):
        df = pd.DataFrame({"ID": [1], **{f"p{i}": [i] for i in range(20)}})
        res = self.ask(df, [f"col_{i}" for i in range(2, 2 + ai_data_chat.MAX_CHAT_COLUMNS + 1)])
        self.assertEqual(res["text"], ai_data_chat.TOO_MANY_COLUMNS)
        self.upload.assert_not_called()
        self.analysis.assert_not_called()

    def test_sin_columnas_pide_ser_mas_especifico(self):
        res = self.ask(pd.DataFrame({"ID": [1], "sede": ["Norte"]}), ["col_1"])
        self.assertEqual(res["text"], ai_data_chat.NO_COLUMNS)
        self.analysis.assert_not_called()

    def test_si_pasa_del_tiempo_maximo_avisa_y_borra_el_archivo(self):
        with self.assertLogs(BASE, "WARNING"):
            res = self.ask(pd.DataFrame({"ID": [1], "sede": ["Norte"]}), ["col_2"],
                           answer=ai_data_chat.AITimeoutError("timeout"))
        self.assertEqual(res, {"text": ai_data_chat.TIMEOUT, "images": [], "code": []})
        self.delete.assert_called_once_with("files/x")

    def test_no_obliga_a_imprimir_los_textos_con_codigo(self):
        """El CSV ya está en el contexto: el modelo decide si usa código para leer los textos."""
        self.assertNotIn(".dropna().tolist()", ai_data_chat.SYSTEM_INSTRUCTION)
        self.assertNotIn("respuestas impresas", ai_data_chat.SYSTEM_INSTRUCTION)

    def test_fuente_vacia(self):
        with mock.patch(f"{BASE}.source_frame", return_value=pd.DataFrame()), \
                mock.patch(f"{BASE}.generate_json") as selector:
            res = ai_data_chat.ask_data_chat(source(), "Resumen")
        self.assertIn("vacía", res["text"])
        selector.assert_not_called()

    def test_el_contexto_describe_solo_las_columnas_del_csv(self):
        """El CSV que se sube no tiene las columnas virtuales de las cuadrículas: el contexto
        tampoco las describe, pero sí las columnas reales de la cuadrícula."""
        from sheets_reports.tests.test_grids import ASPECT, VALUE, survey_source_df
        df = survey_source_df()
        field = "Evaluación [Contenido]"
        alias = f"col_{list(df.columns).index(field) + 1}"
        self.ask(df, [alias], prompt="¿Cómo evalúan?")
        for prompt in (self.selector.call_args.args[0], self.analysis.call_args.kwargs["prompt"]):
            self.assertNotIn(ASPECT, prompt)
            self.assertNotIn(VALUE, prompt)
            self.assertNotIn("Cuadrículas y preguntas de casillas", prompt)
            self.assertIn(field, prompt)


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
