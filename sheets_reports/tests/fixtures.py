import pandas as pd

from sheets_reports.services.sheets import get_sheet_schema


def sales_df() -> pd.DataFrame:
    return pd.DataFrame({
        "categoria": ["Hogar", "Electrónica", "Hogar", "Ropa", "Electrónica", "Hogar"],
        "mes": ["Ene", "Ene", "Feb", "Feb", "Feb", "Mar"],
        "anio": [2026, 2026, 2026, 2025, 2026, 2026],
        "ventas": [100.0, 300.0, 50.0, 80.0, 200.0, 25.0],
    })


def sellers_df() -> pd.DataFrame:
    """Ventas y plan por vendedor: Ana y Luis no llegan al plan, Eva sí."""
    return pd.DataFrame({
        "vendedor": ["Ana", "Ana", "Luis", "Eva", "Eva", "Luis"],
        "categoria": ["Hogar", "Ropa", "Hogar", "Ropa", "Hogar", "Ropa"],
        "anio": [2025, 2026, 2026, 2026, 2025, 2025],
        "ventas": [100.0, 50.0, 300.0, 400.0, 200.0, 20.0],
        "plan": [120.0, 100.0, 300.0, 300.0, 100.0, 100.0],
    })


def sales_schema() -> dict:
    return get_sheet_schema(sales_df())


def agg(name: str, agg: str = "sum", field: str | None = "ventas", **extra) -> dict:
    """Métrica agg; count no lleva field."""
    metric = {"type": "agg", "as": name, "agg": agg, **extra}
    if agg != "count" and field:
        metric["field"] = field
    return metric


def spec(**overrides) -> dict:
    base = {
        "source": "0",
        "dimensions": ["categoria"],
        "pivots": [],
        "filters": [],
        "metrics": [agg("total_ventas")],
        "having": [],
        "sort": None,
        "limit": None,
        "trend_by": None,
    }
    return {**base, **overrides}
