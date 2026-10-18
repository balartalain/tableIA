"""Vistas del envío de correos de un tablero: adaptadores HTTP sobre services/mailing.py.
La configuración es del dueño del tablero (editor); el reporte es de solo lectura y lo usa la
vista compartida, como `dashboard_render`."""
import re
from datetime import time

import nh3
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from sheets_reports.models import Dashboard, EmailCampaign, EmailRecipient, EmailSender
from sheets_reports.services import mailing
from sheets_reports.views import _error, _json_body, _owned_dashboard

MAX_CSV_BYTES = 5 * 1024 * 1024
MAX_INVALID_SHOWN = 20
REPORT_PAGE_SIZE = 50
REPORT_MAX_PAGE_SIZE = 200

# El cuerpo es el HTML que escribe el editor (Quill): solo se permiten estas etiquetas y, en
# las imágenes, el `src` en base64 (`data:`) o una URL normal. `link_rel` añade el `rel`.
BODY_TAGS = {"p", "br", "strong", "em", "u", "s", "ol", "ul", "li", "a",
             "h1", "h2", "h3", "blockquote", "img", "span"}
BODY_ATTRIBUTES = {"a": {"href", "target"}, "img": {"src", "alt", "width", "height"}}
BODY_URL_SCHEMES = {"http", "https", "mailto", "data"}
_ANCHOR_RE = re.compile(r"<a\s(?![^>]*\btarget=)", re.IGNORECASE)


def _clean_html(value) -> str:
    """El HTML del mensaje, saneado (sin scripts ni atributos peligrosos) y con enlaces que
    abren en otra pestaña. Vacío si no queda contenido visible, para no activar un envío en blanco."""
    html = nh3.clean(str(value or ""), tags=BODY_TAGS, attributes=BODY_ATTRIBUTES,
                     url_schemes=BODY_URL_SCHEMES, link_rel="noopener noreferrer")
    html = _ANCHOR_RE.sub('<a target="_blank" ', html).strip()
    return html if mailing.html_to_text(html) or re.search(r"(?i)<img\b", html) else ""


def _campaign(dashboard) -> EmailCampaign:
    """El envío del tablero; uno sin guardar (con los valores por defecto) si aún no existe."""
    return getattr(dashboard, "email_campaign", None) or EmailCampaign(dashboard=dashboard)


def _serialize_campaign(campaign: EmailCampaign) -> dict:
    saved = campaign.pk is not None
    send_time = campaign.send_time
    if isinstance(send_time, str):
        send_time = time.fromisoformat(send_time)
    return {
        "enabled": campaign.enabled,
        "subject": campaign.subject,
        "body": campaign.body,
        "daily_limit": campaign.daily_limit,
        "batch_size": campaign.batch_size,
        "frequency": campaign.frequency,
        "send_time": send_time.strftime("%H:%M"),
        "weekdays": campaign.weekdays or [],
        # La contraseña nunca sale del servidor: solo si ya hay una guardada.
        "senders": [{"email": s.email, "has_password": bool(s.password_encrypted)}
                    for s in campaign.senders.all()] if saved else [],
        "report": mailing.report_summary(campaign) if saved else
                  {"total": 0, "sent": 0, "pending": 0, "failed": 0, "last_log_id": 0, "next_run": None},
        "time_zone": mailing.local_now().tzinfo.key,
        # Cada cuánto despierta el cron (min): la UI lo usa para avisar de las horas que casi
        # no alcanzan a correr (última franja antes de medianoche).
        "cron_minutes": settings.CAMPAIGN_CRON_MINUTES,
    }


def _is_email(value) -> bool:
    try:
        validate_email(value)
    except ValidationError:
        return False
    return True


def _clean_campaign(data: dict, campaign: EmailCampaign) -> tuple[dict, list[dict], str | None]:
    """Lo que llega del formulario → (campos, remitentes [{email, password}], error)."""
    frequency = data.get("frequency")
    if frequency not in dict(EmailCampaign.FREQUENCY_CHOICES):
        return {}, [], "Elige la frecuencia: diario o días de la semana."
    try:
        send_time = time.fromisoformat(str(data.get("send_time") or ""))
    except ValueError:
        return {}, [], "La hora de envío no es válida."
    weekdays = data.get("weekdays") or []
    if (not isinstance(weekdays, list)
            or any(isinstance(d, bool) or not isinstance(d, int) or not 0 <= d <= 6 for d in weekdays)):
        return {}, [], "Los días de la semana no son válidos."
    weekdays = sorted(set(weekdays))
    if frequency == EmailCampaign.WEEKLY and not weekdays:
        return {}, [], "Elige al menos un día de la semana."
    limit = data.get("daily_limit")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        return {}, [], "El máximo de correos al día debe ser un número mayor que cero."
    batch_size = data.get("batch_size")
    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size < 1:
        return {}, [], "El tamaño del lote por remitente debe ser un número mayor que cero."

    stored = {s.email: s for s in campaign.senders.all()} if campaign.pk else {}
    senders, seen = [], set()
    for item in data.get("senders") or []:
        email = str((item or {}).get("email") or "").strip().lower()
        password = str((item or {}).get("password") or "").replace(" ", "")
        if not _is_email(email):
            return {}, [], f"El correo del remitente «{email}» no es válido."
        if email in seen:
            return {}, [], f"El remitente {email} está repetido."
        if not password and not (email in stored and stored[email].password_encrypted):
            return {}, [], f"Escribe la contraseña de aplicación de {email}."
        seen.add(email)
        senders.append({"email": email, "password": password})

    fields = {
        "enabled": bool(data.get("enabled")),
        "subject": str(data.get("subject") or "").strip()[:255],
        "body": _clean_html(data.get("body")),
        "daily_limit": limit,
        "batch_size": batch_size,
        "frequency": frequency,
        "send_time": send_time,
        "weekdays": weekdays,
    }
    if fields["enabled"]:
        if not fields["subject"] or not fields["body"]:
            return {}, [], "Para activar el envío escribe el asunto y el cuerpo del mensaje."
        if not senders:
            return {}, [], "Para activar el envío agrega al menos un remitente."
        if not campaign.pk or not campaign.recipients.exists():
            return {}, [], "Para activar el envío carga primero los destinatarios."
    return fields, senders, None


@csrf_exempt
@require_http_methods(["GET", "PUT"])
def email_campaign(request, dashboard_id):
    """GET: la configuración del envío del tablero (sin contraseñas). PUT {enabled, subject,
    body, daily_limit, batch_size, frequency, send_time, weekdays, senders: [{email,
    password?}]}: la guarda; un remitente sin `password` conserva la que tenía y uno que no
    viene se quita."""
    dashboard = _owned_dashboard(request, dashboard_id)
    if not dashboard:
        return _error("Tablero no encontrado", status=404)
    campaign = _campaign(dashboard)
    if request.method == "GET":
        return JsonResponse(_serialize_campaign(campaign))
    try:
        data = _json_body(request)
    except ValueError as e:
        return _error(str(e))
    fields, senders, error = _clean_campaign(data, campaign)
    if error:
        return _error(error)
    for key, value in fields.items():
        setattr(campaign, key, value)
    # Guardar reabre el día (p. ej. tras corregir una contraseña); el máximo diario se
    # sigue respetando porque se cuenta sobre lo enviado hoy.
    campaign.last_run_on = None
    campaign.save()
    campaign.senders.exclude(email__in=[s["email"] for s in senders]).delete()
    for item in senders:
        sender, _ = EmailSender.objects.get_or_create(campaign=campaign, email=item["email"])
        if item["password"]:
            sender.password_encrypted = mailing.encrypt_password(item["password"])
            sender.save(update_fields=["password_encrypted"])
    return JsonResponse(_serialize_campaign(campaign))


@csrf_exempt
@require_http_methods(["POST", "DELETE"])
def email_recipients(request, dashboard_id):
    """POST (multipart, `file`: CSV): agrega sus correos a la lista → {added, duplicates,
    invalid}; los que ya estaban conservan su estado. DELETE: vacía la lista."""
    dashboard = _owned_dashboard(request, dashboard_id)
    if not dashboard:
        return _error("Tablero no encontrado", status=404)
    campaign = _campaign(dashboard)
    if request.method == "DELETE":
        if campaign.pk:
            campaign.recipients.all().delete()
            # Sin destinatarios no hay nada que enviar.
            EmailCampaign.objects.filter(id=campaign.id).update(enabled=False)
            campaign.enabled = False
        return JsonResponse(_serialize_campaign(campaign))
    upload = request.FILES.get("file")
    if upload is None:
        return _error("Elige un archivo CSV.")
    if upload.size > MAX_CSV_BYTES:
        return _error("El archivo pesa más de 5 MB.")
    valid, invalid = mailing.parse_recipients_csv(upload.read())
    if not valid:
        return _error("El archivo no tiene ningún correo electrónico válido.")
    if campaign.pk is None:
        campaign.save()
    before = campaign.recipients.count()
    EmailRecipient.objects.bulk_create(
        [EmailRecipient(campaign=campaign, email=email) for email in valid], ignore_conflicts=True)
    added = campaign.recipients.count() - before
    return JsonResponse({
        **_serialize_campaign(campaign),
        "added": added,
        "duplicates": len(valid) - added,
        "invalid": invalid[:MAX_INVALID_SHOWN],
        "invalid_count": len(invalid),
    })


@csrf_exempt
@require_http_methods(["POST"])
def email_test(request, dashboard_id):
    """POST {to}: envía el mensaje guardado a una dirección, con el primer remitente."""
    dashboard = _owned_dashboard(request, dashboard_id)
    if not dashboard:
        return _error("Tablero no encontrado", status=404)
    try:
        data = _json_body(request)
    except ValueError as e:
        return _error(str(e))
    to = str(data.get("to") or "").strip()
    if not _is_email(to):
        return _error("Escribe un correo válido para la prueba.")
    campaign = _campaign(dashboard)
    if campaign.pk is None or not campaign.subject or not campaign.body:
        return _error("Guarda primero el asunto y el cuerpo del mensaje.")
    try:
        sender = mailing.send_test(campaign, to)
    except mailing.MailingError as e:
        return _error(str(e), status=502)
    return JsonResponse({"sent": True, "sender": sender})


# ----------------------------------------------------------------- reporte
def _enabled_campaign(dashboard_id):
    """El envío activado del tablero, o None: sin activar no hay reporte que mostrar."""
    dashboard = get_object_or_404(Dashboard, id=dashboard_id)
    campaign = getattr(dashboard, "email_campaign", None)
    return campaign if campaign and campaign.enabled else None


def _page(request, queryset, serialize) -> JsonResponse:
    """Una página de `queryset`: GET ?page=1&size=50 → {rows, total_rows, last_page}."""
    def positive(name: str, default: int) -> int:
        try:
            return max(int(request.GET.get(name) or default), 1)
        except ValueError:
            return default

    page = positive("page", 1)
    size = min(positive("size", REPORT_PAGE_SIZE), REPORT_MAX_PAGE_SIZE)
    total = queryset.count()
    start = (page - 1) * size
    return JsonResponse({
        "rows": [serialize(item) for item in queryset[start:start + size]],
        "total_rows": total,
        "last_page": max(-(-total // size), 1),
    })


@require_http_methods(["GET"])
def email_report(request, dashboard_id):
    """Totales del envío y el id del último log: lo que la pestaña «Seguimiento de correos» consulta cada
    pocos segundos para saber si el cron envió algo."""
    campaign = _enabled_campaign(dashboard_id)
    if not campaign:
        return _error("Este tablero no tiene el envío de correos activado.", status=404)
    return JsonResponse(mailing.report_summary(campaign))


@require_http_methods(["GET"])
def email_report_stats(request, dashboard_id):
    """Enviados por día, detalle por remitente y errores por motivo: la pestaña «Seguimiento de correos» lo
    pide al abrirse y cada vez que cambian los totales."""
    campaign = _enabled_campaign(dashboard_id)
    if not campaign:
        return _error("Este tablero no tiene el envío de correos activado.", status=404)
    return JsonResponse(mailing.report_stats(campaign))


@require_http_methods(["GET"])
def email_report_recipients(request, dashboard_id):
    campaign = _enabled_campaign(dashboard_id)
    if not campaign:
        return _error("Este tablero no tiene el envío de correos activado.", status=404)
    recipients = campaign.recipients.order_by("id")
    if request.GET.get("status") in dict(EmailRecipient.STATUS_CHOICES):
        recipients = recipients.filter(status=request.GET["status"])
    return _page(request, recipients, lambda r: {
        "email": r.email,
        "status": r.status,
        "sent_at": r.sent_at.isoformat() if r.sent_at else None,
        "sender_email": r.sender_email,
        "error": r.error,
    })


@require_http_methods(["GET"])
def email_report_logs(request, dashboard_id):
    campaign = _enabled_campaign(dashboard_id)
    if not campaign:
        return _error("Este tablero no tiene el envío de correos activado.", status=404)
    return _page(request, campaign.logs.order_by("-id"), lambda log: {
        "created_at": log.created_at.isoformat(),
        "level": log.level,
        "message": log.message,
        "recipient_email": log.recipient_email,
        "sender_email": log.sender_email,
    })
