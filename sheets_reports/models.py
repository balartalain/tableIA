import re
from django.conf import settings
from django.db import models


def default_position():
    return {"x": 0, "y": 0, "w": 6, "h": 300}


class Dashboard(models.Model):
    """Tablero de reportes sobre una pestaña (gid) de una hoja de Google Sheets."""
    nombre = models.CharField(max_length=255, help_text="Nombre descriptivo del tablero.")
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="dashboards",
        help_text="Usuario propietario del tablero.",
    )
    sheet_url = models.URLField(max_length=500, help_text="URL de la hoja de Google Sheets.")
    sheet_gid = models.CharField(
        max_length=50,
        default="0",
        help_text="gid de la pestaña a leer (el número después de '#gid=' en la URL).",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Dashboard"
        verbose_name_plural = "Dashboards"
        ordering = ["-created_at"]

    def __str__(self):
        return self.nombre

    @property
    def sheet_id(self) -> str:
        m = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", self.sheet_url or "")
        return m.group(1) if m else ""


def widget_type_choices():
    from sheets_reports.widgets import WIDGETS as WIDGET_REGISTRY
    return [(key, widget_cls.label) for key, widget_cls in WIDGET_REGISTRY.items()]


class Widget(models.Model):
    """
    Widget simplificado basado en contratos planos (fields, style).
    """
    dashboard = models.ForeignKey(Dashboard, on_delete=models.CASCADE, related_name="widgets")
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