"""Las columnas que usa cada widget: un recorrido de `fields` y `style` para renombrar, avisar
del impacto de un cambio de columnas y resolver los nombres de la IA."""
from types import SimpleNamespace

from django.test import SimpleTestCase

from sheets_reports.services.source_columns import column_refs, impact, map_columns

FIELDS = {
    "dimensions": ["categoria"],
    "pivots": ["mes"],
    "columns": [{"field": "ventas", "label": "Monto"}],
    "metrics": [
        {"agg": "sum", "field": "ventas", "alias": "total",
         "filters": [{"field": "anio", "op": "eq", "value": 2026}]},
        {"type": "formula", "alias": "doble", "expression": "total * 2"},
    ],
    "filters": [{"field": "categoria", "op": "ne", "value": "Ropa"}],
    "trend_by": "mes",
    "sort_by": "-ventas",
}
STYLE = {"columnOrder": ["categoria", "ventas"], "stacked": True}


class MapColumnsTests(SimpleTestCase):
    def test_cubre_todas_las_referencias_a_columnas(self):
        fields, style = map_columns(FIELDS, STYLE, str.upper)
        self.assertEqual(fields["dimensions"], ["CATEGORIA"])
        self.assertEqual(fields["pivots"], ["MES"])
        self.assertEqual(fields["columns"], [{"field": "VENTAS", "label": "Monto"}])
        self.assertEqual(fields["metrics"][0]["field"], "VENTAS")
        self.assertEqual(fields["metrics"][0]["filters"][0]["field"], "ANIO")
        self.assertEqual(fields["metrics"][1], FIELDS["metrics"][1])   # las fórmulas usan alias
        self.assertEqual(fields["filters"][0]["field"], "CATEGORIA")
        self.assertEqual(fields["trend_by"], "MES")
        self.assertEqual(fields["sort_by"], "-VENTAS")                 # conserva el «-»
        self.assertEqual(style, {"columnOrder": ["CATEGORIA", "VENTAS"], "stacked": True})

    def test_no_modifica_los_originales(self):
        map_columns(FIELDS, STYLE, str.upper)
        self.assertEqual(FIELDS["dimensions"], ["categoria"])
        self.assertEqual(STYLE["columnOrder"], ["categoria", "ventas"])

    def test_tolera_formas_incompletas(self):
        fields, style = map_columns({"metrics": [None, {"agg": "count"}], "sort_by": None}, None, str.upper)
        self.assertEqual(fields, {"metrics": [None, {"agg": "count"}], "sort_by": None})
        self.assertEqual(style, {})

    def test_column_refs(self):
        self.assertEqual(column_refs(FIELDS, STYLE), {"categoria", "mes", "ventas", "anio"})


class ImpactTests(SimpleTestCase):
    def widget(self, id, **fields):
        return SimpleNamespace(id=id, title=f"W{id}", fields=fields, style={})

    def test_widgets_con_columnas_que_desaparecen_o_cambian_de_tipo(self):
        widgets = [self.widget(1, dimensions=["categoria"]),
                   self.widget(2, metrics=[{"agg": "sum", "field": "ventas", "alias": "total"}],
                               sort_by="-total"),
                   self.widget(3, dimensions=["mes"])]
        result = impact(widgets, current={"categoria", "mes", "ventas"},
                        available={"mes", "ventas"}, retyped={"ventas"})
        self.assertEqual(result, [{"id": 1, "title": "W1", "columns": ["categoria"]},
                                  {"id": 2, "title": "W2", "columns": ["ventas"]}])

    def test_un_alias_en_sort_by_no_cuenta_como_columna(self):
        widgets = [self.widget(1, sort_by="-total")]
        self.assertEqual(impact(widgets, current={"ventas"}, available=set()), [])
