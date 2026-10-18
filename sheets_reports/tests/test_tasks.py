"""Tareas en segundo plano (`TaskRun`): el registro de una corrida con su estado y duración, la
poda de las viejas y el registro que deja el comando del crontab."""
from unittest import mock

from django.core.management import call_command
from django.test import TestCase

from sheets_reports.models import TaskRun


class TaskRunTests(TestCase):
    def test_record_ok_registra_fin_y_duracion(self):
        with TaskRun.record("Envío de correos") as run:
            run.detail = "Sin envíos a esta hora."
        run.refresh_from_db()
        self.assertEqual(run.status, TaskRun.OK)
        self.assertIsNotNone(run.finished_at)
        self.assertIsNotNone(run.duration)
        self.assertEqual(run.detail, "Sin envíos a esta hora.")

    def test_record_error_guarda_el_mensaje_y_relanza(self):
        run = None
        with self.assertRaises(ValueError):
            with TaskRun.record("Envío de correos") as run:
                raise ValueError("boom")
        run.refresh_from_db()
        self.assertEqual(run.status, TaskRun.ERROR)
        self.assertEqual(run.detail, "ValueError: boom")
        self.assertIsNotNone(run.finished_at)

    def test_poda_deja_las_ultimas_corridas(self):
        with mock.patch.object(TaskRun, "KEEP", 3):
            for _ in range(5):
                with TaskRun.record("Envío de correos"):
                    pass
        self.assertEqual(TaskRun.objects.filter(label="Envío de correos").count(), 3)

    def test_comando_del_crontab_registra_la_corrida(self):
        call_command("send_campaign_emails")
        run = TaskRun.objects.get()
        self.assertEqual(run.label, "Envío de correos")
        self.assertEqual(run.status, TaskRun.OK)
        self.assertIsNotNone(run.finished_at)
