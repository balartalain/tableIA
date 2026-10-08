"""Roles del número, meta por métrica y la mini tendencia del KPI."""
import json

import pandas as pd
from django.test import SimpleTestCase

from sheets_reports.engine.formulas import apply_calculated_fields
from sheets_reports.tests.fixtures import (agg, compiled, errors_for, fields, render, sales_df, sellers_df)


def with_field(df, name, formula):
    """`df` con un campo calculado agregado (como lo deja `load_source`)."""
    return apply_calculated_fields(df, [{"id": "c", "name": name, "formula": formula}])


def kpi_fields(*metrics, **overrides):
    return fields(dimensions=[], pivots=[], metrics=list(metrics), **overrides)


class KpiRolesTests(SimpleTestCase):
    def test_el_numero_principal_puede_cambiar_de_metrica(self):
        out = compiled("kpi", kpi_fields(agg("total_ventas"), agg("total_plan", field="plan")),
                       {"primary": "total_plan"}, df=sellers_df())
        self.assertEqual(out["value"], 1020.0)
        # Sin label personalizado, se muestra «Agregación Campo» (Suma Plan)
        self.assertEqual(out["label"], "Suma Plan")

    def test_solo_compara_lo_que_se_elige(self):
        out = compiled("kpi", kpi_fields(agg("total_ventas"), agg("total_plan", field="plan")),
                       {"compare": "total_plan"}, df=sellers_df())
        self.assertEqual(out["compare"]["value"], 1020.0)
        out = compiled("kpi", kpi_fields(agg("total_ventas"), agg("total_plan", field="plan")),
                       df=sellers_df())
        self.assertIsNone(out["compare"])

    def test_comparar_con_el_principal_no_compara(self):
        out = compiled("kpi", kpi_fields(agg("total_ventas"), agg("total_plan", field="plan")),
                       {"primary": "total_plan", "compare": "total_plan"}, df=sellers_df())
        self.assertIsNone(out["compare"])

    def test_la_meta_puede_ser_otra_metrica(self):
        out = compiled("kpi", kpi_fields(agg("total_ventas"), agg("total_plan", field="plan")),
                       {"targetMetric": "total_plan", "targetLabel": "Plan"}, df=sellers_df())
        self.assertEqual(out["target"]["value"], 1020.0)
        self.assertEqual(out["target"]["pct"], 104.9)

    def test_la_meta_por_metrica_gana_sobre_el_valor_fijo(self):
        out = compiled("kpi", kpi_fields(agg("total_ventas"), agg("total_plan", field="plan")),
                       {"target": 999, "targetMetric": "total_plan"}, df=sellers_df())
        self.assertEqual(out["target"]["value"], 1020.0)

    def test_semaforo_sobre_el_valor_sin_meta(self):
        out = compiled("kpi", kpi_fields(agg("actual")),
                       {"statusBasis": "value", "status_good": 700, "status_warn": 800,
                        "higher_is_better": False})
        self.assertIsNone(out["target"])
        self.assertEqual(out["status"], "warn")

    def test_semaforo_forzado_sobre_el_porcentaje_de_meta(self):
        out = compiled("kpi", kpi_fields(agg("actual")),
                       {"statusBasis": "target_pct", "targetMetric": "fixed", "target": 1000,
                        "status_good": 100, "status_warn": 60})
        self.assertEqual(out["status"], "warn")

    def test_semaforo_sin_base_valida_no_pinta_estado(self):
        out = compiled("kpi", kpi_fields(agg("actual")),
                       {"statusBasis": "target_pct", "status_good": 100, "status_warn": 60})
        self.assertIsNone(out["status"])


class KpiTrendTests(SimpleTestCase):
    def test_serie_por_mes_en_orden_cronologico(self):
        out = compiled("kpi", kpi_fields(agg("total_ventas"), trend_by="mes"))
        self.assertEqual(out["trend"]["categories"], ["Ene", "Feb", "Mar"])
        self.assertEqual(out["trend"]["data"], [400.0, 330.0, 25.0])
        json.dumps(out)  # las claves llegan en tipos que el JSON entiende

    def test_participacion_por_punto(self):
        """El campo agregado se evalúa en cada mes: Hogar del mes sobre el total del mes."""
        df = with_field(sales_df(), "Participación",
                        'SUM(IF([categoria] = "Hogar", [ventas], 0)) / SUM([ventas]) * 100')
        out = compiled("kpi", kpi_fields({"field": "Participación", "agg": "auto", "alias": "participacion"},
                                         trend_by="mes"), df=df)
        self.assertAlmostEqual(out["value"], 23.18, places=2)   # 175 / 755
        self.assertEqual(out["trend"]["categories"], ["Ene", "Feb", "Mar"])
        self.assertEqual([round(v, 2) for v in out["trend"]["data"]], [25.0, 15.15, 100.0])

    def test_sin_trend_by_no_hay_serie(self):
        out = compiled("kpi", kpi_fields(agg("total_ventas")))
        self.assertNotIn("trend", out)

    def test_un_filtro_eq_sobre_la_columna_no_vacia_la_serie(self):
        out = compiled("kpi", kpi_fields(
            agg("total_ventas", filters=[{"field": "mes", "op": "eq", "value": "Ene"}]),
            trend_by="mes",
        ))
        self.assertEqual(out["value"], 400.0)
        self.assertEqual(out["trend"]["data"], [400.0, 330.0, 25.0])

    def test_con_muchos_grupos_quedan_los_mas_recientes(self):
        years = list(range(2000, 2070))
        df = pd.DataFrame({"anio": years, "ventas": [float(y) for y in years]})
        out = compiled("kpi", kpi_fields(agg("total_ventas"), trend_by="anio"), df=df)
        self.assertEqual(len(out["trend"]["categories"]), 60)
        self.assertEqual(out["trend"]["categories"][-1], 2069)
        self.assertEqual(out["trend"]["data"][-1], 2069.0)
        json.dumps(out)

    def test_la_serie_es_del_numero_principal_elegido(self):
        df = with_field(sellers_df(), "Diferencia", "SUM([ventas]) - SUM([plan])")
        out = compiled("kpi", kpi_fields(
            agg("total_ventas"), agg("total_plan", field="plan"),
            {"field": "Diferencia", "agg": "auto", "alias": "diferencia"},
            trend_by="anio",
        ), {"primary": "diferencia"}, df=df)
        self.assertEqual(out["value"], 50.0)
        self.assertEqual(out["trend"]["categories"], [2025, 2026])
        self.assertEqual(out["trend"]["data"], [0.0, 50.0])

    def test_hoja_vacia_no_lanza_y_no_pinta_tendencia(self):
        out = compiled("kpi", kpi_fields(agg("total_ventas"), trend_by="mes"),
                       df=sales_df().head(0))
        self.assertNotIn("trend", out)


class KpiValidationTests(SimpleTestCase):
    def test_trend_by_valido_no_da_errores(self):
        self.assertEqual(errors_for(
            "kpi", {"dimensions": [], "metrics": [agg("a")], "trend_by": "mes"}), [])

    def test_trend_by_en_un_widget_sin_tendencia(self):
        errors = errors_for("bar", fields(dimensions=["categoria"], metrics=[agg()],
                                          trend_by="mes"))
        self.assertTrue(any("no muestra tendencia" in e for e in errors), errors)

    def test_trend_by_con_columna_inexistente(self):
        errors = errors_for("kpi", {"dimensions": [], "metrics": [agg("a")],
                                    "trend_by": "inventada"})
        self.assertTrue(any("no existe en la hoja" in e for e in errors), errors)

    def test_roles_con_alias_inexistente(self):
        errors = errors_for("kpi", {"dimensions": [], "metrics": [agg("a")]},
                            {"primary": "otra", "compare": "tambien"})
        self.assertEqual(len(errors), 2, errors)

    def test_roles_con_alias_valido(self):
        self.assertEqual(errors_for("kpi", {"dimensions": [], "metrics": [agg("a"), agg("b")]},
                                    {"primary": "b", "compare": "a", "targetMetric": "b"}), [])

    def test_semaforo_por_meta_sin_meta_se_rechaza(self):
        errors = errors_for("kpi", {"dimensions": [], "metrics": [agg("a")]},
                            {"statusBasis": "target_pct"})
        self.assertTrue(any("necesita una meta" in e for e in errors), errors)
        self.assertEqual(errors_for("kpi", {"dimensions": [], "metrics": [agg("a")]},
                                    {"statusBasis": "target_pct", "targetMetric": "fixed",
                                     "target": 100}), [])
        # Un valor fijo sin «Valor fijo» elegido no es meta: no pinta la barra.
        out = compiled("kpi", kpi_fields(agg("actual")), {"target": 999})
        self.assertIsNone(out["target"])


class KpiCompileTests(SimpleTestCase):
    def kpi(self, *metrics, **overrides):
        return fields(dimensions=[], pivots=[], metrics=list(metrics), **overrides)

    def test_comparacion_con_la_metrica_elegida(self):
        out = compiled("kpi", self.kpi(
            agg("actual", filters=[{"field": "anio", "op": "eq", "value": 2026}]),
            agg("anterior", filters=[{"field": "anio", "op": "eq", "value": 2025}]),
        ), {"compare": "anterior"})
        self.assertEqual(out["value"], 675.0)
        # Sin label personalizado, se muestra «Agregación Campo»
        self.assertEqual(out["compare"]["label"], "Suma Ventas")
        self.assertEqual(out["compare"]["value"], 80.0)
        self.assertEqual(out["compare"]["mode"], "pct")
        self.assertTrue(out["compare"]["better"])

    def test_sin_elegir_comparacion_no_compara(self):
        out = compiled("kpi", self.kpi(
            agg("actual", filters=[{"field": "anio", "op": "eq", "value": 2026}]),
            agg("anterior", filters=[{"field": "anio", "op": "eq", "value": 2025}]),
        ))
        self.assertEqual(out["value"], 675.0)
        self.assertIsNone(out["compare"])

    def test_comparacion_con_alias_inexistente_no_compara(self):
        out = compiled("kpi", self.kpi(
            agg("actual"), agg("anterior"),
        ), {"compare": "inexistente"})
        self.assertIsNone(out["compare"])

    def test_el_numero_principal_puede_ser_otra_metrica(self):
        out = compiled("kpi", self.kpi(
            agg("actual", filters=[{"field": "anio", "op": "eq", "value": 2026}]),
            agg("anterior", filters=[{"field": "anio", "op": "eq", "value": 2025}]),
        ), {"primary": "anterior"})
        self.assertEqual(out["value"], 80.0)
        # Sin label personalizado, se muestra «Agregación Campo»
        self.assertEqual(out["label"], "Suma Ventas")

    def test_comparacion_cuando_menos_es_mejor(self):
        out = compiled("kpi", self.kpi(
            agg("actual", filters=[{"field": "anio", "op": "eq", "value": 2026}]),
            agg("anterior", filters=[{"field": "anio", "op": "eq", "value": 2025}]),
        ), {"compare": "anterior", "higher_is_better": False})
        self.assertFalse(out["compare"]["better"])

    def test_meta_y_semaforo(self):
        out = compiled("kpi", self.kpi(agg("actual")),
                       {"targetMetric": "fixed", "target": 1000, "status_good": 100, "status_warn": 60})
        self.assertEqual(out["target"]["label"], "Meta")
        self.assertEqual(out["target"]["value"], 1000.0)
        self.assertEqual(out["status"], "warn")

    def test_semaforo_por_valor_sin_meta(self):
        out = compiled("kpi", self.kpi(agg("actual")),
                       {"higher_is_better": False, "status_good": 700, "status_warn": 800})
        self.assertIsNone(out["target"])
        self.assertEqual(out["status"], "warn")

    def test_el_formato_de_la_metrica_va_al_front(self):
        """El número lo formatea el front (formatNumber): el servidor manda el formato de la
        métrica principal (el de un campo calculado porcentaje, el elegido…)."""
        self.assertIsNone(compiled("kpi", self.kpi(agg("actual")))["format"])
        chosen = compiled("kpi", self.kpi({**agg("actual"), "format": "currency"}))
        self.assertEqual(chosen["format"], "currency")

    def test_los_defaults_del_estilo_vienen_de_backend(self):
        out = render("kpi", self.kpi(agg("actual")))
        self.assertEqual(out["widget_form"]["style"]["decimals"], 0)
        self.assertEqual(out["widget_form"]["style"]["compareMode"], "pct")
