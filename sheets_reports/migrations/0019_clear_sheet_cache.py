from django.core.cache import caches
from django.db import migrations


def clear_sheet_cache(apps, schema_editor):
    # El caché de hojas cambió de formato (ya no guarda la hoja original, solo el frame que usa
    # cada fuente): las entradas viejas no se leen más. Cada hoja se vuelve a leer de Google en
    # su primera petición.
    caches["sheets"].clear()


class Migration(migrations.Migration):

    dependencies = [
        ('sheets_reports', '0018_alter_widget_position'),
    ]

    operations = [
        migrations.RunPython(clear_sheet_cache, migrations.RunPython.noop),
    ]
