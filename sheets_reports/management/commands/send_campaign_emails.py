"""Ejecuta los Envío de correoss a los que les toca (services/mailing.py). Lo llama el crontab:

    */5 * * * * cd /ruta/dataviz && venv/bin/python manage.py send_campaign_emails
"""
from django.core.management.base import BaseCommand

from sheets_reports.models import EmailCampaign, TaskRun
from sheets_reports.services import mailing

# Etiqueta con la que las corridas del cron quedan registradas en el admin (TaskRun).
TASK_LABEL = "Envío de correos"


class Command(BaseCommand):
    help = "Envía los correos de los tableros cuyo envío está activado y programado para ahora."

    def add_arguments(self, parser):
        parser.add_argument("--dashboard", type=int, help="Id de un solo tablero.")
        parser.add_argument("--force", action="store_true",
                            help="Ejecuta aunque no sea la hora o ya se haya ejecutado hoy.")

    def handle(self, *args, dashboard=None, force=False, **options):
        with TaskRun.record(TASK_LABEL) as run:
            sent = failed = pending = 0
            campaigns = EmailCampaign.objects.select_related("dashboard")
            campaigns = campaigns.filter(dashboard_id=dashboard) if dashboard else campaigns.filter(enabled=True)
            if dashboard and not campaigns.exists():
                self.stdout.write(f"El tablero {dashboard} no tiene envío de correos.")
            for item in campaigns:
                if not force and not mailing.is_due(item):
                    continue
                result = mailing.run_campaign(item, force=force)
                if result is not None:
                    sent += result["sent"]
                    failed += result["failed"]
                    pending += result["pending"]
                    self.stdout.write(f"{item.dashboard}: {result['sent']} enviados, "
                                      f"{result['failed']} con error, {result['pending']} pendientes")
            run.detail = (f"{sent} enviados, {failed} con error, {pending} pendientes"
                          if sent or failed or pending else "Sin envíos a esta hora.")
