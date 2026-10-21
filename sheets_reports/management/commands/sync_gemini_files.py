"""Resube a Gemini Files API las fuentes que usan el chat de datos (services/ai_data_chat.py),
para que el archivo no expire (Gemini lo borra a las 48 h) y el chat no tenga que subirlo
mientras el usuario espera. Lo llama el crontab a las 12:00 AM:

    0 0 * * * cd /ruta/TableIA && venv/bin/python manage.py sync_gemini_files
"""
import logging

from django.core.management.base import BaseCommand, CommandError

from sheets_reports.models import DataSource, TaskRun
from sheets_reports.services.ai_data_chat import resync_source_file

logger = logging.getLogger(__name__)

# Etiqueta con la que las corridas del cron quedan registradas en el admin (TaskRun).
TASK_LABEL = "Subida de fuentes a Gemini"


class Command(BaseCommand):
    help = "Resube a Gemini las fuentes que ya usan el chat de datos (las que tienen archivo)."

    def add_arguments(self, parser):
        parser.add_argument("--source", type=int, help="Id de una sola fuente.")

    def handle(self, *args, source=None, **options):
        with TaskRun.record(TASK_LABEL) as run:
            sources = DataSource.objects.exclude(gemini_file_id="")
            if source:
                sources = sources.filter(pk=source)
            uploaded = empty = 0
            errors = []
            for item in sources:
                try:
                    if resync_source_file(item):
                        uploaded += 1
                    else:
                        empty += 1
                except Exception as exc:  # noqa: BLE001 - una fuente que falla no corta las demás
                    logger.exception("No se pudo resubir la fuente %s a Gemini", item.pk)
                    errors.append(f"fuente #{item.pk}: {type(exc).__name__}: {exc}")
            if not (uploaded or empty or errors):
                run.detail = "Ninguna fuente usa el chat."
                return
            summary = f"{uploaded} subidas, {empty} vacías, {len(errors)} con error"
            self.stdout.write(summary)
            if errors:
                raise CommandError("; ".join([summary, *errors]))
            run.detail = summary
