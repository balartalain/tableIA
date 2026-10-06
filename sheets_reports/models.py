from django.conf import settings
from django.db import models


def default_position():
    return {"x": 0, "y": 0, "w": 6, "h": 300}


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
    columns = models.JSONField(
        default=list, blank=True,
        help_text='Columnas elegidas al conectar la hoja: [{"name", "type": "text"|"number"|"date", '
                  '"include"}]. Vacío = todas, con el tipo que trae la hoja.',
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
        help_text="Posición en el lienzo: x = columna inicial (0 = fluido), y = orden, w = columnas (1-12), h = alto en px.",
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