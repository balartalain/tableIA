from django.db import migrations


def keep_own_keys(apps, schema_editor):
    """Histórico: normalizaba el `data_spec` guardado contra el `spec_cls` de cada widget.

    El campo `data_spec` se eliminó en 0007 y el registro de widgets ya no tiene `spec_cls`,
    así que la operación queda vacía. Los widgets existentes se resetean en 0002 al migrar desde
    cero; en una base ya migrada, 0007 elimina las claves viejas.
    """
    return None


class Migration(migrations.Migration):
    dependencies = [("sheets_reports", "0004_rename_table_to_dynamic_table")]

    operations = [migrations.RunPython(keep_own_keys, migrations.RunPython.noop)]
