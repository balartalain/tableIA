from dataclasses import dataclass

from sheets_reports.dsl.spec import DataSpec
from sheets_reports.dsl.values import percent, series_dict, sort_key, to_key
from sheets_reports.engine.plans.base import PLANS, PlanInput, PlanResult, ResultPlan, others_last


@dataclass(frozen=True)
class PivotChartResult(PlanResult):
    """Cruce de una dimensión y un pivote para UNA métrica (una serie por valor del pivote).

    rows:          {valor_dim: {valor_pivote: {as: valor}}}
    row_totals:    {valor_dim: {as: valor}}
    column_totals: {valor_pivote: {as: valor}}
    grand_totals:  {as: valor}
    """
    dimension: str
    pivot: str
    metric: str
    dimension_values: list
    pivot_values: list
    rows: dict
    row_totals: dict
    column_totals: dict
    grand_totals: dict


@PLANS.register
class PivotChartPlan(ResultPlan[PivotChartResult]):
    key = "pivot_chart"

    def run(self, spec: DataSpec, data: PlanInput) -> PivotChartResult:
        dimension, pivot, metric = spec.dimensions[0], spec.pivots[0], spec.metrics[0]
        df = data.df[data.df[dimension].notna() & data.df[pivot].notna()]
        dimension_values = [to_key(v) for v in df[dimension].unique()]
        pivot_values = [to_key(v) for v in df[pivot].unique()]
        name, show_as, empty = metric.alias, metric.show_as, metric.empty_value
        cells = series_dict(metric.aggregate(df, [dimension, pivot]))
        by_row = series_dict(metric.aggregate(df, dimension))
        by_column = series_dict(metric.aggregate(df, pivot))
        grand = metric.grand_total(df)

        def shown(value, d=None, p=None):
            if show_as == "value":
                return value
            if show_as == "pct_row":
                whole = by_row.get(d, empty) if d is not None else grand
            elif show_as == "pct_column":
                whole = by_column.get(p, empty) if p is not None else grand
            else:
                whole = grand
            return percent(value, whole)

        rows = {d: {p: {name: shown(cells.get((d, p), empty), d, p)} for p in pivot_values}
                for d in dimension_values}
        row_totals = {d: {name: shown(by_row.get(d, empty), d=d)} for d in dimension_values}
        column_totals = {p: {name: shown(by_column.get(p, empty), p=p)} for p in pivot_values}

        if spec.sort:
            reverse = spec.sort.descending
            if spec.sort.by == dimension:
                dimension_values.sort(key=sort_key, reverse=reverse)
            else:
                # Ordena por el total de la fila en esa métrica; los vacíos siempre al final.
                present = [d for d in dimension_values if row_totals[d][name] is not None]
                missing = [d for d in dimension_values if row_totals[d][name] is None]
                present.sort(key=lambda d: row_totals[d][name], reverse=reverse)
                dimension_values = present + missing

        return PivotChartResult(
            dimension=dimension,
            pivot=pivot,
            metric=name,
            dimension_values=others_last(dimension_values),
            pivot_values=pivot_values,
            rows=rows,
            row_totals=row_totals,
            column_totals=column_totals,
            grand_totals={name: shown(grand)},
        )
