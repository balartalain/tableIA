"""
Ejecutor determinista del DSL: el ÚNICO lugar donde se hacen sumas, promedios y conteos
reales. La IA no participa aquí bajo ninguna circunstancia; `spec` ya viene validado. Solo
usa operaciones vectorizadas de pandas: nada de DataFrame.query()/eval() ni ninguna otra forma
de evaluar texto.

No conoce widgets ni claves del data_spec: resuelve los valores relativos, corre el paso de
cada pieza (filters, having, limit...) en su orden y delega en el plan.
"""
import pandas as pd

from sheets_reports.dsl.parts import Rows
from sheets_reports.dsl.spec import DataSpec
from sheets_reports.engine.plans.base import R, PlanInput, ResultPlan


def run(spec: DataSpec, df: pd.DataFrame, plan: ResultPlan[R]) -> R:
    # Universo de los porcentajes: la hoja antes de las condiciones del widget.
    universe = df
    spec = spec.resolved(universe)
    rows = Rows(df)
    for part in sorted(spec.parts, key=lambda p: p.order):
        rows = part.prepare(spec, rows, aggregates=plan.aggregates)
    return plan.run(spec, PlanInput(df=rows.df, universe=universe, has_others=rows.has_others))
