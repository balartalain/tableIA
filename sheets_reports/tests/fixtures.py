import pandas as pd

from sheets_reports.services.sheets import get_sheet_schema


def sales_df() -> pd.DataFrame:
    return pd.DataFrame({
        "categoria": ["Hogar", "Electrónica", "Hogar", "Ropa", "Electrónica", "Hogar"],
        "mes": ["Ene", "Ene", "Feb", "Feb", "Feb", "Mar"],
        "anio": [2026, 2026, 2026, 2025, 2026, 2026],
        "ventas": [100.0, 300.0, 50.0, 80.0, 200.0, 25.0],
    })


def sales_schema() -> dict:
    return get_sheet_schema(sales_df())


def spec(**overrides) -> dict:
    base = {
        "source": "0",
        "dimensions": ["categoria"],
        "pivot": None,
        "metrics": [{"field": "ventas", "agg": "sum", "as": "total_ventas"}],
        "filters": [],
        "sort": None,
    }
    return {**base, **overrides}
