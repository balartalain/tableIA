"""
Pipeline de ejecución secuencial para widgets.

    Widget.process_query(df, fields)
       └──> PipelineExecutor.execute(df, fields)
              ├── 1. FilterStep              (dsl/conditions)
              ├── 2. AggregationOrPivotStep  (groupby + agg, o pivot_table)
              ├── 3. CalculatedMetricsStep   (métricas tipo "formula")
              ├── 4. WindowFunctionsStep     (percent_of_total, running_total, ...)
              └── 5. SortLimitStep           (sort_by + limit)

Reemplaza a engine/executor.py y a engine/plans/*.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import pandas as pd

from sheets_reports.dsl.conditions import Condition, apply_filters, parse_conditions
from sheets_reports.dsl.values import to_python

if TYPE_CHECKING:
    from sheets_reports.widgets.schemas import WidgetFields


MAX_PIVOT_CELLS = 50_000

# Claves de celda de una tabla dinámica: separan los valores de varios pivotes ("2026" y
# "Ene"). Es un carácter de control: no aparece en los valores de una hoja.
KEY_SEPARATOR = "\x1f"


def cell_field(col_key: tuple, metric: str) -> str:
    """Clave plana de la celda (fila, columna) de una tabla dinámica. El frontend desactiva
    el separador de campos anidados de Tabulator, así que los puntos no rompen nada."""
    return "__pivots." + KEY_SEPARATOR.join(str(v) for v in col_key) + "." + metric


def total_field(metric: str) -> str:
    """Clave de la columna «Total general» de una métrica en una tabla con pivotes."""
    return f"__total.{metric}"



class ResultTooLargeError(ValueError):
    """El resultado de la consulta supera el máximo de celdas permitido."""

    def __init__(self, message: str = "El resultado es demasiado grande para mostrarlo."):
        super().__init__(message)


DEFAULT_STEPS = [
    "filter",
    "aggregation",
    "calculated",
    "window",
    "sort_limit",
]


@dataclass
class PipelineContext:
    """Contexto que se pasa entre pasos del pipeline."""

    df: pd.DataFrame
    fields: "WidgetFields"
    metadata: Dict[str, Any] = field(default_factory=dict)
    # Tipo del widget que pidió la consulta: algunos (tabla dinámica) necesitan el resultado
    # jerárquico con subtotales en vez del frame plano.
    widget_type: Optional[str] = None


class PipelineStep(ABC):
    """Paso base del pipeline: recibe el contexto y devuelve el contexto transformado."""

    name: str = "step"

    @abstractmethod
    def execute(self, ctx: PipelineContext) -> PipelineContext:
        ...

    def __call__(self, ctx: PipelineContext) -> PipelineContext:
        return self.execute(ctx)


# Agregaciones aceptadas en `fields.metrics[].agg` → nombre de Pandas.
AGGREGATIONS: Dict[str, str] = {
    "sum": "sum",
    "avg": "mean",
    "mean": "mean",
    "median": "median",
    "min": "min",
    "max": "max",
    "std": "std",
    "count": "count",
    "count_distinct": "nunique",
}


def agg_name(agg: str) -> str:
    """Traduce una agregación del form al nombre que entiende Pandas."""
    return AGGREGATIONS.get(agg, agg)


def _aggregate(series: pd.Series, agg: str):
    if agg == "count":
        return series.count()
    return series.agg(agg_name(agg))


def _metric_columns(df: pd.DataFrame, metrics: list) -> tuple[pd.DataFrame, list[tuple[str, str, str]]]:
    """
    Una columna intermedia por métrica: dos métricas sobre el mismo campo (suma y promedio)
    no colisionan, y `count` (sin campo) se resuelve con unos. Devuelve
    (frame, [(columna, agregación pandas, alias), ...]).

    Las condiciones propias de la métrica enmascaran sus filas como NaN: todas las
    agregaciones aceptadas (sum, avg, count, nunique...) ignoran los NaN, así que cada
    métrica resume solo sus filas dentro del mismo groupby/pivote.
    """
    df = df.copy()
    specs = []
    for i, metric in enumerate(metrics):
        if metric.get("type") == "formula":
            continue  # la calcula CalculatedMetricsStep, sobre las columnas ya agregadas
        field_name, agg, alias = _get_field(metric), metric.get("agg"), _get_alias(metric)
        if field_name:
            df[f"__metric_{i}"] = df[field_name]
        elif agg == "count":
            df[f"__metric_{i}"] = 1
        else:
            continue
        if metric.get("filters"):
            df.loc[~_filter_mask(df, metric), f"__metric_{i}"] = float("nan")
        specs.append((f"__metric_{i}", agg_name(agg), alias))
    return df, specs


def _get_alias(metric: dict) -> str:
    """Alias de una métrica; admite la clave legada «as» de datos ya guardados."""
    return metric.get("alias") or metric.get("as") or ""


def _get_field(metric: dict) -> str:
    """Columna de la hoja que resume la métrica."""
    return metric.get("field", "")


def _filter_mask(df: pd.DataFrame, metric: dict) -> pd.Series:
    """Máscara booleana de las filas que cumplen las condiciones propias de la métrica."""
    filters = metric.get("filters") or []
    if not filters:
        return pd.Series(True, index=df.index)
    mask = pd.Series(True, index=df.index)
    for condition in parse_conditions(filters):
        mask &= condition.mask(df)
    return mask.fillna(False)


def _metric_filters(df: pd.DataFrame, metric: dict) -> pd.DataFrame:
    """Aplica las condiciones propias de una métrica (si las trae)."""
    if not metric.get("filters"):
        return df
    return df[_filter_mask(df, metric)]


# ------------------------------------------------------- claves jerárquicas
def _ordered_keys(df: pd.DataFrame, columns: list) -> List[tuple]:
    """Combinaciones de `columns` presentes en el frame, en orden de aparición y sin nulos."""
    if not columns:
        return []
    rows = df[list(columns)].dropna().itertuples(index=False, name=None)
    return list(dict.fromkeys(tuple(to_python(v) for v in row) for row in rows))


def _hierarchy(leaf_keys: List[tuple], sort_key=None) -> List[tuple]:
    """
    Recorre las claves hoja como un árbol y devuelve todas las claves en orden de despliegue:
    los hijos de cada grupo y, al terminar el grupo, su clave (el subtotal), como las tablas
    dinámicas de Sheets. `sort_key(level, siblings)` reordena los hermanos de ese nivel.
    """
    depth = len(leaf_keys[0]) if leaf_keys else 0
    if not depth:
        return []
    children: Dict[tuple, List[tuple]] = {}
    for key in leaf_keys:
        for level in range(depth):
            children.setdefault(key[:level], []).append(key[:level + 1])

    ordered: List[tuple] = []

    def walk(prefix: tuple) -> None:
        siblings = list(dict.fromkeys(children.get(prefix, [])))
        if sort_key:
            siblings = sort_key(len(prefix), siblings)
        for child in siblings:
            if len(child) == depth:
                ordered.append(child)
            else:
                walk(child)
                ordered.append(child)

    walk(())
    return ordered


def _grouped_values(df: pd.DataFrame, specs: list, by: list) -> Dict[tuple, dict]:
    """{clave (tupla) → {alias: valor}} agregando `df` por `by`; sin `by`, el total general."""
    if not by:
        return {(): {alias: to_python(df[col].agg(func)) for col, func, alias in specs}}
    cols = [col for col, _, _ in specs]
    funcs = {col: func for col, func, _ in specs}
    grouped = df.groupby(list(by), sort=False, dropna=True)[cols].agg(funcs)
    out: Dict[tuple, dict] = {}
    for key, row in grouped.iterrows():
        out[tuple(key) if isinstance(key, tuple) else (key,)] = {
            alias: to_python(row[col]) for col, _, alias in specs
        }
    return out


def _wide_frame(dimensions, pivots, row_keys, col_keys, cells) -> pd.DataFrame:
    """Marco ancho de las celdas hoja: una fila por clave de fila completa y una columna por
    celda (o la métrica directa, si no hay pivotes). Es el `data` de respaldo del resultado."""
    records = []
    for key in row_keys:
        if dimensions and len(key) != len(dimensions):
            continue  # solo las filas hoja: los subtotales viven en `nested`
        record = {dim: value for dim, value in zip(dimensions, key)}
        if pivots:
            for col in col_keys:
                if len(col) != len(pivots):
                    continue
                for alias, value in cells[(key, col)].items():
                    record[cell_field(col, alias)] = value
        else:
            record.update(cells[(key, ())])
        records.append(record)
    return pd.DataFrame(records)


class FilterStep(PipelineStep):
    """Paso 1: condiciones sobre las FILAS que entran al widget (dsl/conditions)."""

    name = "filter"

    def execute(self, ctx: PipelineContext) -> PipelineContext:
        filters = ctx.fields.filters or []
        if not filters:
            ctx.metadata["universe"] = ctx.df
            return ctx
        conditions: List[Condition] = parse_conditions(filters)
        ctx.df = apply_filters(ctx.df, conditions)
        ctx.metadata["filters_applied"] = len(conditions)
        # Universo del widget (el denominador de sus porcentajes): filas de la hoja que
        # entraron al widget, sin recortar por los filtros propios de cada métrica.
        ctx.metadata["universe"] = ctx.df
        return ctx


class AggregationOrPivotStep(PipelineStep):
    """
    Paso 2: agregación y/o pivote.
      - con `pivots`  → pivot_table (tabla dinámica / series por valor del pivote)
      - con `dimensions` → groupby + agg (resultado plano)
      - sin ninguno de los dos → escalar (KPI)
    """

    name = "aggregation"

    def execute(self, ctx: PipelineContext) -> PipelineContext:
        fields = ctx.fields
        df = ctx.df
        metrics = fields.metrics or []
        if not metrics:
            return ctx

        dimensions = fields.dimensions or []
        pivots = fields.pivots or []

        if ctx.widget_type == "dynamic_table":
            # La tabla dinámica necesita subtotales por nivel y total general: el frame
            # plano no los trae, así que arma el resultado jerárquico.
            if not dimensions and not pivots:
                return self._scalar(df, metrics, ctx)
            return self._nested(df, fields, dimensions, pivots, metrics, ctx)

        if not dimensions and not pivots:
            return self._scalar(df, metrics, ctx)

        if pivots:
            return self._pivot(df, fields, dimensions, pivots, metrics, ctx)

        return self._grouped(df, fields, dimensions, metrics, ctx)

    # -- escalar (KPI) ---------------------------------------------------
    def _scalar(self, df, metrics, ctx) -> PipelineContext:
        values = {}
        for m in metrics:
            if m.get("type") == "formula":
                continue
            metric_df = _metric_filters(df, m)
            agg_func, field_name, alias = m.get("agg"), _get_field(m), _get_alias(m)
            if field_name:
                values[alias] = _aggregate(metric_df[field_name], agg_func)
            elif agg_func == "count":
                values[alias] = len(metric_df)
            else:
                values[alias] = None
        ctx.metadata["scalar_result"] = {"values": values}
        ctx.metadata["aggregated"] = True
        return ctx

    # -- groupby ---------------------------------------------------------
    def _grouped(self, df, fields, dimensions, metrics, ctx) -> PipelineContext:
        df, specs = _metric_columns(df, metrics)
        if not specs:
            return ctx

        original = {dim: df[dim].dropna().unique().tolist() for dim in dimensions}
        df_agg = df.groupby(dimensions, sort=False).agg({col: agg for col, agg, _ in specs}).reset_index()

        lead = dimensions[0]
        order = original.get(lead, [])
        if order and lead in df_agg.columns:
            values = df_agg[lead]
            df_agg[lead] = pd.Categorical(values, categories=order, ordered=True)
            df_agg = df_agg.sort_values(lead).reset_index(drop=True)
            df_agg[lead] = df_agg[lead].astype(values.dtype)

        df_agg = df_agg.rename(columns={col: alias for col, _, alias in specs if alias and alias != col})

        ctx.df = df_agg
        ctx.metadata["aggregated"] = True
        ctx.metadata["dimensions"] = dimensions
        ctx.metadata["pivots"] = fields.pivots or []
        ctx.metadata["dimension_values"] = df_agg[dimensions[0]].tolist()
        ctx.metadata["flat_result"] = {
            "dimension": dimensions[0],
            "rows": df_agg,
            "totals": {},
            "dimension_values": ctx.metadata["dimension_values"],
        }
        return ctx

    # -- tabla dinámica (filas y columnas con subtotales) ----------------
    def _nested(self, df, fields, dimensions, pivots, metrics, ctx) -> PipelineContext:
        """
        Resultado jerárquico de la tabla dinámica: una celda por (prefijo de filas,
        prefijo de columnas), con el subtotal de cada nivel y el total general, como en
        las tablas dinámicas de una hoja de cálculo.

        - `metadata["nested"]` lleva claves, celdas y filas para `dynamic_table.compile`;
        - `ctx.df` queda como marco ancho de las celdas hoja (el `data` de respaldo).
        """
        df, specs = _metric_columns(df, metrics)
        if not specs:
            return ctx

        row_leaf = _ordered_keys(df, dimensions)
        col_leaf = _ordered_keys(df, pivots)
        row_keys = _hierarchy(row_leaf)
        col_keys = _hierarchy(col_leaf)
        all_rows, all_cols = [(), *row_keys], [(), *col_keys]

        # Celdas (con subtotales y totales) × métricas: un cruce mayor no se puede leer.
        cells = max(len(all_rows), 1) * max(len(all_cols), 1) * len(specs)
        if cells > MAX_PIVOT_CELLS:
            raise ResultTooLargeError(
                f"La tabla {len(all_rows)} × {len(all_cols)} × {len(specs)} métricas genera "
                f"{cells:,} celdas; acota las dimensiones o filtra la hoja."
            )

        # Un groupby por pareja (nivel de fila, nivel de columna): todos los valores de esa
        # pareja salen de una sola agregación, clave a clave.
        tables = {
            (k, j): _grouped_values(df, specs, list(dimensions[:k]) + list(pivots[:j]))
            for k in range(len(dimensions) + 1)
            for j in range(len(pivots) + 1)
        }
        agg_cells: Dict[tuple, dict] = {}
        for row_key in all_rows:
            for col_key in all_cols:
                values = tables[(len(row_key), len(col_key))].get(row_key + col_key)
                agg_cells[(row_key, col_key)] = (
                    values if values is not None
                    else {alias: None for _, _, alias in specs}
                )

        aliases = [a for a in (_get_alias(m) for m in metrics) if a]
        formulas = [m for m in metrics if m.get("type") == "formula" and m.get("expression")]
        windows = [m for m in metrics if (m.get("window") or {}).get("type")]
        if formulas or windows:
            self._cell_metrics(agg_cells, formulas, windows)
            aliases = list(dict.fromkeys(
                [*aliases, *(m.get("alias") for m in formulas if m.get("alias"))]
            ))

        rows = [
            {
                "key": list(key),
                "cells": {col: agg_cells[(key, col)] for col in col_keys},
                "totals": agg_cells[(key, ())],
                "subtotal": len(key) < len(dimensions),
            }
            for key in row_keys
        ]
        ctx.metadata["nested"] = {
            "dimensions": list(dimensions),
            "pivots": list(pivots),
            "metrics": aliases,
            "row_leaf": row_leaf,
            "column_keys": col_keys,
            "cells": agg_cells,
            "rows": rows,
            "grand": {"cells": {col: agg_cells[((), col)] for col in col_keys},
                      "totals": agg_cells[((), ())]},
        }
        ctx.metadata["aggregated"] = True
        ctx.metadata["dimensions"] = dimensions
        ctx.metadata["pivots"] = pivots
        ctx.metadata["metrics"] = metrics
        ctx.metadata["dimension_values"] = [list(k) for k in row_leaf]
        ctx.df = _wide_frame(dimensions, pivots, row_keys, col_keys, agg_cells)
        return ctx

    @staticmethod
    def _cell_metrics(cells: Dict[tuple, dict], formulas: list, windows: list) -> None:
        """Métricas calculadas y ventanas sobre TODAS las celdas (hoja, subtotales y total
        general): una fórmula o un porcentaje se recalcula en cada nivel, no se suma."""
        keys = list(cells)
        frame = pd.DataFrame([cells[key] for key in keys])
        for metric in formulas:
            alias, expression = metric.get("alias"), metric.get("expression")
            if not (alias and expression):
                continue
            try:
                frame[alias] = frame.eval(expression)
            except Exception:  # noqa: BLE001 - una fórmula mala no tumba el widget entero
                frame[alias] = None
        columns = list(frame.columns)
        for metric in windows:
            AggregationOrPivotStep._cell_window(frame, metric, keys, columns)
        for position, key in enumerate(keys):
            cells[key] = {alias: to_python(frame.at[position, alias]) for alias in columns}

    @staticmethod
    def _cell_window(frame: pd.DataFrame, metric: dict, keys: list, columns: list) -> None:
        """Porcentajes sobre las celdas: el denominador es el total de su columna
        (`percent_of_total`) o el de su fila (`percent_of_row`). El resto de ventanas no
        tiene sentido fila a fila en una tabla y se ignoran."""
        alias = metric.get("alias") or metric.get("field")
        w_type = (metric.get("window") or {}).get("type")
        if alias not in columns or w_type not in ("percent_of_total", "percent_of_row"):
            return
        values = {key: frame.at[position, alias] for position, key in enumerate(keys)}

        def _percent(value, total) -> float:
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value != value:
                return 0
            if not isinstance(total, (int, float)) or isinstance(total, bool) or not total:
                return 0
            return round(value / total * 100, 2)

        # Cada celda sobre el total de su columna, o el de su fila.
        for position, key in enumerate(keys):
            row_key, col_key = key
            total = (values[((), col_key)] if w_type == "percent_of_total"
                     else values[(row_key, ())])
            frame.at[position, alias] = _percent(frame.at[position, alias], total)

    # -- pivote ----------------------------------------------------------
    def _pivot(self, df, fields, dimensions, pivots, metrics, ctx) -> PipelineContext:
        pivot_column = pivots[0]
        df, specs = _metric_columns(df, metrics)
        if not specs:
            return ctx

        order = {dim: df[dim].dropna().unique().tolist() for dim in dimensions}
        pivot_values = df[pivot_column].dropna().unique().tolist()

        # pivot_table multiplica filas × valores del pivote × métricas: un fan-out inesperado
        # se corta aquí en vez de mandar un JSON gigante al navegador.
        cells = max(1, len(dimensions or ["_"])) * max(1, len(pivot_values)) * len(specs)
        if cells > MAX_PIVOT_CELLS:
            raise ResultTooLargeError(
                f"El cruce {pivot_column} × {'/'.join(dimensions or ['filas'])} genera "
                f"{cells:,} celdas; acota las dimensiones o filtra la hoja."
            )

        table = df.pivot_table(
            index=dimensions or None,
            columns=pivot_column,
            values=[col for col, _, _ in specs],
            aggfunc={col: agg for col, agg, _ in specs},
            fill_value=0,
            sort=False,
        )
        if isinstance(table.columns, pd.MultiIndex):
            table.columns = [
                f"{value}_{col}" if value else str(col)
                for col, value in table.columns
            ]
        table = table.reset_index()

        # {valor}_{columna intermedia} → {valor}_{alias}
        for col, _, alias in specs:
            if not alias or alias == col:
                continue
            table.columns = [
                c[: -len(col)] + alias if c.endswith(f"_{col}") else c
                for c in table.columns
            ]

        ctx.df = table
        ctx.metadata["pivoted"] = True
        ctx.metadata["pivot_column"] = pivot_column
        ctx.metadata["pivot_values"] = pivot_values
        ctx.metadata["dimensions"] = dimensions
        ctx.metadata["pivots"] = pivots
        ctx.metadata["metrics"] = metrics
        ctx.metadata["dimension_values"] = order.get(dimensions[0], []) if dimensions else []

        if dimensions:
            dim = dimensions[0]
            ctx.metadata["pivot_chart_result"] = {
                "dimension": dim,
                "pivot_values": pivot_values,
                "metric": _get_alias(metrics[0]),
                "dimension_values": order.get(dim, []),
                "rows": {},
            }
        return ctx


class CalculatedMetricsStep(PipelineStep):
    """Paso 3: métricas calculadas sobre las columnas ya agregadas (`type: "formula"`)."""

    name = "calculated"

    def execute(self, ctx: PipelineContext) -> PipelineContext:
        if ctx.metadata.get("nested") is not None:
            # En la tabla dinámica la fórmula ya se calculó celda a celda (con sus subtotales).
            return ctx
        df = ctx.df
        for metric in ctx.fields.metrics or []:
            if metric.get("type") != "formula":
                continue
            alias, expression = metric.get("alias"), metric.get("expression")
            if not (alias and expression):
                continue
            try:
                df[alias] = df.eval(expression)
            except Exception:  # noqa: BLE001 - una fórmula mala no tumba el widget entero
                df[alias] = None
        ctx.df = df
        return ctx


class WindowFunctionsStep(PipelineStep):
    """
    Paso 4: transformaciones sobre ventanas de las métricas, declaradas dentro del propio
    diccionario de la métrica: `{"alias": "pct_total", "field": "monto", "agg": "sum",
    "window": {"type": "percent_of_total"}}`.
    """

    name = "window"

    def execute(self, ctx: PipelineContext) -> PipelineContext:
        if ctx.metadata.get("nested") is not None:
            # En la tabla dinámica los porcentajes ya salen por celda (niveles y totales).
            return ctx
        scalar = ctx.metadata.get("scalar_result")
        if scalar is not None:
            self._scalar_windows(scalar, ctx)
            return ctx

        df = ctx.df
        planned: Dict[str, Any] = {}
        for metric in ctx.fields.metrics or []:
            window = metric.get("window") or {}
            w_type = window.get("type")
            if not w_type:
                continue
            # La ventana trabaja sobre la columna ya agregada (el alias); si no existe, sobre el
            # campo crudo de la hoja.
            target = metric.get("alias") or metric.get("field")
            source = target if target in df.columns else metric.get("field")
            if not source or source not in df.columns:
                continue
            if w_type == "percent_of_total":
                total = df[source].sum()
                planned[target] = (df[source] / total * 100).round(2) if total else 0
            elif w_type == "percent_of_row":
                values = df[source]
                planned[target] = (values / values.sum() * 100).round(2) if values.sum() else 0
            elif w_type == "running_total":
                planned[target] = df[source].cumsum()
            elif w_type == "pct_change":
                planned[target] = (df[source].pct_change() * 100).round(2)

        if planned:
            # Se copia antes de escribir: el frame que llega de un groupby/filtro puede ser
            # una vista y la escritura no llegaría al resultado final.
            df = df.copy()
            for target, values in planned.items():
                df[target] = values
            ctx.df = df
            ctx.metadata["window_applied"] = True
        return ctx

    def _scalar_windows(self, scalar: dict, ctx: PipelineContext) -> None:
        """Ventanas sobre un KPI (sin dimensiones): el denominador es el universo del widget,
        es decir, sus filas sin el recorte de los filtros propios de la métrica."""
        values = scalar.get("values") or {}
        universe = ctx.metadata.get("universe")
        if universe is None:
            universe = ctx.df
        applied = False
        for metric in ctx.fields.metrics or []:
            if (metric.get("window") or {}).get("type") != "percent_of_total":
                continue
            alias, field_name = metric.get("alias") or metric.get("field"), _get_field(metric)
            if not field_name or alias not in values or field_name not in universe.columns:
                continue
            total = _aggregate(universe[field_name], metric.get("agg"))
            value = values[alias]
            values[alias] = round(value / total * 100, 2) if total else 0
            applied = True
        if applied:
            ctx.metadata["scalar_result"] = values
            ctx.metadata["window_applied"] = True


class SortLimitStep(PipelineStep):
    """Paso final: orden (`sort_by`, con «-» delante para descendente) y `limit`."""

    name = "sort_limit"

    def execute(self, ctx: PipelineContext) -> PipelineContext:
        fields, df = ctx.fields, ctx.df

        nested = ctx.metadata.get("nested")
        if nested is not None:
            # La tabla dinámica se ordena por niveles: cada grupo se recorre entero y su
            # subtotal cierra el grupo, sin mezclar filas de distinto nivel.
            self._sort_nested(nested, fields)
            if fields.limit:
                nested["rows"] = nested["rows"][:fields.limit]
                df = df.head(fields.limit)
            ctx.df = df
            return ctx

        if fields.sort_by:
            descending = fields.sort_by.startswith("-")
            column = fields.sort_by.lstrip("-")
            if column in df.columns:
                df = df.sort_values(column, ascending=not descending)

        if fields.limit:
            df = df.head(fields.limit)

        ctx.df = df
        return ctx

    @staticmethod
    def _sort_nested(nested: Dict[str, Any], fields) -> None:
        """Ordena los hermanos de cada nivel por la métrica o la dimensión pedida
        (`sort_by`, con «-» delante para descendente); el subtotal cierra a su grupo."""
        sort_by = fields.sort_by or ""
        if not sort_by:
            return
        descending = sort_by.startswith("-")
        name = sort_by.lstrip("-")
        dimensions = nested.get("dimensions") or []
        totals = {tuple(r["key"]): r.get("totals") or {} for r in nested["rows"]}

        def order_key(key: tuple):
            if name in dimensions:
                index = dimensions.index(name)
                raw = key[index] if index < len(key) else None
            else:
                raw = totals.get(key, {}).get(name)
            if raw is None or isinstance(raw, bool):
                return (2, "")
            if isinstance(raw, (int, float)):
                return (1, float(raw))
            return (0, str(raw))

        row_keys = _hierarchy(nested.get("row_leaf") or [], lambda _level, siblings:
                              sorted(siblings, key=order_key, reverse=descending))
        by_key = {tuple(r["key"]): r for r in nested["rows"]}
        nested["rows"] = [by_key[key] for key in row_keys if key in by_key]


class PipelineExecutor:
    """Ejecuta los pasos del pipeline en secuencia y compila el resultado final."""

    def __init__(self, steps: Optional[List[str]] = None):
        self.step_names = list(steps or DEFAULT_STEPS)
        self._steps = {
            "filter": FilterStep(),
            "aggregation": AggregationOrPivotStep(),
            "calculated": CalculatedMetricsStep(),
            "window": WindowFunctionsStep(),
            "sort_limit": SortLimitStep(),
        }

    def execute(self, df: pd.DataFrame, fields: "WidgetFields",
                widget_type: Optional[str] = None) -> Dict[str, Any]:
        """Ejecuta el pipeline completo y devuelve `{data, type, metadata}`."""
        ctx = PipelineContext(df=df, fields=fields, widget_type=widget_type)
        for name in self.step_names:
            step = self._steps.get(name)
            if step:
                ctx = step(ctx)
        return self._compile_result(ctx)

    # ------------------------------------------------------------------ salida
    def _compile_result(self, ctx: PipelineContext) -> Dict[str, Any]:
        """Convierte el estado final del pipeline en la data plana del frontend."""
        metadata = ctx.metadata
        base_meta = {
            "fields": ctx.fields,
            "dimensions": metadata.get("dimensions", []),
            "pivots": metadata.get("pivots", []),
            "metrics": ctx.fields.metrics or [],
            "pivot_column": metadata.get("pivot_column"),
            "pivot_values": metadata.get("pivot_values", []),
            "dimension_values": metadata.get("dimension_values", []),
            "totals": metadata.get("totals", {}),
            "row_totals": metadata.get("row_totals", []),
            "column_totals": metadata.get("column_totals", []),
            # Tabla dinámica: resultado jerárquico con subtotales y total general.
            "nested": metadata.get("nested"),
        }

        if "scalar_result" in metadata:
            return {"data": metadata["scalar_result"], "type": "scalar", "metadata": base_meta}

        if "flat_result" in metadata:
            flat = metadata["flat_result"]
            # ctx.df (no el snapshot de la agregación) es el que ya pasó por orden y límite.
            dimension = flat["dimension"]
            values = ctx.df[dimension].tolist() if dimension in ctx.df.columns else flat["dimension_values"]
            return {
                "data": ctx.df.to_dict(orient="records"),
                "type": "flat",
                "dimension": dimension,
                "dimension_values": values,
                "totals": flat["totals"],
                "metadata": base_meta,
            }

        if "pivot_chart_result" in metadata:
            chart = metadata["pivot_chart_result"]
            return {
                "data": ctx.df.to_dict(orient="records"),
                "type": "pivot_chart",
                "dimension": chart["dimension"],
                "pivot_values": chart["pivot_values"],
                "metrics": chart["metric"],
                "metadata": base_meta,
            }

        if metadata.get("pivoted"):
            return {
                "data": ctx.df.to_dict(orient="records"),
                "type": "pivot_table",
                "dimensions": metadata.get("dimensions", []),
                "pivots": metadata.get("pivots", []),
                "metadata": base_meta,
            }

        return {
            "data": ctx.df.to_dict(orient="records"),
            "type": "flat" if metadata.get("aggregated") else "rows",
            "columns": list(ctx.df.columns),
            "metadata": base_meta,
        }
