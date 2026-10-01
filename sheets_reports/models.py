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
    """Los tipos de widget registrados (sheets_reports/widgets)."""
    from sheets_reports.widgets import WIDGETS

    return [(w.key, w.label) for w in WIDGETS]


class Widget(models.Model):
    """
    Widget de un tablero, definido por dos specs JSON independientes:
    - data_spec: qué calcular (dsl.spec.DataSpec), agnóstico del tipo de widget.
    - view_spec: cómo presentarlo; lo construye su WidgetType a partir de data_spec y sus
      opciones de vista (WidgetType.build_view).
    Nunca contiene código: solo valores de datos validados contra un JSON Schema cerrado.
    """
    dashboard = models.ForeignKey(Dashboard, on_delete=models.CASCADE, related_name="widgets")
    type = models.CharField(max_length=10, choices=widget_type_choices)
    position = models.JSONField(
        default=default_position,
        help_text="Posición en el lienzo: x = columna inicial (0 = fluido), y = orden, w = columnas (1-12), h = alto en px.",
    )
    data_spec = models.JSONField(help_text="Capa de datos: consulta (dimensiones, pivote, métricas, filtros, orden).")
    view_spec = models.JSONField(help_text="Capa de presentación, derivada de data_spec + type.")
    source_prompt = models.TextField(null=True, blank=True, help_text="Prompt original del usuario, si se generó con IA.")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Widget"
        verbose_name_plural = "Widgets"
        ordering = ["created_at"]

    def __str__(self):
        return (self.view_spec or {}).get("title") or f"{self.get_type_display()} ({self.id})"

    @property
    def definition(self):
        """El WidgetType de este widget."""
        from sheets_reports.widgets import WIDGETS

        return WIDGETS.get(self.type)
