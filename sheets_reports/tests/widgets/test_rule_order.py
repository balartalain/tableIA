"""El orden de los errores lo deciden la etapa y la prioridad de cada regla, no su posición en
la lista de rules()."""
from django.test import SimpleTestCase

from sheets_reports.dsl.rules import BUSINESS, METRICS, REFERENCES, STRUCTURE, Rule, Stage
from sheets_reports.tests.fixtures import agg, errors_for, sales_ctx, spec
from sheets_reports.widgets import WIDGETS
from sheets_reports.widgets.bar import BarWidget


class RuleOrderTests(SimpleTestCase):
    def test_regla_de_negocio_sale_sola(self):
        # Pivote con varias métricas, una columna inexistente y un alias inválido a la vez.
        errors = errors_for("bar", spec(pivots=["mes"], dimensions=["region"],
                                        metrics=[agg("Total"), agg("cantidad", "count")]))
        self.assertEqual(len(errors), 1)
        self.assertTrue(errors[0].startswith("Con pivote solo se permite una métrica"))

    def test_todas_las_reglas_estan_en_una_banda_conocida(self):
        bands = {BUSINESS, STRUCTURE, METRICS, REFERENCES}
        for widget in WIDGETS:
            for rule in widget.rules():
                self.assertIn(rule.priority, bands, f"{widget.key}: {type(rule).__name__}")
                if rule.blocking:
                    self.assertEqual(rule.priority, BUSINESS, type(rule).__name__)

    def test_el_orden_no_depende_de_la_posicion_en_rules(self):
        data_spec = spec(dimensions=["categoria", "categoria"], sort={"by": "nada", "dir": "asc"},
                         metrics=[agg("total"), agg("total", "avg")])
        widget = WIDGETS.get("dynamic_table")
        expected = widget.errors(data_spec, sales_ctx())

        class Reversed(type(widget)):
            def rules(self):
                return list(reversed(super().rules()))

        self.assertEqual(Reversed().errors(data_spec, sales_ctx()), expected)
        self.assertEqual(expected, [
            "dimensions: no se puede repetir una columna.",
            "metrics[1].as: el nombre 'total' está repetido.",
            "sort.by: 'nada' no es válido; usa una de: categoria, categoria, total, total.",
        ])

    def test_regla_nueva_con_prioridad_de_negocio_va_primero_aunque_se_agregue_al_final(self):
        class NoBarsOnSunday(Rule):
            stage, priority, blocking = Stage.PRE_SCHEMA, BUSINESS, True

            def check(self, raw, widget, ctx):
                return ["Hoy no hay barras."]

        class Strict(BarWidget):
            def rules(self):
                return [*super().rules(), NoBarsOnSunday()]

        self.assertEqual(Strict().errors(spec(dimensions=["region"]), sales_ctx()), ["Hoy no hay barras."])
