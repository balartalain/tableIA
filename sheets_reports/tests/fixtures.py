import pandas as pd

from sheets_reports.dsl.context import SheetContext
from sheets_reports.dsl.spec import DataSpec
from sheets_reports.engine import PLANS, run
from sheets_reports.widgets import WIDGETS


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


def sales_ctx() -> SheetContext:
    return SheetContext.from_dataframe(sales_df(), "0")


def agg(name: str, agg: str = "sum", field: str | None = "ventas", **extra) -> dict:
    """Métrica agg; count no lleva field."""
    metric = {"type": "agg", "as": name, "agg": agg, **extra}
    if agg != "count" and field:
        metric["field"] = field
    return metric


def calc(name: str, op: str, left: str, right) -> dict:
    return {"type": "calc", "as": name, "op": op, "left": left, "right": right}


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


def errors_for(widget_type: str, data_spec: dict, ctx: SheetContext | None = None) -> list[str]:
    return WIDGETS.get(widget_type).errors(data_spec, ctx or sales_ctx())


def view(widget_type: str, data_spec: dict, options: dict | None = None) -> dict:
    """view_spec de un widget, como lo construye el builder."""
    definition = WIDGETS.get(widget_type)
    return definition.build_view(DataSpec.from_dict(data_spec), definition.options(options))


def compiled(widget_type: str, data_spec: dict, options: dict | None = None, df=None) -> dict:
    """Lo que recibe el frontend para dibujar el widget."""
    df = sales_df() if df is None else df
    return WIDGETS.get(widget_type).render(data_spec, view(widget_type, data_spec, options), df)


def execute(df: pd.DataFrame, data_spec: dict, plan: str | None = None):
    """Resultado tipado del plan (por defecto, el que corresponde a la forma del spec)."""
    if plan is None:
        plan = ("scalar" if not data_spec["dimensions"]
                else "pivot_chart" if data_spec["pivots"] else "flat")
    return run(DataSpec.from_dict(data_spec), df, PLANS.get(plan))
