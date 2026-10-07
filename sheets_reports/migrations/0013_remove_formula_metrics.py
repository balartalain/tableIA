"""Las métricas «Cálculo entre métricas» (`type: formula`) dejan de existir: sus cálculos se
hacen con campos calculados de la fuente. Se quitan de los widgets guardados, junto con lo que
las nombraba (orden y roles del KPI)."""
from django.db import migrations

# Controles de estilo que guardan el alias de una métrica (roles del KPI).
METRIC_ROLE_KEYS = ("primary", "compare", "targetMetric")


def remove_formula_metrics(apps, schema_editor):
    Widget = apps.get_model("sheets_reports", "Widget")
    for widget in Widget.objects.all():
        fields = widget.fields or {}
        metrics = fields.get("metrics") or []
        removed = {m.get("alias") for m in metrics if isinstance(m, dict) and m.get("type") == "formula"}
        if not removed:
            continue
        fields["metrics"] = [m for m in metrics if not (isinstance(m, dict) and m.get("type") == "formula")]
        if str(fields.get("sort_by") or "").lstrip("-") in removed:
            fields["sort_by"] = None
        style = widget.style or {}
        for key in METRIC_ROLE_KEYS:
            if style.get(key) in removed:
                style[key] = ""
        widget.fields, widget.style = fields, style
        widget.save(update_fields=["fields", "style"])


class Migration(migrations.Migration):

    dependencies = [
        ('sheets_reports', '0012_data_source_calculated_fields'),
    ]

    operations = [
        migrations.RunPython(remove_formula_metrics, migrations.RunPython.noop),
    ]
