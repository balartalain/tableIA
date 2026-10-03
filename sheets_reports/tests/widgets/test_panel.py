"""Panel de datos del editor: sale de manifest()["parts"] (SpecPart.panel). El frontend lo
recorre y monta un componente por `ui`, así que un widget nuevo no escribe panel propio."""
import re
from pathlib import Path

from django.test import SimpleTestCase

from sheets_reports.dsl.parts import PanelColumns
from sheets_reports.widgets import WIDGETS

COLUMNS = PanelColumns(all=("categoria", "mes", "anio", "ventas"), numeric=("anio", "ventas"),
                       dimension=("categoria", "mes", "anio"))
PART_TEMPLATE = Path(__file__).resolve().parents[2] / "templates" / "partials" / "panel" / "part.html"


def parts(widget_type, columns=COLUMNS):
    return WIDGETS.get(widget_type).manifest(columns)["parts"]


def part(widget_type, key, columns=COLUMNS):
    return next(p for p in parts(widget_type, columns) if p["key"] == key)


class PanelPartsTests(SimpleTestCase):
    def test_orden_y_componente_de_cada_pieza(self):
        self.assertEqual([(p["key"], p["ui"]) for p in parts("bar")], [
            ("dimensions", "column-list"), ("pivots", "column-list"), ("metrics", "metric-list"),
            ("filters", "condition-list"), ("having", "condition-list-metric"),
            ("sort", "field-group"), ("limit", "field-group"),
        ])
        self.assertEqual([p["key"] for p in parts("table")], ["columns", "filters", "sort"])
        self.assertEqual([p["key"] for p in parts("kpi")], ["metrics", "trend_by", "filters"])

    def test_solo_las_piezas_del_widget(self):
        # La dona no tiene pivote: el panel no lo dibuja (antes, un select deshabilitado).
        self.assertNotIn("pivots", [p["key"] for p in parts("donut")])

    def test_piezas_ocultas_del_panel(self):
        # La caja de filtros tiene `filters` en su data_spec, pero el panel no lo edita.
        self.assertEqual([p["key"] for p in parts("filter")], ["columns"])

    def test_opciones_resueltas_desde_la_hoja(self):
        self.assertEqual(part("bar", "dimensions")["options"],
                         [{"value": "categoria", "label": "categoria"}, {"value": "mes", "label": "mes"},
                          {"value": "anio", "label": "anio"}])
        self.assertEqual([o["value"] for o in part("table", "columns")["options"]], list(COLUMNS.all))
        self.assertEqual([o["value"] for o in part("kpi", "trend_by")["options"]], list(COLUMNS.dimension))

    def test_sin_la_hoja_los_selects_llegan_vacios(self):
        self.assertEqual(part("bar", "dimensions", columns=None)["options"], [])

    def test_cotas_y_grupo_de_las_listas(self):
        dims, pivots = part("dynamic_table", "dimensions"), part("dynamic_table", "pivots")
        self.assertEqual((dims["min"], dims["max"]), (1, 3))
        self.assertEqual(dims["group"], pivots["group"])
        self.assertEqual(pivots["excludes"], ["dimensions", "pivots"])
        self.assertTrue(pivots["empty_selectable"])
        self.assertEqual((part("bar", "dimensions")["max"], part("bar", "pivots")["max"]), (1, 1))

    def test_textos_propios_del_widget(self):
        columns = part("filter", "columns")
        self.assertEqual((columns["label"], columns["add_label"], columns["allow_all"]),
                         ("Filtros", "Agregar filtro", False))
        self.assertEqual(part("table", "columns")["label"], "Columnas")

    def test_field_group_con_opciones_estaticas_y_del_builder(self):
        sort = part("bar", "sort")
        by, direction = sort["item_fields"]
        self.assertEqual(by["options_from"], "sort_targets")
        self.assertEqual(direction["options"], [{"value": "desc", "label": "Mayor a menor"},
                                                {"value": "asc", "label": "Menor a mayor"}])
        limit = part("bar", "limit")
        self.assertEqual(limit["required"], "n")
        self.assertEqual([(f["key"], f["ui"]) for f in limit["item_fields"]], [("n", "number"), ("others", "checkbox")])

    def test_cada_ui_tiene_su_componente(self):
        # Una ui sin rama en part.html no se dibujaría.
        known = set(re.findall(r"part\.ui === '([a-z-]+)'", PART_TEMPLATE.read_text()))
        used = {p["ui"] for w in WIDGETS for p in w.manifest(COLUMNS)["parts"]}
        self.assertLessEqual(used, known)
