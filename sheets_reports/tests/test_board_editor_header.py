from django.test import TestCase
from django.urls import reverse
from django.contrib.auth.models import User
from sheets_reports.models import Dashboard, DataSource


class BoardEditorHeaderTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="testuser", password="password")
        self.client.login(username="testuser", password="password")
        self.dashboard = Dashboard.objects.create(
            nombre="Tablero de prueba",
            owner=self.user,
        )
        self.source = DataSource.objects.create(
            dashboard=self.dashboard,
            sheet_id="sheet123",
        )

    def test_board_editor_header_elements(self):
        r = self.client.get(reverse("board_editor", kwargs={"dashboard_id": self.dashboard.id}))
        self.assertEqual(r.status_code, 200)
        html = r.content.decode("utf-8")

        # Título y menú desplegable
        self.assertIn('id="board-title"', html)
        self.assertIn("boardHeaderMenu()", html)
        self.assertIn("Duplicar tablero", html)
        self.assertIn("Guardar como plantilla", html)
        self.assertIn("Configuración", html)
        self.assertIn("Salir al inicio", html)

        # Botón de fuentes central e indicador de autoguardado
        self.assertIn('id="sources-btn"', html)
        self.assertIn("Gestionar fuentes", html)

        # Botones de la zona derecha
        self.assertIn('id="ai-board-btn"', html)
        self.assertIn("Generar con IA", html)
        self.assertIn('id="preview-btn"', html)
        self.assertIn('id="share-btn"', html)

        # El botón independiente de correo ya no está en el header
        self.assertNotIn('id="email-btn"', html)

    def test_bottom_sheet_settings_tabs(self):
        r = self.client.get(reverse("board_editor", kwargs={"dashboard_id": self.dashboard.id}))
        self.assertEqual(r.status_code, 200)
        html = r.content.decode("utf-8")

        # Pestañas de configuración en el bottom sheet
        self.assertIn("switchSettingsTab('board')", html)
        self.assertIn("switchSettingsTab('email')", html)
        self.assertIn("Configuración del tablero", html)
        self.assertIn("Envíos de correo", html)
        self.assertIn('id="settings-board-name"', html)
        self.assertIn("Eliminar tablero definitivamente", html)

    def test_bottom_sheet_backdrop(self):
        r = self.client.get(reverse("board_editor", kwargs={"dashboard_id": self.dashboard.id}))
        self.assertEqual(r.status_code, 200)
        html = r.content.decode("utf-8")

        # Telón de fondo a pantalla completa
        self.assertIn('id="bottom-sheet-backdrop"', html)
        self.assertIn("fixed inset-0", html)
        self.assertIn("z-[35]", html)
        self.assertIn("bg-ink/40", html)
