"""DataSpec por piezas: cada widget guarda solo sus claves, ignora lo ajeno vacío que manda el
builder y rechaza lo ajeno con valor con el mensaje de esa pieza."""
from django.test import SimpleTestCase

from sheets_reports.dsl.parts import Dimensions, Metrics
from sheets_reports.dsl.spec import GroupedSpec, RowsSpec, ScalarSpec, with_parts
from sheets_reports.tests.fixtures import agg, errors_for, spec


class DataSpecTests(SimpleTestCase):
    def test_guarda_solo_sus_claves(self):
        kpi = ScalarSpec.from_dict(spec(dimensions=[]))
        self.assertEqual(set(kpi.to_dict()), {"source", "filters", "metrics", "trend_by"})
        table = RowsSpec.from_dict(spec(columns=["ventas"], metrics=[], dimensions=[]))
        self.assertEqual(set(table.to_dict()), {"source", "columns", "filters", "sort"})

    def test_normalize_descarta_lo_ajeno_vacio_y_deja_lo_ajeno_con_valor(self):
        raw = ScalarSpec.normalize({**spec(dimensions=[]), "having": [{"left": "a", "op": "lt", "right": 1}]})
        self.assertNotIn("pivots", raw)
        self.assertIn("having", raw)
        # Una clave desconocida no se toca: la rechaza el schema.
        self.assertIn("code", ScalarSpec.normalize({**spec(), "code": "x"}))

    def test_with_defaults_completa_lo_que_falta(self):
        raw = GroupedSpec.with_defaults({"source": "0", "dimensions": ["categoria"], "metrics": [agg("v")]})
        self.assertEqual((raw["pivots"], raw["having"], raw["sort"], raw["limit"]), ([], [], None, None))

    def test_acceso_por_clave_e_inmutable(self):
        grouped = GroupedSpec.from_dict(spec(pivots=["mes"]))
        self.assertEqual((grouped.dimensions, grouped.pivots, grouped.aliases), (["categoria"], ["mes"], ["total_ventas"]))
        with self.assertRaises(AttributeError):
            grouped.trend_by
        with self.assertRaises(AttributeError):
            grouped.dimensions = []

    def test_with_parts_reemplaza_por_clave_y_quita(self):
        parts = with_parts(GroupedSpec, Dimensions(1, 3), Metrics(1, 1), without=("pivots",))
        self.assertEqual([p.key for p in parts], ["dimensions", "filters", "metrics", "having", "sort", "limit"])
        self.assertEqual(parts[0].high, 3)

    def test_clave_ajena_con_valor_da_el_mensaje_de_su_pieza(self):
        self.assertIn("trend_by: «Gráfico de Barras» no muestra tendencia.", errors_for("bar", spec(trend_by="mes")))
        self.assertIn("columns: este tipo de widget no muestra columnas sueltas (agrupa con dimensiones).",
                      errors_for("bar", spec(columns=["ventas"])))
