from django.db import migrations


def remove_color_scheme(apps, schema_editor):
    """`style.color_scheme` ya no existe (los gráficos usan el color del tablero) y la validación
    rechaza las claves de estilo fuera del schema."""
    Widget = apps.get_model("sheets_reports", "Widget")
    for widget in Widget.objects.filter(type__in=["bar", "line"]):
        if isinstance(widget.style, dict) and "color_scheme" in widget.style:
            widget.style.pop("color_scheme")
            widget.save(update_fields=["style"])


class Migration(migrations.Migration):

    dependencies = [
        ("sheets_reports", "0031_remove_emailcampaign_batch_size_and_more"),
    ]

    operations = [
        migrations.RunPython(remove_color_scheme, migrations.RunPython.noop),
    ]
