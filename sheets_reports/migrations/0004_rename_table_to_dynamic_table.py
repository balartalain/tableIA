import sheets_reports.models
from django.db import migrations, models


def table_to_dynamic_table(apps, schema_editor):
    """La tabla dinámica (agrupa por filas) pasa de "table" a "dynamic_table"."""
    Widget = apps.get_model("sheets_reports", "Widget")
    for widget in Widget.objects.filter(type="table"):
        widget.type = "dynamic_table"
        widget.view_spec = {**(widget.view_spec or {}), "widget": "dynamic_table"}
        widget.save(update_fields=["type", "view_spec"])


class Migration(migrations.Migration):
    dependencies = [("sheets_reports", "0003_widget_registry_inner_having")]

    operations = [
        migrations.AlterField(
            model_name="widget",
            name="type",
            field=models.CharField(choices=sheets_reports.models.widget_type_choices, max_length=20),
        ),
        migrations.RunPython(table_to_dynamic_table, migrations.RunPython.noop),
    ]
