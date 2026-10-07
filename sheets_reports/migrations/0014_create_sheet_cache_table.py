from django.core.management import call_command
from django.db import migrations


def create_cache_table(apps, schema_editor):
    # 0008 corrió antes de que existiera el alias `sheets` (sheet_cache_table) en
    # settings.CACHES; createcachetable es idempotente y solo crea las que faltan.
    call_command('createcachetable', database=schema_editor.connection.alias)


class Migration(migrations.Migration):

    dependencies = [
        ('sheets_reports', '0013_remove_formula_metrics'),
    ]

    operations = [
        migrations.RunPython(create_cache_table, migrations.RunPython.noop),
    ]
