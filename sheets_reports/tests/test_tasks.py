"""Tareas en segundo plano (`TaskRun`): el registro de una corrida con su estado y duración, la
poda de las viejas y el registro que dejan los comandos del crontab."""
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from sheets_reports.models import Dashboard, DataSource, TaskRun


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


class SyncGeminiFilesTests(TestCase):
    RESYNC = "sheets_reports.management.commands.sync_gemini_files.resync_source_file"

    def setUp(self):
        user = get_user_model().objects.create_user("u", "u@a.com", "pass")
        dashboard = Dashboard.objects.create(owner=user, nombre="Tablero")
        hoy = timezone.now()
        self.con_chat = DataSource.objects.create(dashboard=dashboard, sheet_id="a", gemini_file_id="files/a",
                                                  chat_used_at=hoy)
        self.otra = DataSource.objects.create(dashboard=dashboard, sheet_id="b", gemini_file_id="files/b",
                                              chat_used_at=hoy - timedelta(days=2))
        self.sin_chat = DataSource.objects.create(dashboard=dashboard, sheet_id="c")

    def test_sync_gemini_registra_la_corrida(self):
        with mock.patch(self.RESYNC, side_effect=["uploaded", "current"]) as mock_resync:
            call_command("sync_gemini_files")
        self.assertEqual({c.args[0].pk for c in mock_resync.call_args_list}, {self.con_chat.pk, self.otra.pk})
        run = TaskRun.objects.get()
        self.assertEqual(run.label, "Subida de fuentes a Gemini")
        self.assertEqual(run.status, TaskRun.OK)
        self.assertEqual(run.detail, "1 subidas, 1 vigentes, 0 sin uso, 0 vacías, 0 con error")

    @override_settings(GEMINI_SYNC_IDLE_DAYS=7)
    def test_sync_gemini_se_salta_las_fuentes_sin_uso_reciente(self):
        """Una fuente sin consultas en los últimos días (o nunca marcada) no se sube aunque su
        archivo haya expirado o la hoja haya cambiado: la próxima consulta la sube."""
        self.otra.chat_used_at = timezone.now() - timedelta(days=8)
        self.otra.save()
        self.sin_chat.gemini_file_id = "files/c"
        self.sin_chat.save()
        with mock.patch(self.RESYNC, return_value="uploaded") as mock_resync:
            call_command("sync_gemini_files")
        self.assertEqual([c.args[0].pk for c in mock_resync.call_args_list], [self.con_chat.pk])
        self.assertEqual(TaskRun.objects.get().detail, "1 subidas, 0 vigentes, 2 sin uso, 0 vacías, 0 con error")

    def test_sync_gemini_una_fuente_sin_uso_se_puede_forzar(self):
        self.otra.chat_used_at = None
        self.otra.save()
        with mock.patch(self.RESYNC, return_value="uploaded") as mock_resync:
            call_command("sync_gemini_files", source=self.otra.pk)
        self.assertEqual([c.args[0].pk for c in mock_resync.call_args_list], [self.otra.pk])

    def test_sync_gemini_error_en_una_fuente_sigue_y_marca_error(self):
        def resync(source):
            if source.pk == self.con_chat.pk:
                raise RuntimeError("cuota agotada")
            return "uploaded"

        with mock.patch(self.RESYNC, side_effect=resync) as mock_resync:
            with self.assertRaises(CommandError):
                call_command("sync_gemini_files")
        self.assertEqual(mock_resync.call_count, 2)
        run = TaskRun.objects.get()
        self.assertEqual(run.status, TaskRun.ERROR)
        self.assertIn("1 subidas, 0 vigentes, 0 sin uso, 0 vacías, 1 con error", run.detail)
        self.assertIn(f"fuente #{self.con_chat.pk}: RuntimeError: cuota agotada", run.detail)

    def test_sync_gemini_sin_fuentes_con_chat(self):
        DataSource.objects.update(gemini_file_id="")
        call_command("sync_gemini_files")
        self.assertEqual(TaskRun.objects.get().detail, "Ninguna fuente usa el chat.")
