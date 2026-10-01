from dataclasses import dataclass, field

import pandas as pd

from sheets_reports.dsl.metrics import Metric, evaluation_order
from sheets_reports.dsl.spec import DataSpec
from sheets_reports.dsl.values import percent, sort_key, to_key, to_python
from sheets_reports.engine.plans.base import (
    PLANS,
    PlanInput,
    PlanResult,
    ResultPlan,
    ResultTooLargeError,
    others_last,
)

# Tope de celdas de una tabla dinámica (filas × columnas × métricas): un cruce mayor no se
# puede leer y congelaría el navegador.
MAX_TABLE_CELLS = 20000


@dataclass
class PivotBlock:
    cells: dict = field(default_factory=dict)   # {clave_col: {as: valor}}
    totals: dict = field(default_factory=dict)  # {as: valor}


@dataclass
class PivotRow(PivotBlock):
    key: tuple = ()
    subtotal: bool = False


@dataclass(frozen=True)
class PivotTableResult(PlanResult):
    """
    Tabla dinámica con varias filas (`dimensions`) y columnas (`pivots`) anidadas. Cada fila y
    cada columna tiene una clave (tupla de valores); las claves más cortas que la profundidad
    son subtotales. `rows` va en orden de despliegue (cada grupo seguido de su subtotal);
    `grand` es la fila de totales.
    """
    dimensions: list
    pivots: list
    metrics: list
    column_keys: list
    rows: list[PivotRow]
    grand: PivotBlock


def _ordered_keys(df: pd.DataFrame, columns: list[str]) -> list[tuple]:
    """Combinaciones de `columns` presentes en `df`, en orden de aparición en la hoja."""
    if not columns:
        return []
    rows = df[columns].itertuples(index=False, name=None)
    return list(dict.fromkeys(tuple(to_key(v) for v in row) for row in rows))


def _hierarchy(leaf_keys: list[tuple], sort_level=None) -> list[tuple]:
    """
    Recorre las claves hoja como un árbol y devuelve todas las claves en orden de despliegue:
    los hijos de cada grupo y, al terminar el grupo, su subtotal (la clave del grupo, más
    corta), como las tablas dinámicas de Sheets. `sort_level(level, siblings)` ordena los
    hermanos de cada nivel.
    """
    depth = len(leaf_keys[0]) if leaf_keys else 0
    children: dict[tuple, dict] = {}
    for key in leaf_keys:
        for level in range(depth):
            children.setdefault(key[:level], {})[key[:level + 1]] = None

    ordered = []

    def walk(prefix):
        siblings = list(children.get(prefix, {}))
        if sort_level:
            siblings = sort_level(len(prefix), siblings)
        for child in siblings:
            if len(child) == depth:
                ordered.append(child)
            else:
                walk(child)
                ordered.append(child)

    walk(())
    return ordered


@PLANS.register
class PivotTablePlan(ResultPlan[PivotTableResult]):
    """
    Los valores se calculan desde los datos en cada nivel (nunca sumando celdas) y `show_as`
    divide entre el total de la fila, de la columna o el general de ese mismo nivel. Las
    métricas derivadas (cálculos) se evalúan en cada celda con los valores ya mostrados de las
    métricas a las que se refieren.
    """
    key = "pivot_table"

    def run(self, spec: DataSpec, data: PlanInput) -> PivotTableResult:
        dimensions, pivots, sort = list(spec.dimensions), list(spec.pivots), spec.sort
        df = data.df.dropna(subset=[*dimensions, *pivots])
        by_name = {m.alias: m for m in spec.metrics}
        ordered = evaluation_order(spec.metrics)
        aggregated = [m for m in ordered if not m.derived]
        derived = [m for m in ordered if m.derived]

        cache: dict = {}

        def raw(columns, metric: Metric) -> dict:
            """{tupla de valores de `columns`: valor crudo}; () es el total general."""
            cache_key = (tuple(columns), metric.alias)
            if cache_key not in cache:
                if not columns:
                    cache[cache_key] = {(): metric.grand_total(df)}
                else:
                    cache[cache_key] = {
                        tuple(to_key(k) for k in (key if isinstance(key, tuple) else (key,))): to_python(v)
                        for key, v in metric.aggregate(df, list(columns)).items()
                    }
            return cache[cache_key]

        def level_values(columns, metric: Metric) -> dict:
            """Valor de `metric` en cada grupo de `columns` (para ordenar): crudo en las
            agregadas, derivado en las demás."""
            if not metric.derived:
                return raw(columns, metric)
            deps = {ref: level_values(columns, by_name[ref]) for ref in metric.depends_on()}
            keys = list(dict.fromkeys(k for values in deps.values() for k in values))
            return {k: metric.derive({ref: values.get(k) for ref, values in deps.items()}) for k in keys}

        def sort_rows(level, siblings):
            if sort:
                reverse = sort.descending
                if sort.by in dimensions:
                    if dimensions.index(sort.by) == level:
                        siblings = sorted(siblings, key=lambda k: sort_key(k[-1]), reverse=reverse)
                else:
                    # Por una métrica: cada nivel se ordena por el valor de su grupo; vacíos al final.
                    values = level_values(dimensions[:level + 1], by_name[sort.by])
                    present = [k for k in siblings if values.get(k) is not None]
                    missing = [k for k in siblings if values.get(k) is None]
                    siblings = sorted(present, key=lambda k: values[k], reverse=reverse) + missing
            return others_last(siblings, key=lambda k: k[0]) if level == 0 else siblings

        row_keys = _hierarchy(_ordered_keys(df, dimensions), sort_rows)
        column_keys = _hierarchy(_ordered_keys(df, pivots))

        size = len(row_keys) * (len(column_keys) + 1) * len(spec.metrics)
        if size > MAX_TABLE_CELLS:
            raise ResultTooLargeError(
                f"La tabla tendría {size:,} celdas (máximo {MAX_TABLE_CELLS:,}). Quita un nivel de "
                f"filas o columnas, o agrega filtros."
            )

        rows = [PivotRow(key=key, subtotal=len(key) < len(dimensions)) for key in row_keys]
        grand = PivotBlock()
        for metric in aggregated:
            name, show_as, empty = metric.alias, metric.show_as, metric.empty_value
            grand_value = raw([], metric)[()]

            def column_total(col_key):
                return raw(pivots[:len(col_key)], metric).get(col_key, empty)

            def shown(value, row_total, col_total):
                if show_as == "value":
                    return value
                whole = {"pct_row": row_total, "pct_column": col_total}.get(show_as, grand_value)
                return percent(value, whole)

            for row in rows:
                key = row.key
                row_total = raw(dimensions[:len(key)], metric).get(key, empty)
                for col_key in column_keys:
                    columns = dimensions[:len(key)] + pivots[:len(col_key)]
                    value = raw(columns, metric).get(key + col_key, empty)
                    row.cells.setdefault(col_key, {})[name] = shown(value, row_total, column_total(col_key))
                row.totals[name] = shown(row_total, row_total, grand_value)
            for col_key in column_keys:
                col_total = column_total(col_key)
                grand.cells.setdefault(col_key, {})[name] = shown(col_total, grand_value, col_total)
            grand.totals[name] = shown(grand_value, grand_value, grand_value)

        if derived:
            for block in [*rows, grand]:
                for values in [*block.cells.values(), block.totals]:
                    for metric in derived:
                        values[metric.alias] = metric.derive(values)

        return PivotTableResult(
            dimensions=dimensions,
            pivots=pivots,
            metrics=spec.aliases,
            column_keys=column_keys,
            rows=rows,
            grand=grand,
        )
