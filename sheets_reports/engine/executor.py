"""
Ejecutor determinista del DSL: el ÚNICO lugar donde se hacen sumas, promedios y conteos
reales. La IA no participa aquí bajo ninguna circunstancia; `spec` ya viene validado. Solo
usa operaciones vectorizadas de pandas: nada de DataFrame.query()/eval() ni ninguna otra forma
de evaluar texto.

No conoce widgets ni tipos de métrica: hace los pasos comunes y delega en el plan.
"""
import pandas as pd

from sheets_reports.dsl.conditions import apply_filters
from sheets_reports.dsl.groups import having_mask
from sheets_reports.dsl.metrics import flat_table
from sheets_reports.dsl.spec import DataSpec
from sheets_reports.engine.plans.base import OTHERS_LABEL, R, PlanInput, ResultPlan, sorted_table


def run(spec: DataSpec, df: pd.DataFrame, plan: ResultPlan[R]) -> R:
    # Universo de los porcentajes: la hoja antes de las condiciones del widget.
    universe = df
    spec = spec.resolved(universe)
    df = apply_filters(df, spec.filters)
    has_others = False
    if plan.aggregates and spec.dimensions:
        df, has_others = restrict_groups(df, spec)
    return plan.run(spec, PlanInput(df=df, universe=universe, has_others=has_others))


def restrict_groups(df: pd.DataFrame, spec: DataSpec) -> tuple[pd.DataFrame, bool]:
    """
    `having` y `limit` sobre los grupos de la primera dimensión: se calculan las métricas por
    grupo, se eligen los grupos y se recortan las FILAS del df a esos grupos. Así todo lo que
    sigue (pivotes, subtotales, porcentajes) se calcula sobre los grupos que se muestran.
    Con limit.others, los grupos fuera del Top N se renombran a «Otros» (sus valores se
    recalculan desde los datos, así funciona con cualquier agregación).
    Retorna (df, hay_grupo_otros).
    """
    if not spec.having and not spec.limit:
        return df, False
    dimension = spec.dimensions[0]
    table = flat_table(df, dimension, spec.metrics)
    table = table[having_mask(table, spec.having)]
    kept = list(table[dimension])
    if not spec.limit:
        return df[df[dimension].isin(kept)], False
    top = list(sorted_table(table, spec.sort)[dimension][:spec.limit.n])
    rest = [k for k in kept if k not in set(top)]
    if not (spec.limit.others and rest):
        return df[df[dimension].isin(top)], False
    df = df[df[dimension].isin(kept)].copy()
    df[dimension] = df[dimension].astype(object).where(df[dimension].isin(top), OTHERS_LABEL)
    return df, True
