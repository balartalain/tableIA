from django.test import TestCase
from django.urls import reverse
from django.contrib.auth.models import User
from sheets_reports.models import Dashboard, DataSource


class BoardRulerTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="testuser", password="password")
        self.client.login(username="testuser", password="password")
        self.dashboard = Dashboard.objects.create(
            nombre="Tablero con regla",
            owner=self.user,
        )
        self.source = DataSource.objects.create(
            dashboard=self.dashboard,
            sheet_id="sheet123",
        )

    def test_board_editor_renders_board_ruler(self):
        r = self.client.get(reverse("board_editor", kwargs={"dashboard_id": self.dashboard.id}))
        self.assertEqual(r.status_code, 200)
        html = r.content.decode("utf-8")

        # Verificar que el componente modularizado board_ruler está incluido
        self.assertIn('id="board-ruler"', html)
        self.assertIn('id="board-ruler-guides"', html)
        self.assertIn("boardRuler()", html)
        self.assertIn("board-ruler.js", html)

        # Verificar que la regla vieja de depuración en rojo fue eliminada
        self.assertNotIn(".grid-numbers-header", html)
        self.assertNotIn("rgba(239, 68, 68, 0.8)", html)

    def test_board_ruler_template_syntax(self):
        from django.template.loader import render_to_string
        rendered = render_to_string("sheets_reports/board_ruler.html")
        self.assertIn('id="board-ruler"', rendered)
        self.assertIn('id="board-ruler-guides"', rendered)
        self.assertIn("grid-cols-12", rendered)
