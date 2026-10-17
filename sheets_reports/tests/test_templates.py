from django.test import TestCase
from django.urls import reverse
from django.contrib.auth.models import User
from sheets_reports.models import Dashboard, DataSource, Widget


class DashboardTemplateTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="templateuser", password="password")
        self.client.login(username="templateuser", password="password")
        self.dashboard = Dashboard.objects.create(
            nombre="Ventas Trimestrales",
            owner=self.user,
            is_template=False,
        )
        self.source = DataSource.objects.create(
            dashboard=self.dashboard,
            sheet_id="sheet_sample_123",
            name="Ventas",
        )
        self.widget = Widget.objects.create(
            dashboard=self.dashboard,
            source=self.source,
            type="bar",
            title="Ventas por Mes",
            fields={"dimensions": ["mes"], "metrics": [{"field": "total", "agg": "sum"}]},
        )

    def test_save_dashboard_as_template(self):
        fake_thumb = "data:image/jpeg;base64,/9j/4AAQSkZJRg=="
        r = self.client.post(
            reverse("dashboard_save_as_template", kwargs={"dashboard_id": self.dashboard.id}),
            data={"thumbnail": fake_thumb},
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 201)
        data = r.json()
        self.assertTrue(data["is_template"])
        self.assertIn("plantilla", data["nombre"].lower())
        self.assertEqual(data["thumbnail"], fake_thumb)
        self.assertEqual(data["owner"], "templateuser")

        # Verificar que en base de datos existe la plantilla clonada con thumbnail
        template = Dashboard.objects.get(id=data["id"])
        self.assertTrue(template.is_template)
        self.assertEqual(template.thumbnail, fake_thumb)
        self.assertEqual(template.sources.count(), 1)
        self.assertEqual(template.widgets.count(), 1)

    def test_dashboard_list_filters_templates(self):
        # Crear una plantilla
        Dashboard.objects.create(
            nombre="Plantilla Modelo",
            owner=self.user,
            is_template=True,
        )

        # GET /api/dashboards/ debe traer solo tableros de trabajo (no plantillas)
        r = self.client.get(reverse("dashboard_list"))
        self.assertEqual(r.status_code, 200)
        items = r.json()
        self.assertEqual(len(items), 1)
        self.assertFalse(items[0]["is_template"])
        self.assertEqual(items[0]["id"], self.dashboard.id)

        # GET /api/dashboards/?is_template=1 debe traer solo plantillas
        r_tpl = self.client.get(reverse("dashboard_list") + "?is_template=1")
        self.assertEqual(r_tpl.status_code, 200)
        tpl_items = r_tpl.json()
        self.assertEqual(len(tpl_items), 1)
        self.assertTrue(tpl_items[0]["is_template"])
        self.assertEqual(tpl_items[0]["nombre"], "Plantilla Modelo")

    def test_templates_visible_to_all_users(self):
        # Crear plantilla con el usuario A
        Dashboard.objects.create(
            nombre="Plantilla Global",
            owner=self.user,
            is_template=True,
        )
        # Iniciar sesión como usuario B
        other_user = User.objects.create_user(username="otheruser", password="password")
        self.client.login(username="otheruser", password="password")

        # El usuario B debe ver la plantilla creada por el usuario A
        r_tpl = self.client.get(reverse("dashboard_list") + "?is_template=1")
        self.assertEqual(r_tpl.status_code, 200)
        tpl_items = r_tpl.json()
        self.assertEqual(len(tpl_items), 1)
        self.assertTrue(tpl_items[0]["is_template"])
        self.assertEqual(tpl_items[0]["nombre"], "Plantilla Global")

    def test_use_template_creates_editable_copy(self):
        # Crear una plantilla con fuentes y widgets
        template = Dashboard.objects.create(
            nombre="Tablero Financiero (plantilla)",
            owner=self.user,
            is_template=True,
        )
        source = DataSource.objects.create(dashboard=template, sheet_id="fin_123", name="Finanzas")
        Widget.objects.create(
            dashboard=template,
            source=source,
            type="kpi",
            title="Margen Neto",
            fields={"metrics": [{"field": "margen", "agg": "sum"}]},
        )

        r = self.client.post(reverse("dashboard_use_template", kwargs={"dashboard_id": template.id}))
        self.assertEqual(r.status_code, 201)
        data = r.json()
        self.assertFalse(data["is_template"])
        self.assertEqual(data["nombre"], "Tablero Financiero")

        new_board = Dashboard.objects.get(id=data["id"])
        self.assertFalse(new_board.is_template)
        self.assertEqual(new_board.sources.count(), 1)
        self.assertEqual(new_board.widgets.count(), 1)
        self.assertEqual(new_board.widgets.first().title, "Margen Neto")

    def test_board_view_shows_template_badge_and_edit_button(self):
        template = Dashboard.objects.create(
            nombre="Plantilla RRHH",
            owner=self.user,
            is_template=True,
        )
        r = self.client.get(reverse("board_view", kwargs={"dashboard_id": template.id}))
        self.assertEqual(r.status_code, 200)
        html = r.content.decode("utf-8")
        self.assertIn("Plantilla", html)
        self.assertIn('id="use-template-btn"', html)
        self.assertIn("Editar", html)
        self.assertIn(f"useTemplate({template.id})", html)

    def test_clone_includes_calculated_fields_and_styles_but_excludes_email_campaign(self):
        from sheets_reports.models import EmailCampaign

        # Configurar campos calculados en la fuente
        calc_field = {
            "id": "calc_1",
            "name": "Margen Porcentual",
            "formula": "([Ventas] - [Costos]) / [Ventas] * 100",
            "format": "percent",
        }
        self.source.calculated_fields = [calc_field]
        self.source.columns = [
            {"name": "Ventas", "label": "Ventas Totales", "type": "number", "include": True},
            {"name": "mes", "label": "Mes", "type": "text", "include": True, "scale": "nominal"},
        ]
        self.source.save()

        # Configurar estilos detallados en el widget
        widget_style = {
            "color_scheme": "ocean",
            "horizontal": True,
            "stacked": True,
            "barWidth": 80,
            "showGrid": False,
        }
        self.widget.style = widget_style
        self.widget.save()

        # Crear una campaña de correo en el tablero original
        EmailCampaign.objects.create(
            dashboard=self.dashboard,
            enabled=True,
            subject="Reporte Trimestral",
            body="Adjunto reporte.",
            daily_limit=150,
        )

        # 1. Guardar como plantilla
        r_tpl = self.client.post(reverse("dashboard_save_as_template", kwargs={"dashboard_id": self.dashboard.id}))
        self.assertEqual(r_tpl.status_code, 201)
        tpl_id = r_tpl.json()["id"]
        template = Dashboard.objects.get(id=tpl_id)

        # Verificar que la plantilla clonó calculated_fields, columns y styles
        tpl_source = template.sources.first()
        self.assertEqual(tpl_source.calculated_fields, [calc_field])
        self.assertEqual(tpl_source.columns, self.source.columns)
        tpl_widget = template.widgets.first()
        self.assertEqual(tpl_widget.style, widget_style)

        # Verificar que la campaña de correo NO se clonó a la plantilla
        self.assertFalse(hasattr(template, "email_campaign"))
        self.assertIsNone(getattr(template, "email_campaign", None))

        # 2. Usar la plantilla para crear un nuevo tablero
        r_use = self.client.post(reverse("dashboard_use_template", kwargs={"dashboard_id": template.id}))
        self.assertEqual(r_use.status_code, 201)
        new_board_id = r_use.json()["id"]
        new_board = Dashboard.objects.get(id=new_board_id)

        # Verificar que el nuevo tablero tiene calculated_fields, columns y styles
        new_source = new_board.sources.first()
        self.assertEqual(new_source.calculated_fields, [calc_field])
        self.assertEqual(new_source.columns, self.source.columns)
        new_widget = new_board.widgets.first()
        self.assertEqual(new_widget.style, widget_style)

        # Verificar que la campaña de correo NO existe en el nuevo tablero
        self.assertFalse(hasattr(new_board, "email_campaign"))
        self.assertIsNone(getattr(new_board, "email_campaign", None))

    def test_home_renders_template_card_structure(self):
        r = self.client.get(reverse("home"))
        self.assertEqual(r.status_code, 200)
        html = r.content.decode("utf-8")
        self.assertIn("Creado por:", html)
        self.assertIn("templateViewUrl(t)", html)
