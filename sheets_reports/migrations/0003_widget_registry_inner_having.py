import sheets_reports.models
from django.db import migrations, models


def rename_inner_having(apps, schema_editor):
    """`having` de las métricas «por grupo» pasa a llamarse `inner_having` (tiene otro alcance
    que el `having` del widget). Sin compatibilidad con el nombre anterior: se reescribe."""
    Widget = apps.get_model("sheets_reports", "Widget")
    for widget in Widget.objects.all():
        changed = False
        for metric in (widget.data_spec or {}).get("metrics") or []:
            if metric.get("type") == "grouped" and "having" in metric:
                metric["inner_having"] = metric.pop("having")
                changed = True
        if changed:
            widget.save(update_fields=["data_spec"])


class Migration(migrations.Migration):
    dependencies = [("sheets_reports", "0002_reset_widgets_dsl")]

    operations = [
        migrations.AlterField(
            model_name="widget",
            name="type",
            field=models.CharField(choices=sheets_reports.models.widget_type_choices, max_length=10),
        ),
        migrations.RunPython(rename_inner_having, migrations.RunPython.noop),
    ]
