"""Actividad del sistema para el tab «Actividad» de la home: qué trabajo recurrente existe,
cada cuánto corre y qué ha ido pasando. Los eventos se guardan en `ActivityLog` (una fila por
tablero); `scheduled_summary` describe el trabajo recurrente (el cron de correos, el refresco
de fuentes y los análisis de IA) sin depender de que exista una fila. Los prompts/respuestas de
la IA se sumarán después en `ActivityLog.data`.
"""
from sheets_reports.models import ActivityLog, EmailCampaign

# Filas de actividad que se conservan por tablero; al escribir se borran las más viejas.
MAX_PER_DASHBOARD = 500
# Cuántas se borran por pasada al podar (acota el trabajo de cada escritura).
PRUNE_BATCH = 500


def log_event(dashboard, category: str, message: str, level: str = ActivityLog.INFO,
              data: dict | None = None, widget_id: int | None = None,
              source_id: int | None = None) -> ActivityLog:
    """Registra un evento de un tablero y poda los más viejos si pasa del tope."""
    event = ActivityLog.objects.create(
        dashboard=dashboard, category=category, level=level, message=message,
        data=data or {}, widget_id=widget_id, source_id=source_id,
    )
    _prune(dashboard.id)
    return event


def _prune(dashboard_id: int) -> None:
    older = list(
        ActivityLog.objects.filter(dashboard_id=dashboard_id)
        .order_by("-id").values_list("id", flat=True)[MAX_PER_DASHBOARD:MAX_PER_DASHBOARD + PRUNE_BATCH]
    )
    if older:
        ActivityLog.objects.filter(id__in=older).delete()


def recent_events(user, page: int = 1, size: int = 50) -> dict:
    """Una página de la actividad de todos los tableros del usuario → {rows, total_rows, last_page}."""
    queryset = ActivityLog.objects.filter(dashboard__owner=user).select_related("dashboard")
    total = queryset.count()
    start = (page - 1) * size
    rows = [{
        "id": event.id,
        "created_at": event.created_at.isoformat(),
        "dashboard_id": event.dashboard_id,
        "dashboard": event.dashboard.nombre,
        "category": event.category,
        "level": event.level,
        "message": event.message,
        "data": event.data,
    } for event in queryset[start:start + size]]
    return {"rows": rows, "total_rows": total, "last_page": max(-(-total // size), 1)}


# ------------------------------------------------------- trabajo recurrente
# El cron del servidor dispara `manage.py send_campaign_emails` cada 5 minutos (ver
# docs/configuracion_correos.md); su frecuencia es cuánto puede atrasarse el arranque, no el
# ritmo de los correos, que lo marca la ejecución misma.
CRON_INTERVAL = "Cada 5 min (cron)"

# Estados del envío (los mismos que mailing.report_summary) → etiqueta e ícono del panel.
_STATE_LABELS = {
    "running": ("En curso", "warning"),
    "scheduled": ("Programado", "info"),
    "done": ("Terminado", "info"),
    "stopped": ("Detenido", "warning"),
}


def _email_task(campaign: EmailCampaign) -> dict:
    from sheets_reports.services import mailing

    summary = mailing.report_summary(campaign)
    state, level = _STATE_LABELS.get(summary["state"], ("Sin actividad", "info"))
    frequency = "todos los días" if campaign.frequency == EmailCampaign.DAILY else "días elegidos"
    return {
        "id": f"email-{campaign.dashboard_id}",
        "label": f"Envío de correos · {campaign.dashboard.nombre}",
        "category": ActivityLog.EMAIL,
        "interval": f"{CRON_INTERVAL} · {frequency} a las {campaign.send_time.strftime('%H:%M')}",
        "state": summary["state"],
        "state_label": state,
        "level": level,
        "detail": (f"{summary['sent']} enviados, {summary['pending']} pendientes, "
                   f"{summary['failed']} con error"),
        "last_run": campaign.last_run_on.isoformat() if campaign.last_run_on else None,
        "next_run": summary["next_run"],
    }


def _source_task(source) -> dict:
    from sheets_reports.services import sheets

    fetched = sheets.fetched_at(source)
    return {
        "id": f"source-{source.id}",
        "label": f"Refresco de datos · {source.sheet_name or source.sheet_id}",
        "category": ActivityLog.SHEETS,
        "interval": "Manual («Actualizar datos»)",
        "state": "ok" if fetched else "idle",
        "state_label": "Al día" if fetched else "Sin leer",
        "level": "info",
        "detail": "Se relee de Google al pulsar «Actualizar datos».",
        "last_run": fetched.isoformat() if fetched else None,
        "next_run": None,
    }


def _ai_task() -> dict:
    return {
        "id": "ai-insights",
        "label": "Análisis de IA de las tarjetas",
        "category": ActivityLog.AI,
        "interval": "A demanda · caché de 7 días",
        "state": "idle",
        "state_label": "A demanda",
        "level": "info",
        "detail": "Se genera al abrir el análisis de una tarjeta y se guarda 7 días.",
        "last_run": None,
        "next_run": None,
    }


def scheduled_summary(user) -> list[dict]:
    """El trabajo recurrente del usuario y su estado: el cron de correos por tablero, el
    refresco manual de cada fuente y los análisis de IA a demanda."""
    from sheets_reports.models import DataSource

    dashboards = list(user.dashboards.all())
    tasks = [_ai_task()]
    for dashboard in dashboards:
        campaign = getattr(dashboard, "email_campaign", None)
        if campaign and campaign.enabled:
            tasks.append(_email_task(campaign))
    sources = DataSource.objects.filter(dashboard__owner=user).select_related("dashboard")
    tasks.extend(_source_task(source) for source in sources)
    return tasks


def feed(user, page: int = 1, size: int = 50) -> dict:
    """Lo que consume el tab «Actividad»: el resumen de trabajos y la página de eventos."""
    return {"tasks": scheduled_summary(user), **recent_events(user, page, size)}
