"""Paso 4 del motor: orden (`sort_by`, con «-» delante para descendente) y `limit`."""
from __future__ import annotations

from typing import Any, Dict

import pandas as pd

from sheets_reports.engine.steps.aggregation import hierarchy
from sheets_reports.utils.data import is_number


def apply_sort_limit(df: pd.DataFrame, sort_by: str | None, limit: int | None,
                     metadata: dict) -> pd.DataFrame:
    nested = metadata.get("nested")
    if nested is not None:
        # La tabla dinámica se ordena por niveles: cada grupo se recorre entero y su
        # subtotal cierra el grupo, sin mezclar filas de distinto nivel.
        _sort_nested(nested, sort_by)
        if limit:
            nested["rows"] = nested["rows"][:limit]
            df = df.head(limit)
        return df

    if sort_by:
        column = sort_by.lstrip("-")
        if column in df.columns:
            df = df.sort_values(column, ascending=not sort_by.startswith("-"))
    if limit:
        df = df.head(limit)
    return df


def _sort_nested(nested: Dict[str, Any], sort_by: str | None) -> None:
    """Ordena los hermanos de cada nivel por la métrica o la dimensión pedida; el subtotal
    cierra a su grupo."""
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
        if is_number(raw):
            return (1, float(raw))
        return (0, str(raw))

    row_keys = hierarchy(nested.get("row_leaf") or [], lambda _level, siblings:
                         sorted(siblings, key=order_key, reverse=descending))
    by_key = {tuple(r["key"]): r for r in nested["rows"]}
    nested["rows"] = [by_key[key] for key in row_keys if key in by_key]
