from django.db import migrations


def add_columns(apps, schema_editor):
    """El data_spec gana la clave `columns` (columnas que se muestran tal cual, sin agrupar;
    las usa la tabla de datos). En los widgets existentes va vacía."""
    Widget = apps.get_model("sheets_reports", "Widget")
    for widget in Widget.objects.all():
        if "columns" not in (widget.data_spec or {}):
            widget.data_spec = {**(widget.data_spec or {}), "columns": []}
            widget.save(update_fields=["data_spec"])


class Migration(migrations.Migration):
    dependencies = [("sheets_reports", "0004_rename_table_to_dynamic_table")]

    operations = [migrations.RunPython(add_columns, migrations.RunPython.noop)]
