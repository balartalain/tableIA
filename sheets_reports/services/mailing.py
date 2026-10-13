"""
Envío de correos de un tablero (`EmailCampaign`): cada destinatario recibe el mensaje una
sola vez. El crontab llama a `manage.py send_campaign_emails`, que ejecuta las campañas a
las que les toca (`is_due`); cada ejecución envía hasta el límite diario, repartido entre
los remitentes (cuentas de Gmail con contraseña de aplicación), y deja su bitácora en
`EmailLog`. No hace HTTP: las vistas (views_email.py) son adaptadores sobre este servicio.
"""
import base64
import csv
import hashlib
import io
import smtplib
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import EmailMessage, get_connection
from django.core.validators import validate_email
from django.db.models import Count
from django.utils import timezone

from sheets_reports.models import EmailCampaign, EmailLog, EmailRecipient

# Intentos por destinatario antes de darlo por fallido.
MAX_ATTEMPTS = 3
SMTP_TIMEOUT_S = 30
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


def is_due(campaign: EmailCampaign, now: datetime | None = None) -> bool:
    """Le toca ejecutarse: activada, con pendientes, hoy es día de envío, ya pasó la hora y
    hoy todavía no se ejecutó. Un cron atrasado la ejecuta igual, una sola vez por día."""
    current = local_now(now)
    return (campaign.enabled
            and campaign.last_run_on != current.date()
            and _is_scheduled_day(campaign, current.date())
            and current.time() >= campaign.send_time
            and _has_pending(campaign))


def next_run(campaign: EmailCampaign, now: datetime | None = None) -> datetime | None:
    """Cuándo es la próxima ejecución (hora local), o None si no hay nada por enviar."""
    if not campaign.enabled or not _has_pending(campaign):
        return None
    current = local_now(now)
    for offset in range(8):
        day = current.date() + timedelta(days=offset)
        if not _is_scheduled_day(campaign, day) or campaign.last_run_on == day:
            continue
        return datetime.combine(day, campaign.send_time, tzinfo=current.tzinfo)
    return None


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


def _sent_today(campaign: EmailCampaign, current: datetime) -> int:
    start = datetime.combine(current.date(), time.min, tzinfo=current.tzinfo)
    return campaign.recipients.filter(status=EmailRecipient.SENT, sent_at__gte=start,
                                      sent_at__lt=start + timedelta(days=1)).count()


def _send_group(campaign: EmailCampaign, sender, group: list, now: datetime | None = None) -> tuple[int, int]:
    """Los correos de un remitente por una sola conexión → (enviados, fallidos). Si la cuenta
    no puede enviar, sus destinatarios quedan pendientes para la próxima ejecución."""
    try:
        connection = _connection(sender)
        connection.open()
    except Exception as e:
        _log(campaign, f"No se pudo iniciar sesión con el remitente: {e}", EmailLog.ERROR, sender=sender.email)
        return 0, 0
    sent = failed = 0
    try:
        for recipient in group:
            try:
                EmailMessage(campaign.subject, campaign.body, from_email=sender.email,
                             to=[recipient.email], connection=connection).send()
            except SENDER_ERRORS as e:
                _log(campaign, f"El remitente dejó de enviar: {e}", EmailLog.ERROR, sender=sender.email)
                break
            except Exception as e:
                recipient.attempts += 1
                recipient.error = str(e)
                if recipient.attempts >= MAX_ATTEMPTS:
                    recipient.status = EmailRecipient.FAILED
                recipient.sender_email = sender.email
                failed += 1
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
    return sent, failed


def run_campaign(campaign: EmailCampaign, now: datetime | None = None, force: bool = False) -> dict | None:
    """Una ejecución: envía a los pendientes hasta el límite diario, repartidos en ronda entre
    los remitentes → {sent, failed, pending}. None si otra ejecución ya tomó el día (dos
    crons solapados) y no es `force`."""
    current = local_now(now)
    today = current.date()
    claimed = EmailCampaign.objects.filter(id=campaign.id)
    if not force:
        claimed = claimed.exclude(last_run_on=today)
    if not claimed.update(last_run_on=today):
        return None
    campaign.last_run_on = today

    senders = [s for s in campaign.senders.all() if s.password_encrypted]
    pending = campaign.recipients.filter(status=EmailRecipient.PENDING)
    quota = max(campaign.daily_limit - _sent_today(campaign, current), 0)
    batch = list(pending.order_by("id")[:quota])
    if not senders:
        _log(campaign, "No hay remitentes con contraseña: no se envió nada.", EmailLog.ERROR)
        return {"sent": 0, "failed": 0, "pending": pending.count()}
    if not batch:
        _log(campaign, "Ya se alcanzó el límite de correos de hoy." if pending.exists()
             else "No hay destinatarios pendientes.")
        return {"sent": 0, "failed": 0, "pending": pending.count()}

    _log(campaign, f"Inicio del envío: {len(batch)} correos con {len(senders)} "
                   f"{'remitente' if len(senders) == 1 else 'remitentes'}.")
    sent = failed = 0
    for index, sender in enumerate(senders):
        group = batch[index::len(senders)]
        if group:
            group_sent, group_failed = _send_group(campaign, sender, group, now)
            sent += group_sent
            failed += group_failed
    remaining = pending.count()
    _log(campaign, f"Fin del envío: {sent} enviados, {failed} con error, {remaining} pendientes."
         + (" Envío completado." if not remaining else ""))
    return {"sent": sent, "failed": failed, "pending": remaining}


def send_test(campaign: EmailCampaign, to: str) -> str:
    """El mensaje de la campaña a una sola dirección, con el primer remitente → su correo."""
    sender = next((s for s in campaign.senders.all() if s.password_encrypted), None)
    if sender is None:
        raise MailingError("Guarda al menos un remitente con su contraseña de aplicación.")
    try:
        connection = _connection(sender)
        EmailMessage(campaign.subject, campaign.body, from_email=sender.email, to=[to],
                     connection=connection).send()
    except MailingError:
        raise
    except Exception as e:
        raise MailingError(f"No se pudo enviar con {sender.email}: {e}")
    return sender.email


# ----------------------------------------------------------------- reporte
def report_summary(campaign: EmailCampaign) -> dict:
    """Lo que la pestaña «Correos» consulta cada pocos segundos: totales por estado y el id del
    último log (si cambian, hay novedades)."""
    counts = dict(campaign.recipients.values_list("status").annotate(n=Count("id")))
    last_log = campaign.logs.order_by("-id").values_list("id", flat=True).first()
    upcoming = next_run(campaign)
    return {
        "total": sum(counts.values()),
        "sent": counts.get(EmailRecipient.SENT, 0),
        "pending": counts.get(EmailRecipient.PENDING, 0),
        "failed": counts.get(EmailRecipient.FAILED, 0),
        "last_log_id": last_log or 0,
        "next_run": upcoming.isoformat() if upcoming else None,
    }
