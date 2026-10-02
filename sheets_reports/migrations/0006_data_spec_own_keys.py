from django.db import migrations


def keep_own_keys(apps, schema_editor):
    """Cada data_spec guarda solo las claves de las piezas de su widget (spec_cls): las de
    otras piezas, que antes iban siempre vacías, se quitan."""
    from sheets_reports.widgets import WIDGETS

    Widget = apps.get_model("sheets_reports", "Widget")
    for widget in Widget.objects.all():
        if widget.type not in WIDGETS:
            continue
        spec_cls = WIDGETS.get(widget.type).spec_cls
        data_spec = spec_cls.from_dict(spec_cls.with_defaults(widget.data_spec or {})).to_dict()
        if data_spec != widget.data_spec:
            widget.data_spec = data_spec
            widget.save(update_fields=["data_spec"])


class Migration(migrations.Migration):
    dependencies = [("sheets_reports", "0005_data_spec_columns")]

    operations = [migrations.RunPython(keep_own_keys, migrations.RunPython.noop)]
