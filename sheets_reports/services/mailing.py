"""
Envío de correos de un tablero (`EmailCampaign`): cada destinatario recibe el mensaje una
sola vez. El crontab llama a `manage.py send_campaign_emails`, que ejecuta las campañas a
las que les toca (`is_due`); la ejecución envía lotes (`batch_size` correos por remitente;
los remitentes son cuentas de Gmail con contraseña de aplicación), con un minuto entre uno y
otro, hasta llegar al máximo del día (`daily_limit`), y deja su bitácora en `EmailLog`. No hace HTTP: las vistas (views_email.py) son adaptadores sobre este servicio.
"""
import base64
import csv
import hashlib
import io
import re
import smtplib
from datetime import datetime, time, timedelta
from html import unescape
from time import sleep
from zoneinfo import ZoneInfo

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import EmailMultiAlternatives, get_connection
from django.core.validators import validate_email
from django.db.models import Count, Max, Q
from django.db.models.functions import TruncDate
from django.utils import timezone
from django.utils.html import strip_tags

from sheets_reports.models import EmailCampaign, EmailLog, EmailRecipient

# Intentos por destinatario antes de darlo por fallido.
MAX_ATTEMPTS = 3
SMTP_TIMEOUT_S = 30
# Segundos entre un lote y el siguiente.
BATCH_PAUSE_S = 60
# El envío renueva su candado (`running_since`) en cada lote: uno más viejo que esto es de
# un proceso que murió.
LOCK_STALE = timedelta(minutes=10)
# Errores del remitente (no del destinatario): se deja de usar esa cuenta en la ejecución.
SENDER_ERRORS = (smtplib.SMTPServerDisconnected, smtplib.SMTPSenderRefused,
                 smtplib.SMTPAuthenticationError, smtplib.SMTPConnectError)


class MailingError(Exception):
    """Algo que el usuario puede corregir (contraseña ilegible, sin remitente, SMTP)."""


# ------------------------------------------------------------- contraseñas
def _fernet() -> Fernet:
    key = settings.EMAIL_ENCRYPTION_KEY
    if not key:
        key = base64.urlsafe_b64encode(hashlib.sha256(settings.SECRET_KEY.encode()).digest())
    return Fernet(key)


def encrypt_password(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode()


def decrypt_password(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        raise MailingError("La contraseña guardada no se puede leer: escríbela de nuevo.")


# ----------------------------------------------------------- destinatarios
def _is_email(value: str) -> bool:
    try:
        validate_email(value)
    except ValidationError:
        return False
    return True


def parse_recipients_csv(content: bytes) -> tuple[list[str], list[str]]:
    """Los correos de un CSV → (válidos sin repetir, en minúsculas; celdas que no son un
    correo). De cada fila se toma la primera celda que sea un correo; una primera fila sin
    ninguno es el encabezado."""
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("latin-1")
    try:
        dialect = csv.Sniffer().sniff(text[:2048], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    valid, invalid, seen = [], [], set()
    for index, row in enumerate(csv.reader(io.StringIO(text), dialect)):
        cells = [cell.strip() for cell in row if cell and cell.strip()]
        if not cells:
            continue
        email = next((cell.lower() for cell in cells if _is_email(cell)), None)
        if email is None:
            if index > 0:
                invalid.append(cells[0])
            continue
        if email not in seen:
            seen.add(email)
            valid.append(email)
    return valid, invalid


# ------------------------------------------------------------ programación
def local_now(now: datetime | None = None) -> datetime:
    return (now or timezone.now()).astimezone(ZoneInfo(settings.CAMPAIGN_TIME_ZONE))


def _is_scheduled_day(campaign: EmailCampaign, day) -> bool:
    return campaign.frequency == EmailCampaign.DAILY or day.weekday() in (campaign.weekdays or [])


def _has_pending(campaign: EmailCampaign) -> bool:
    return campaign.recipients.filter(status=EmailRecipient.PENDING).exists()


def _day_start(current: datetime) -> datetime:
    return datetime.combine(current.date(), time.min, tzinfo=current.tzinfo)


def _sent_today(campaign: EmailCampaign, current: datetime) -> int:
    start = _day_start(current)
    return campaign.recipients.filter(status=EmailRecipient.SENT, sent_at__gte=start,
                                      sent_at__lt=start + timedelta(days=1)).count()


def is_due(campaign: EmailCampaign, now: datetime | None = None) -> bool:
    """Le toca ejecutarse: activada, con pendientes, hoy es día de envío, ya pasó la hora,
    el día no está cerrado (`last_run_on`) y queda cupo del máximo diario. La ejecución
    cierra el día al terminar; una que se cortó a medias vuelve a tocarle y se retoma."""
    current = local_now(now)
    return (campaign.enabled
            and campaign.last_run_on != current.date()
            and _is_scheduled_day(campaign, current.date())
            and current.time() >= campaign.send_time
            and _has_pending(campaign)
            and _sent_today(campaign, current) < campaign.daily_limit)


def next_run(campaign: EmailCampaign, now: datetime | None = None) -> datetime | None:
    """Cuándo es la próxima ejecución (hora local), o None si no hay nada por enviar. Con el
    envío de hoy sin terminar es ahora mismo."""
    if not campaign.enabled or not _has_pending(campaign):
        return None
    current = local_now(now)
    for offset in range(8):
        day = current.date() + timedelta(days=offset)
        if not _is_scheduled_day(campaign, day) or campaign.last_run_on == day:
            continue
        if offset == 0 and _sent_today(campaign, current) >= campaign.daily_limit:
            continue
        return max(datetime.combine(day, campaign.send_time, tzinfo=current.tzinfo), current)
    return None


# ------------------------------------------------------------------ mensaje
# Cierres de bloque que en texto plano pasan a ser saltos de línea.
_BLOCK_END_RE = re.compile(r"(?i)<\s*(?:br\s*/?|/p\s*|/div\s*|/li\s*|/h[1-6]\s*|/blockquote\s*)>")


def html_to_text(html: str) -> str:
    """El HTML de un mensaje → su alternativa en texto plano (clientes de correo sin HTML)."""
    if not html:
        return ""
    text = _BLOCK_END_RE.sub("\n", html)
    text = unescape(strip_tags(text))
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _message(campaign: EmailCampaign, sender_email: str, to: list[str], connection) -> EmailMultiAlternatives:
    """El mensaje de la campaña: HTML, con su alternativa en texto plano."""
    message = EmailMultiAlternatives(campaign.subject, html_to_text(campaign.body),
                                     from_email=sender_email, to=to, connection=connection)
    message.attach_alternative(campaign.body, "text/html")
    return message


# ------------------------------------------------------------------- envío
def _connection(sender):
    return get_connection(
        backend=settings.CAMPAIGN_EMAIL_BACKEND,
        host=settings.CAMPAIGN_SMTP_HOST,
        port=settings.CAMPAIGN_SMTP_PORT,
        username=sender.email,
        password=decrypt_password(sender.password_encrypted),
        use_tls=True,
        timeout=SMTP_TIMEOUT_S,
        fail_silently=False,
    )


def _log(campaign, message: str, level: str = EmailLog.INFO, recipient: str = "", sender: str = "") -> None:
    EmailLog.objects.create(campaign=campaign, level=level, message=message,
                            recipient_email=recipient, sender_email=sender)


def _send_group(campaign: EmailCampaign, sender, group: list, now: datetime | None = None) -> tuple[int, list, bool]:
    """Los correos de un remitente por una sola conexión → (enviados, ids de los que fallaron,
    si la cuenta sigue pudiendo enviar). Si no puede, sus destinatarios quedan pendientes."""
    try:
        connection = _connection(sender)
        connection.open()
    except Exception as e:
        _log(campaign, f"No se pudo iniciar sesión con el remitente: {e}", EmailLog.ERROR, sender=sender.email)
        return 0, [], False
    sent, failed, alive = 0, [], True
    try:
        for recipient in group:
            try:
                _message(campaign, sender.email, [recipient.email], connection).send()
            except SENDER_ERRORS as e:
                _log(campaign, f"El remitente dejó de enviar: {e}", EmailLog.ERROR, sender=sender.email)
                alive = False
                break
            except Exception as e:
                recipient.attempts += 1
                recipient.error = str(e)
                if recipient.attempts >= MAX_ATTEMPTS:
                    recipient.status = EmailRecipient.FAILED
                recipient.sender_email = sender.email
                failed.append(recipient.id)
                _log(campaign, f"No se pudo enviar (intento {recipient.attempts} de {MAX_ATTEMPTS}): {e}",
                     EmailLog.ERROR, recipient=recipient.email, sender=sender.email)
            else:
                recipient.attempts += 1
                recipient.status = EmailRecipient.SENT
                recipient.sent_at = now or timezone.now()
                recipient.sender_email = sender.email
                recipient.error = ""
                sent += 1
            recipient.save(update_fields=["attempts", "status", "sent_at", "sender_email", "error"])
    finally:
        try:
            connection.close()
        except Exception:
            pass
    return sent, failed, alive


def _close_day(campaign: EmailCampaign, today) -> None:
    """Hoy ya no se envía más: `is_due` no vuelve a dar la campaña hasta mañana."""
    EmailCampaign.objects.filter(id=campaign.id).update(last_run_on=today)
    campaign.last_run_on = today


def run_campaign(campaign: EmailCampaign, now: datetime | None = None, force: bool = False) -> dict | None:
    """El envío del día: lotes de `batch_size` correos por remitente, repartidos en ronda y con
    `BATCH_PAUSE_S` entre uno y otro, hasta el máximo diario → {sent, failed, pending}. None si
    hay otro envío en curso (dos crons solapados) o si el día ya está cerrado y no es `force`."""
    moment = now or timezone.now()
    current = local_now(now)
    claimed = EmailCampaign.objects.filter(
        Q(running_since__isnull=True) | Q(running_since__lt=moment - LOCK_STALE), id=campaign.id)
    if not force:
        claimed = claimed.exclude(last_run_on=current.date())
    if not claimed.update(running_since=moment):
        return None
    try:
        return _run_day(campaign, current, now)
    finally:
        EmailCampaign.objects.filter(id=campaign.id).update(running_since=None)


def _run_day(campaign: EmailCampaign, current: datetime, now: datetime | None) -> dict:
    today = current.date()
    senders = [s for s in campaign.senders.all() if s.password_encrypted]
    pending = campaign.recipients.filter(status=EmailRecipient.PENDING)
    if not senders:
        _log(campaign, "No hay remitentes con contraseña: no se envió nada.", EmailLog.ERROR)
        _close_day(campaign, today)
        return {"sent": 0, "failed": 0, "pending": pending.count()}

    sent = failed = batches = 0
    # Los que fallaron en esta ejecución: se reintentan el próximo día de envío, no al minuto.
    tried = set()
    while senders:
        quota = max(campaign.daily_limit - _sent_today(campaign, current), 0)
        batch = list(pending.exclude(id__in=tried).order_by("id")[:min(quota, campaign.batch_size * len(senders))])
        if not batch:
            break
        if batches:
            sleep(BATCH_PAUSE_S)
            if local_now(now).date() != today:
                break
        else:
            _log(campaign, f"Inicio del envío: lotes de {campaign.batch_size} por remitente, con "
                           f"{len(senders)} {'remitente' if len(senders) == 1 else 'remitentes'}.")
        batches += 1
        batch_sent, batch_failed, alive = 0, 0, []
        for index, sender in enumerate(senders):
            group_sent, group_failed, sender_alive = _send_group(campaign, sender, batch[index::len(senders)], now)
            batch_sent += group_sent
            batch_failed += len(group_failed)
            tried.update(group_failed)
            if sender_alive:
                alive.append(sender)
        # Un remitente que no puede enviar no se vuelve a usar en esta ejecución.
        senders = alive
        sent += batch_sent
        failed += batch_failed
        _log(campaign, f"Lote {batches}: {batch_sent} enviados, {batch_failed} con error.")
        # Renueva el candado: el envío sigue vivo.
        EmailCampaign.objects.filter(id=campaign.id).update(running_since=timezone.now())

    _close_day(campaign, today)
    remaining = pending.count()
    reached = _sent_today(campaign, current) >= campaign.daily_limit
    if not batches:
        _log(campaign, "Ya se alcanzó el máximo de correos de hoy." if remaining
             else "No hay destinatarios pendientes.")
    else:
        _log(campaign, f"Fin del envío: {sent} enviados, {failed} con error, {remaining} pendientes."
             + (" Envío completado." if not remaining
                else " Se alcanzó el máximo de correos de hoy." if reached else ""))
    return {"sent": sent, "failed": failed, "pending": remaining}


def send_test(campaign: EmailCampaign, to: str) -> str:
    """El mensaje de la campaña a una sola dirección, con el primer remitente → su correo."""
    sender = next((s for s in campaign.senders.all() if s.password_encrypted), None)
    if sender is None:
        raise MailingError("Guarda al menos un remitente con su contraseña de aplicación.")
    try:
        connection = _connection(sender)
        _message(campaign, sender.email, [to], connection).send()
    except MailingError:
        raise
    except Exception as e:
        raise MailingError(f"No se pudo enviar con {sender.email}: {e}")
    return sender.email


# ----------------------------------------------------------------- reporte
# Estado del envío que muestra la franja de la pestaña «Seguimiento de correos».
RUNNING, DONE, STOPPED, SCHEDULED = "running", "done", "stopped", "scheduled"
# Días que muestra el gráfico de enviados por día y motivos de la tabla de errores.
STATS_DAYS = 30
STATS_ERRORS = 10
# «(intento 2 de 3)»: fuera, para que el mismo motivo cuente junto.
_ATTEMPT_RE = re.compile(r" \(intento \d+ de \d+\)")


def _estimated_end(campaign: EmailCampaign, current: datetime, pending: int, sent_today: int):
    """El día en que se acaban los pendientes al ritmo del máximo diario, o None si no hay
    pendientes o falta más de un año."""
    if not pending or not campaign.enabled:
        return None
    for offset in range(366):
        day = current.date() + timedelta(days=offset)
        if not _is_scheduled_day(campaign, day) or campaign.last_run_on == day:
            continue
        pending -= max(campaign.daily_limit - sent_today, 0) if offset == 0 else campaign.daily_limit
        if pending <= 0:
            return day
    return None


def report_summary(campaign: EmailCampaign, now: datetime | None = None) -> dict:
    """Lo que la pestaña «Seguimiento de correos» consulta cada pocos segundos: en qué está el envío, los
    totales por estado y el id del último log (si cambian, hay novedades)."""
    current = local_now(now)
    counts = dict(campaign.recipients.values_list("status").annotate(n=Count("id")))
    pending = counts.get(EmailRecipient.PENDING, 0)
    sent_today = _sent_today(campaign, current)
    last_log = campaign.logs.order_by("-id").values_list("id", flat=True).first()
    upcoming = next_run(campaign, now)
    running_since = campaign.running_since
    if running_since and running_since >= current - LOCK_STALE:
        state = RUNNING
    elif not pending:
        state = DONE
    elif campaign.last_run_on == current.date() and sent_today < campaign.daily_limit:
        state = STOPPED
    else:
        state = SCHEDULED
    error = campaign.logs.filter(level=EmailLog.ERROR, created_at__gte=_day_start(current)).order_by("-id").first()
    end = _estimated_end(campaign, current, pending, sent_today)
    return {
        "state": state,
        "total": sum(counts.values()),
        "sent": counts.get(EmailRecipient.SENT, 0),
        "pending": pending,
        "failed": counts.get(EmailRecipient.FAILED, 0),
        "sent_today": sent_today,
        "daily_limit": campaign.daily_limit,
        "last_log_id": last_log or 0,
        "next_run": upcoming.isoformat() if upcoming else None,
        "estimated_end": end.isoformat() if end else None,
        "last_error": {"message": error.message, "sender_email": error.sender_email,
                       "created_at": error.created_at.isoformat()} if error else None,
    }


def report_stats(campaign: EmailCampaign, now: datetime | None = None) -> dict:
    """Lo que la pestaña «Seguimiento de correos» pide cuando hay novedades: enviados por día, el detalle por
    remitente y los errores agrupados por motivo."""
    current = local_now(now)
    sent = campaign.recipients.filter(status=EmailRecipient.SENT, sent_at__isnull=False)
    by_day = (sent.annotate(day=TruncDate("sent_at", tzinfo=current.tzinfo))
              .values("day").annotate(n=Count("id")).order_by("-day")[:STATS_DAYS])

    senders = {s.email: {"email": s.email, "active": True} for s in campaign.senders.all()}

    def row(email: str) -> dict:
        item = senders.setdefault(email, {"email": email, "active": False})
        for key, default in (("sent_today", 0), ("sent_total", 0), ("errors", 0),
                             ("last_error", ""), ("last_error_at", None)):
            item.setdefault(key, default)
        return item

    for email in list(senders):
        row(email)
    today = Count("id", filter=Q(sent_at__gte=_day_start(current)))
    for item in sent.exclude(sender_email="").values("sender_email").annotate(total=Count("id"), today=today):
        row(item["sender_email"]).update(sent_total=item["total"], sent_today=item["today"])

    errors = campaign.logs.filter(level=EmailLog.ERROR)
    for item in errors.exclude(sender_email="").values("sender_email").annotate(n=Count("id"), last=Max("id")):
        last = errors.get(id=item["last"])
        row(item["sender_email"]).update(errors=item["n"], last_error=last.message,
                                         last_error_at=last.created_at.isoformat())

    reasons = {}
    for item in errors.values("message").annotate(n=Count("id"), last=Max("created_at")):
        reason = reasons.setdefault(_ATTEMPT_RE.sub("", item["message"]), {"count": 0, "last_at": item["last"]})
        reason["count"] += item["n"]
        reason["last_at"] = max(reason["last_at"], item["last"])
    top = sorted(reasons.items(), key=lambda pair: -pair[1]["count"])[:STATS_ERRORS]
    return {
        "daily_limit": campaign.daily_limit,
        "by_day": [{"date": item["day"].isoformat(), "sent": item["n"]} for item in list(by_day)[::-1]],
        "by_sender": sorted(senders.values(), key=lambda item: (not item["active"], item["email"])),
        "errors": [{"message": message, "count": reason["count"], "last_at": reason["last_at"].isoformat()}
                   for message, reason in top],
    }
