from django.apps import AppConfig
from django.core.exceptions import ImproperlyConfigured


class SheetsReportsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'sheets_reports'

    def ready(self):
        from sheets_reports.models import Widget
        from sheets_reports.widgets import WIDGETS

        max_length = Widget._meta.get_field("type").max_length
        too_long = [key for key in WIDGETS.keys() if len(key) > max_length]
        if too_long:
            raise ImproperlyConfigured(
                f"La clave de un widget no puede pasar de {max_length} caracteres: {', '.join(too_long)}."
            )
