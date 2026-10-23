"""Mantiene en Gemini Files API la hoja de las fuentes que usan el chat de datos
(services/ai_data_chat.py), para que el chat no tenga que subirla mientras el usuario espera.
Solo las fuentes consultadas en los últimos GEMINI_SYNC_IDLE_DAYS días, y de ellas solo las
cuyo archivo expiró (Gemini lo borra a las 48 h) o cuya hoja cambió. Lo llama el crontab a
las 12:00 AM:

    0 0 * * * cd /ruta/TableIA && venv/bin/python manage.py sync_gemini_files
"""
import logging
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils import timezone

from sheets_reports.models import DataSource, TaskRun
from sheets_reports.services.ai_data_chat import resync_source_file

logger = logging.getLogger(__name__)

# Etiqueta con la que las corridas del cron quedan registradas en el admin (TaskRun).
TASK_LABEL = "Subida de fuentes a Gemini"


class Command(BaseCommand):
    help = ("Sube a Gemini las fuentes con uso reciente del chat de datos cuyo archivo expiró "
            "o cuya hoja cambió.")

    def add_arguments(self, parser):
        parser.add_argument("--source", type=int, help="Id de una sola fuente (sin mirar su uso).")

    def handle(self, *args, source=None, **options):
        with TaskRun.record(TASK_LABEL) as run:
            sources = DataSource.objects.exclude(gemini_file_id="")
            idle = 0
            if source:
                sources = sources.filter(pk=source)
            else:
                since = timezone.now() - timedelta(days=settings.GEMINI_SYNC_IDLE_DAYS)
                recent = Q(chat_used_at__gte=since)
                idle = sources.exclude(recent).count()
                sources = sources.filter(recent)
            counts = {"uploaded": 0, "current": 0, "empty": 0}
            errors = []
            for item in sources:
                try:
                    counts[resync_source_file(item)] += 1
                except Exception as exc:  # noqa: BLE001 - una fuente que falla no corta las demás
                    logger.exception("No se pudo resubir la fuente %s a Gemini", item.pk)
                    errors.append(f"fuente #{item.pk}: {type(exc).__name__}: {exc}")
            if not (any(counts.values()) or idle or errors):
                run.detail = "Ninguna fuente usa el chat."
                return
            summary = (f"{counts['uploaded']} subidas, {counts['current']} vigentes, {idle} sin uso, "
                       f"{counts['empty']} vacías, {len(errors)} con error")
            self.stdout.write(summary)
            if errors:
                raise CommandError("; ".join([summary, *errors]))
            run.detail = summary
