from django.core.management import call_command
from django.db import migrations


def create_cache_table(apps, schema_editor):
    # DatabaseCache (settings.CACHES) no la crea migrate; createcachetable es idempotente.
    call_command('createcachetable', database=schema_editor.connection.alias)


class Migration(migrations.Migration):

    dependencies = [
        ('sheets_reports', '0007_remove_widget_data_spec_remove_widget_view_spec_and_more'),
    ]

    operations = [
        migrations.RunPython(create_cache_table, migrations.RunPython.noop),
    ]
