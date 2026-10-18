"""Actividad del sistema (services/activity.py): el registro `ActivityLog`, el resumen de
trabajos programados y el endpoint del tab «Actividad» de la home (solo los tableros del dueño)."""
from datetime import time
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from sheets_reports.models import ActivityLog, EmailCampaign, EmailRecipient
from sheets_reports.services import activity, mailing
from sheets_reports.tests.fixtures import make_board

LOCMEM = override_settings(CAMPAIGN_EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
                           CAMPAIGN_TIME_ZONE="America/Santo_Domingo")


class ActivityLogServiceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("admin", "a@a.com", "x")
        self.dashboard, self.source = make_board(self.user)

    def test_log_event_guarda_los_datos(self):
        event = activity.log_event(self.dashboard, ActivityLog.EMAIL, "Hola",
                                   level=ActivityLog.ERROR, data={"k": 1}, widget_id=7)
        self.assertEqual(event.category, ActivityLog.EMAIL)
        self.assertEqual(event.level, ActivityLog.ERROR)
        self.assertEqual(event.data, {"k": 1})
        self.assertEqual(event.widget_id, 7)

    def test_poda_los_mas_viejos_al_pasar_del_tope(self):
        with mock.patch.object(activity, "MAX_PER_DASHBOARD", 3):
            for i in range(5):
                activity.log_event(self.dashboard, ActivityLog.SYSTEM, f"evento {i}")
        rows = list(ActivityLog.objects.filter(dashboard=self.dashboard).values_list("message", flat=True))
        self.assertEqual(len(rows), 3)
        self.assertIn("evento 4", rows)
        self.assertNotIn("evento 0", rows)

    @LOCMEM
    def test_mailing_escribe_actividad_ademas_de_la_bitacora(self):
        campaign = EmailCampaign.objects.create(
            dashboard=self.dashboard, enabled=True, subject="Hola", body="Mensaje",
            daily_limit=100, send_time=time(8, 0))
        EmailRecipient.objects.create(campaign=campaign, email="p@uapa.edu.do")
        mailing.run_campaign(campaign, mailing.local_now())
        self.assertTrue(campaign.logs.exists())
        self.assertTrue(ActivityLog.objects.filter(
            dashboard=self.dashboard, category=ActivityLog.EMAIL).exists())


class ScheduledSummaryTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("admin", "a@a.com", "x")
        self.dashboard, self.source = make_board(self.user)

    def test_incluye_ia_fuente_y_correos(self):
        EmailCampaign.objects.create(
            dashboard=self.dashboard, enabled=True, subject="Hola", body="Mensaje",
            daily_limit=100, send_time=time(8, 0))
        tasks = activity.scheduled_summary(self.user)
        categories = {t["category"] for t in tasks}
        self.assertIn(ActivityLog.EMAIL, categories)
        self.assertIn(ActivityLog.SHEETS, categories)
        self.assertIn(ActivityLog.AI, categories)

    def test_sin_campana_activada_no_hay_tarea_de_correos(self):
        tasks = activity.scheduled_summary(self.user)
        self.assertNotIn(ActivityLog.EMAIL, {t["category"] for t in tasks})


class ActivityFeedEndpointTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("admin", "a@a.com", "x")
        self.client.force_login(self.user)
        self.dashboard, self.source = make_board(self.user)
        self.url = reverse("activity_feed")

    def test_devuelve_trabajos_y_eventos(self):
        activity.log_event(self.dashboard, ActivityLog.EMAIL, "Enviado")
        data = self.client.get(self.url).json()
        self.assertIn("tasks", data)
        self.assertEqual(data["total_rows"], 1)
        self.assertEqual(data["rows"][0]["message"], "Enviado")
        self.assertEqual(data["rows"][0]["dashboard"], self.dashboard.nombre)

    def test_no_muestra_tableros_de_otro_dueno(self):
        other = get_user_model().objects.create_user("bob", password="x")
        other_dashboard, _ = make_board(other, nombre="Ajeno")
        activity.log_event(other_dashboard, ActivityLog.SYSTEM, "Secreto")
        data = self.client.get(self.url).json()
        self.assertEqual(data["total_rows"], 0)

    def test_pagina_los_eventos(self):
        for i in range(3):
            activity.log_event(self.dashboard, ActivityLog.SYSTEM, f"evento {i}")
        data = self.client.get(self.url, {"page": 2, "size": 2}).json()
        self.assertEqual(data["total_rows"], 3)
        self.assertEqual(data["last_page"], 2)
        self.assertEqual(len(data["rows"]), 1)
