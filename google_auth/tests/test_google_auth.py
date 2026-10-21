"""Login con Google (OAuth 2.0) y Google Picker: guardar y renovar los tokens, y los endpoints
de login/callback/status/disconnect. También, que leer una hoja use el token del usuario antes
que el de la cuenta de servicio."""
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from google_auth import services
from google_auth.models import GoogleCredential
from sheets_reports.services import sheets

CLIENT = {
    "GOOGLE_OAUTH_CLIENT_ID": "cid",
    "GOOGLE_OAUTH_CLIENT_SECRET": "secret",
    "GOOGLE_OAUTH_REDIRECT_URI": "http://testserver/auth/google/callback/",
    "GOOGLE_PICKER_API_KEY": "picker-key",
    "GOOGLE_PICKER_APP_ID": "123456",
}


def credentials(token="tok", refresh="ref"):
    from google.oauth2.credentials import Credentials

    return Credentials(token=token, refresh_token=refresh,
                       token_uri="https://oauth2.googleapis.com/token",
                       client_id="cid", client_secret="secret", scopes=services.SCOPES)


class ConfigTests(TestCase):
    def test_sin_credenciales_no_esta_configurado(self):
        with override_settings(GOOGLE_OAUTH_CLIENT_ID="", GOOGLE_OAUTH_CLIENT_SECRET=""):
            self.assertFalse(services.is_configured())

    @override_settings(**CLIENT)
    def test_configurado_con_cliente(self):
        self.assertTrue(services.is_configured())

    @override_settings(**CLIENT)
    def test_la_url_de_consentimiento_es_de_google(self):
        url, state, _ = services.authorization_url(None)
        self.assertTrue(url.startswith("https://accounts.google.com/"))
        self.assertTrue(state)


class CredentialStorageTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("ana", password="x")

    def test_sin_conexion_no_hay_credenciales(self):
        self.assertIsNone(services.user_credentials(self.user))

    @override_settings(**CLIENT)
    def test_guardar_cifra_y_recupera(self):
        services.save_credentials(self.user, credentials(), email="ana@example.com")
        stored = GoogleCredential.objects.get(user=self.user)
        self.assertEqual(stored.email, "ana@example.com")
        self.assertNotIn("tok", stored.access_token_encrypted)   # no queda en claro
        self.assertNotIn("ref", stored.refresh_token_encrypted)

        fresh = services.user_credentials(self.user)
        self.assertEqual(fresh.token, "tok")
        self.assertEqual(fresh.refresh_token, "ref")

    @override_settings(**CLIENT)
    def test_desconectar_borra_los_tokens(self):
        services.save_credentials(self.user, credentials())
        services.disconnect(self.user)
        self.assertFalse(GoogleCredential.objects.filter(user=self.user).exists())


class TokenOrderTests(TestCase):
    """Al leer una hoja se intenta con el token del usuario activo."""

    @mock.patch("google_auth.services.active_access_token", return_value="user")
    def test_usuario_activo(self, *_):
        self.assertEqual(sheets._read_attempts(), ["user"])

    @mock.patch("google_auth.services.active_access_token", return_value=None)
    def test_sin_usuario_intenta_sin_token_por_si_es_publica(self, *_):
        self.assertEqual(sheets._read_attempts(), [None])


class EndpointTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("admin", "a@a.com", "x")
        self.client.force_login(self.user)

    def test_status_sin_configurar(self):
        with override_settings(GOOGLE_OAUTH_CLIENT_ID="", GOOGLE_OAUTH_CLIENT_SECRET=""):
            data = self.client.get("/auth/google/status/").json()
        self.assertFalse(data["configured"])
        self.assertFalse(data["connected"])
        self.assertEqual(data["access_token"], "")

    @override_settings(**CLIENT)
    def test_status_conectado_devuelve_el_token(self):
        services.save_credentials(self.user, credentials())
        data = self.client.get("/auth/google/status/").json()
        self.assertTrue(data["configured"])
        self.assertTrue(data["connected"])
        self.assertEqual(data["access_token"], "tok")

    @override_settings(**CLIENT)
    def test_login_redirige_a_google_y_recuerda_el_estado(self):
        with mock.patch("google_auth.services.build_flow") as build:
            build.return_value.authorization_url.return_value = (
                "https://accounts.google.com/o/oauth2/auth?client_id=cid", "st")
            r = self.client.get("/auth/google/login/", {"next": "/tableros/nuevo/"})
        self.assertEqual(r.status_code, 302)
        self.assertIn("accounts.google.com", r["Location"])
        self.assertEqual(self.client.session["google_oauth_state"], "st")
        self.assertEqual(self.client.session["google_oauth_next"], "/tableros/nuevo/")

    def test_login_sin_configurar_avisa(self):
        with override_settings(GOOGLE_OAUTH_CLIENT_ID="", GOOGLE_OAUTH_CLIENT_SECRET=""):
            r = self.client.get("/auth/google/login/")
        self.assertEqual(r.status_code, 400)

    @override_settings(**CLIENT)
    def test_callback_guarda_los_tokens_y_vuelve(self):
        session = self.client.session
        session["google_oauth_state"] = "st"
        session["google_oauth_next"] = "/tableros/nuevo/"
        session.save()
        with mock.patch("google_auth.services.exchange_code", return_value=credentials()):
            r = self.client.get("/auth/google/callback/", {"code": "abc", "state": "st"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r["Location"], "/tableros/nuevo/?google=connected")
        self.assertTrue(GoogleCredential.objects.filter(user=self.user).exists())

    @override_settings(**CLIENT)
    def test_callback_con_estado_invalido(self):
        r = self.client.get("/auth/google/callback/", {"code": "abc", "state": "otro"})
        self.assertEqual(r.status_code, 400)

    def test_marca_la_vuelta_para_abrir_el_picker(self):
        from google_auth.views import _mark_connected

        self.assertEqual(_mark_connected("/tableros/nuevo/"), "/tableros/nuevo/?google=connected")
        self.assertEqual(_mark_connected("/tableros/5/edit/?a=1"), "/tableros/5/edit/?a=1&google=connected")

    @override_settings(**CLIENT)
    def test_disconnect_borra_la_conexion(self):
        services.save_credentials(self.user, credentials())
        r = self.client.post("/auth/google/disconnect/")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(GoogleCredential.objects.filter(user=self.user).exists())

    def test_login_page_renders_template(self):
        self.client.logout()
        r = self.client.get("/login/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Bienvenido a UAPA DataViz AI")

    def test_login_page_autenticado_redirige_a_home(self):
        r = self.client.get("/login/")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r["Location"], "/")

    def test_logout_cierra_sesion(self):
        r = self.client.get("/logout/")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r["Location"], "/login/")
