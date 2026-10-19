"""
Envío de correos de un tablero (`EmailCampaign`): cada destinatario recibe el mensaje una
sola vez. El crontab llama a `manage.py send_campaign_emails`, que ejecuta las campañas a
las que les toca (`is_due`); la ejecución envía los pendientes de uno en uno, espaciados
`SEND_INTERVAL_S` (todos los remitentes van en serie; son cuentas de Gmail con contraseña de
aplicación), hasta llegar al máximo de cada remitente (`per_sender_limit`), y deja su bitácora
en `EmailLog`. No hace HTTP: las vistas (views_email.py) son adaptadores sobre este servicio.
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
# Segundos entre un correo y el siguiente (los remitentes van en serie, así que el ritmo es
# global). Reparte los envíos a paso uniforme para no activar las defensas anti-spam de Gmail.
SEND_INTERVAL_S = 1.0
# El envío renueva su candado (`running_since`) a lo largo de la corrida: uno más viejo que
# esto es de un proceso que murió.
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


def _sent_today_by_sender(campaign: EmailCampaign, current: datetime) -> dict:
    """Los enviados hoy, por remitente: es el cupo que ya gastó cada cuenta."""
    start = _day_start(current)
    rows = (campaign.recipients.filter(status=EmailRecipient.SENT, sent_at__gte=start,
                                       sent_at__lt=start + timedelta(days=1))
            .exclude(sender_email="").values("sender_email").annotate(n=Count("id")))
    return {row["sender_email"]: row["n"] for row in rows}


def _senders(campaign: EmailCampaign) -> list:
    """Los remitentes que pueden enviar (tienen contraseña de aplicación)."""
    return [s for s in campaign.senders.all() if s.password_encrypted]


def _capacity(campaign: EmailCampaign) -> int:
    """Correos que se pueden enviar hoy: el tope de cada remitente por el número de remitentes."""
    return campaign.per_sender_limit * len(_senders(campaign))


def _has_quota(campaign: EmailCampaign, current: datetime) -> bool:
    """Queda cupo hoy. Sin remitentes no hay cupo, pero se deja pasar la ejecución para que
    quede el aviso en la bitácora."""
    senders = _senders(campaign)
    if not senders:
        return True
    return _sent_today(campaign, current) < campaign.per_sender_limit * len(senders)


def is_due(campaign: EmailCampaign, now: datetime | None = None) -> bool:
    """Le toca ejecutarse: activada, con pendientes, hoy es día de envío, ya pasó la hora,
    el día no está cerrado (`last_run_on`) y queda cupo. La ejecución cierra el día al
    terminar; una que se cortó a medias vuelve a tocarle y se retoma."""
    current = local_now(now)
    return (campaign.enabled
            and campaign.last_run_on != current.date()
            and _is_scheduled_day(campaign, current.date())
            and current.time() >= campaign.send_time
            and _has_pending(campaign)
            and _has_quota(campaign, current))


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
        if offset == 0 and not _has_quota(campaign, current):
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


class _Pacer:
    """Espacia los envíos a un ritmo uniforme (`SEND_INTERVAL_S`) y, de paso, renueva el candado
    de la campaña cada `keepalive_every` correos: así un envío largo no se lo roba otro cron."""

    def __init__(self, keepalive=None, keepalive_every: int = 0, interval: float = SEND_INTERVAL_S):
        self.interval = interval
        self.keepalive = keepalive
        self.keepalive_every = keepalive_every
        self._started = False
        self._sent = 0

    def before_send(self) -> None:
        if self._started and self.interval:
            sleep(self.interval)
        self._started = True
        self._sent += 1
        if self.keepalive and self.keepalive_every and self._sent % self.keepalive_every == 0:
            self.keepalive()


def _send_group(campaign: EmailCampaign, sender, group: list, now: datetime | None = None,
                pacer: "_Pacer | None" = None) -> tuple[int, list, bool]:
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
            if pacer:
                pacer.before_send()
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
    """El envío del día: reparte los pendientes en ronda entre los remitentes, hasta
    `per_sender_limit` por remitente, con los correos espaciados `SEND_INTERVAL_S` →
    {sent, failed, pending}. None si hay otro envío en curso (dos crons solapados) o si el día
    ya está cerrado y no es `force`."""
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
    senders = _senders(campaign)
    pending = campaign.recipients.filter(status=EmailRecipient.PENDING)
    if not senders:
        _log(campaign, "No hay remitentes con contraseña: no se envió nada.", EmailLog.ERROR)
        _close_day(campaign, today)
        return {"sent": 0, "failed": 0, "pending": pending.count()}

    # Cupo de hoy por remitente: su tope menos lo que ya envió hoy.
    ya_hoy = _sent_today_by_sender(campaign, current)
    quota = {s.email: max(campaign.per_sender_limit - ya_hoy.get(s.email, 0), 0) for s in senders}
    quota = {email: n for email, n in quota.items() if n > 0}

    sent = failed = 0
    started = False
    # Los que fallaron en esta ejecución: se reintentan el próximo día de envío, no en la misma.
    tried = set()
    # El ritmo lo lleva un solo pacer para todo el envío; de paso renueva el candado cada minuto.
    keepalive_every = max(1, round(60 / SEND_INTERVAL_S)) if SEND_INTERVAL_S else 0
    pacer = _Pacer(
        keepalive=lambda: EmailCampaign.objects.filter(id=campaign.id).update(running_since=timezone.now()),
        keepalive_every=keepalive_every)
    while quota:
        if local_now(now).date() != today:
            break
        queue = list(pending.exclude(id__in=tried).order_by("id")[:sum(quota.values())])
        if not queue:
            break
        if not started:
            _log(campaign, f"Inicio del envío: hasta {campaign.per_sender_limit} por remitente, "
                           f"con {len(senders)} {'remitente' if len(senders) == 1 else 'remitentes'}.")
            started = True
        # Reparte en ronda entre los remitentes con cupo (los agotados se saltan).
        order = list(quota)
        groups = {email: [] for email in order}
        caps = dict(quota)
        index = 0
        for recipient in queue:
            for _ in range(len(order)):
                email = order[index]
                index = (index + 1) % len(order)
                if caps[email]:
                    groups[email].append(recipient)
                    caps[email] -= 1
                    break
        round_sent, round_failed, alive = 0, 0, []
        for sender in senders:
            group = groups.get(sender.email)
            if not group:
                continue
            group_sent, group_failed, sender_alive = _send_group(campaign, sender, group, now, pacer)
            round_sent += group_sent
            round_failed += len(group_failed)
            tried.update(group_failed)
            # Solo lo enviado de verdad gasta cupo: un fallo deja el hueco para otro pendiente.
            quota[sender.email] -= group_sent
            if sender_alive:
                alive.append(sender)
        # Un remitente que no puede enviar no se vuelve a usar en esta ejecución.
        senders = alive
        sent += round_sent
        failed += round_failed
        # Fuera los remitentes que se fueron o ya agotaron su cupo.
        vivos = {s.email for s in senders}
        quota = {email: n for email, n in quota.items() if n > 0 and email in vivos}
        # Renueva el candado: el envío sigue vivo.
        EmailCampaign.objects.filter(id=campaign.id).update(running_since=timezone.now())

    _close_day(campaign, today)
    remaining = pending.count()
    reached = _sent_today(campaign, current) >= _capacity(campaign)
    if not started:
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
    """El día en que se acaban los pendientes al ritmo de la capacidad diaria, o None si no hay
    pendientes o falta más de un año."""
    capacity = _capacity(campaign)
    if not pending or not campaign.enabled or not capacity:
        return None
    for offset in range(366):
        day = current.date() + timedelta(days=offset)
        if not _is_scheduled_day(campaign, day) or campaign.last_run_on == day:
            continue
        pending -= max(capacity - sent_today, 0) if offset == 0 else capacity
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
    capacity = _capacity(campaign)
    last_log = campaign.logs.order_by("-id").values_list("id", flat=True).first()
    upcoming = next_run(campaign, now)
    running_since = campaign.running_since
    if running_since and running_since >= current - LOCK_STALE:
        state = RUNNING
    elif not pending:
        state = DONE
    elif campaign.last_run_on == current.date() and _has_quota(campaign, current):
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
        "per_sender_limit": campaign.per_sender_limit,
        "daily_capacity": capacity,
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
        "per_sender_limit": campaign.per_sender_limit,
        "daily_capacity": _capacity(campaign),
        "by_day": [{"date": item["day"].isoformat(), "sent": item["n"]} for item in list(by_day)[::-1]],
        "by_sender": sorted(senders.values(), key=lambda item: (not item["active"], item["email"])),
        "errors": [{"message": message, "count": reason["count"], "last_at": reason["last_at"].isoformat()}
                   for message, reason in top],
    }
