from django.db import migrations


def delete_widgets(apps, schema_editor):
    # El DSL de data_spec cambió (pivots como lista, métricas con "type", condiciones nuevas)
    # sin compatibilidad con el formato anterior: los widgets guardados se recrean.
    apps.get_model("sheets_reports", "Widget").objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [("sheets_reports", "0001_initial")]

    operations = [migrations.RunPython(delete_widgets, migrations.RunPython.noop)]
