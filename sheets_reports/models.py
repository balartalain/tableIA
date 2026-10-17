from django.conf import settings
from django.db import models


def default_position():
    return {"order": 0, "w": 6, "h": 300, "row_id": "row_1"}


class Dashboard(models.Model):
    """Tablero de reportes. Sus datos vienen de una o varias fuentes (DataSource); cada widget
    usa la suya."""
    nombre = models.CharField(max_length=255, help_text="Nombre descriptivo del tablero.")
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="dashboards",
        help_text="Usuario propietario del tablero.",
    )
    last_opened_at = models.DateTimeField(null=True, blank=True,
                                          help_text="Última vez que se abrió en el editor.")
    is_template = models.BooleanField(
        default=False,
        help_text="Indica si este tablero actúa como plantilla de solo lectura.",
    )
    thumbnail = models.TextField(
        blank=True,
        default="",
        help_text="Miniatura del tablero (data-URL de un SVG) para la galería de plantillas.",
    )
    primary_color = models.CharField(
        max_length=20,
        default="#1F8A5F",
        blank=True,
        help_text="Color primario del tema del tablero.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Dashboard"
        verbose_name_plural = "Dashboards"
        ordering = ["-created_at"]

    def __str__(self):
        return self.nombre


class DataSource(models.Model):
    """Una pestaña (gid) de una hoja de Google Sheets conectada a un tablero, con las columnas
    y los tipos que eligió el usuario."""
    GOOGLE_SHEET = "google_sheet"
    KIND_CHOICES = [(GOOGLE_SHEET, "Hoja de Google")]

    dashboard = models.ForeignKey(Dashboard, on_delete=models.CASCADE, related_name="sources")
    kind = models.CharField(max_length=30, choices=KIND_CHOICES, default=GOOGLE_SHEET)
    sheet_id = models.CharField(max_length=200, help_text="Id del documento de Google Sheets.")
    gid = models.CharField(max_length=50, default="0", help_text="gid de la pestaña.")
    sheet_name = models.CharField(max_length=255, blank=True, default="",
                                  help_text="Nombre del documento.")
    tab_name = models.CharField(max_length=255, blank=True, default="",
                                help_text="Nombre de la pestaña.")
    name = models.CharField(max_length=255, blank=True, default="",
                            help_text="Nombre opcional de la fuente; vacío = «Documento · Pestaña».")
    first_row_headers = models.BooleanField(
        default=True, help_text="La primera fila de la pestaña son los encabezados de las columnas.")
    is_form_response = models.BooleanField(
        default=False,
        help_text="La pestaña es una hoja de respuestas de Google Forms: las preguntas "
                  "de casillas se expanden en columnas binarias (pregunta_valor). "
                  "Ver services/form_transform.py.")
    columns = models.JSONField(
        default=list, blank=True,
        help_text='Columnas elegidas al conectar la hoja: [{"name", "label"?, "type": '
                  '"text"|"number"|"date", "include", "scale"?, "order"?}]. `name` es el encabezado '
                  '(o columna del modelo tidy, en respuestas de formulario), '
                  '`label` el nombre a mostrar que lo reemplaza en todo el '
                  'tablero. `scale` (continuous|discrete|ordinal|nominal|none) es la escala '
                  'de medida confirmada y `order` el orden de los valores de una ordinal; '
                  'sin ella se detecta (utils/scales.py). '
                  'Vacío = todas, con el tipo que trae la hoja.',
    )
    calculated_fields = models.JSONField(
        default=list, blank=True,
        help_text='Campos calculados: [{"id", "name", "formula", "format": "number"|"percent"}]. '
                  'Sin agregaciones son por fila (una columna más); con SUM, AVG… son agregados '
                  '(solo métricas). Ver engine/formulas.py.',
    )
    gemini_file_id = models.CharField(
        max_length=255, blank=True, default="",
        help_text="Id del archivo subido a Gemini Files API para el chatbot de datos.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Fuente de datos"
        verbose_name_plural = "Fuentes de datos"
        ordering = ["created_at", "id"]

    def __str__(self):
        return self.label

    @property
    def label(self) -> str:
        """El nombre que le dio el usuario o, sin él, el original."""
        return self.name or self.original_label

    @property
    def original_label(self) -> str:
        """«Documento · Pestaña» (o el id de la hoja si se creó sin nombres)."""
        name = self.sheet_name or f"{self.sheet_id[:12]}…"
        tab = self.tab_name or f"gid {self.gid}"
        return f"{name} · {tab}"


def widget_type_choices():
    from sheets_reports.widgets import WIDGETS as WIDGET_REGISTRY
    return [(key, widget_cls.label) for key, widget_cls in WIDGET_REGISTRY.items()]


class Widget(models.Model):
    """
    Widget simplificado basado en contratos planos (fields, style).
    """
    dashboard = models.ForeignKey(Dashboard, on_delete=models.CASCADE, related_name="widgets")
    # Al borrar la fuente el widget se queda sin ella y muestra el error al calcularse.
    source = models.ForeignKey(DataSource, null=True, blank=True, on_delete=models.SET_NULL,
                               related_name="widgets")
    type = models.CharField(max_length=50, choices=widget_type_choices)
    title = models.CharField(max_length=255, default="Nuevo Widget")
    position = models.JSONField(
        default=default_position,
        help_text="Posición en el lienzo: order = orden secuencial, w = columnas (1-12), h = alto en px, row_id = fila.",
    )
    
    # Contratos planos del nuevo approach
    fields = models.JSONField(default=dict, help_text="Campos de datos (dimensions, metrics, filters, etc.)")
    style = models.JSONField(default=dict, help_text="Opciones estéticas según el style_schema")
    
    source_prompt = models.TextField(null=True, blank=True, help_text="Prompt original del usuario, si se generó con IA.")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Widget"
        verbose_name_plural = "Widgets"
        ordering = ["created_at"]

    def __str__(self):
        return self.title or f"{self.get_type_display()} ({self.id})"

    @property
    def definition(self):
        """El widget (instancia registrada) de este tipo, o None si el tipo ya no existe."""
        from sheets_reports.widgets import WIDGETS as WIDGET_REGISTRY
        if self.type not in WIDGET_REGISTRY:
            return None
        return WIDGET_REGISTRY.get(self.type)

class EmailCampaign(models.Model):
    """El envío de correos de un tablero: el mensaje, cuántos se envían por día y cuándo.
    Cada destinatario lo recibe una sola vez (ver services/mailing.py)."""
    DAILY = "daily"
    WEEKLY = "weekly"
    FREQUENCY_CHOICES = [(DAILY, "Diario"), (WEEKLY, "Días de la semana")]

    dashboard = models.OneToOneField(Dashboard, on_delete=models.CASCADE, related_name="email_campaign")
    enabled = models.BooleanField(default=False)
    subject = models.CharField(max_length=255, blank=True, default="")
    body = models.TextField(blank=True, default="")
    daily_limit = models.PositiveIntegerField(
        default=100, help_text="Máximo de correos al día, entre todos los remitentes.")
    batch_size = models.PositiveIntegerField(
        default=50, help_text="Correos que envía cada remitente en cada lote; entre lotes pasa 1 minuto.")
    frequency = models.CharField(max_length=10, choices=FREQUENCY_CHOICES, default=DAILY)
    send_time = models.TimeField(default="08:00", help_text="Hora local (CAMPAIGN_TIME_ZONE).")
    weekdays = models.JSONField(
        default=list, blank=True,
        help_text="Días de envío con frecuencia semanal: 0 = lunes … 6 = domingo.")
    last_run_on = models.DateField(
        null=True, blank=True,
        help_text="Día local cerrado: ya no se envía más ese día (se alcanzó el máximo o un lote no envió nada).")
    running_since = models.DateTimeField(
        null=True, blank=True, help_text="Hay un lote en curso desde entonces (candado entre crons).")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Envío de correos"
        verbose_name_plural = "Envio de correoss"

    def __str__(self):
        return f"Correos de {self.dashboard}"


class EmailSender(models.Model):
    """Cuenta de Gmail que envía: su correo y su contraseña de aplicación, cifrada."""
    campaign = models.ForeignKey(EmailCampaign, on_delete=models.CASCADE, related_name="senders")
    email = models.EmailField()
    password_encrypted = models.TextField(blank=True, default="")

    class Meta:
        verbose_name = "Remitente"
        verbose_name_plural = "Remitentes"
        ordering = ["id"]
        constraints = [models.UniqueConstraint(fields=["campaign", "email"], name="unique_campaign_sender")]

    def __str__(self):
        return self.email


class EmailRecipient(models.Model):
    """Un destinatario del envío y si ya se le notificó."""
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    STATUS_CHOICES = [(PENDING, "Pendiente"), (SENT, "Notificado"), (FAILED, "Falló")]

    campaign = models.ForeignKey(EmailCampaign, on_delete=models.CASCADE, related_name="recipients")
    email = models.EmailField()
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=PENDING)
    attempts = models.PositiveSmallIntegerField(default=0)
    sent_at = models.DateTimeField(null=True, blank=True)
    sender_email = models.EmailField(blank=True, default="")
    error = models.TextField(blank=True, default="")

    class Meta:
        verbose_name = "Destinatario"
        verbose_name_plural = "Destinatarios"
        ordering = ["id"]
        constraints = [models.UniqueConstraint(fields=["campaign", "email"], name="unique_campaign_recipient")]
        indexes = [models.Index(fields=["campaign", "status"])]

    def __str__(self):
        return self.email


class EmailLog(models.Model):
    """Bitácora del envío: cada ejecución, sus errores y su resumen."""
    INFO = "info"
    ERROR = "error"
    LEVEL_CHOICES = [(INFO, "Información"), (ERROR, "Error")]

    campaign = models.ForeignKey(EmailCampaign, on_delete=models.CASCADE, related_name="logs")
    created_at = models.DateTimeField(auto_now_add=True)
    level = models.CharField(max_length=10, choices=LEVEL_CHOICES, default=INFO)
    message = models.TextField()
    recipient_email = models.EmailField(blank=True, default="")
    sender_email = models.EmailField(blank=True, default="")

    class Meta:
        verbose_name = "Log de correos"
        verbose_name_plural = "Logs de correos"
        ordering = ["-id"]

    def __str__(self):
        return self.message[:80]
