"""Fixtures de la suite: la hoja de ventas de prueba y helpers para armar `WidgetForm`."""
import pandas as pd

from sheets_reports.engine import run_steps
from sheets_reports.engine.context import SheetContext
from sheets_reports.services.ai_spec import form_errors
from sheets_reports.widgets import WIDGETS
from sheets_reports.widgets.schemas import WidgetFields, WidgetForm


def sales_df() -> pd.DataFrame:
    return pd.DataFrame({
        "categoria": ["Hogar", "Electrónica", "Hogar", "Ropa", "Electrónica", "Hogar"],
        "mes": ["Ene", "Ene", "Feb", "Feb", "Feb", "Mar"],
        "anio": [2026, 2026, 2026, 2025, 2026, 2026],
        "ventas": [100.0, 300.0, 50.0, 80.0, 200.0, 25.0],
    })


def sales_int_df() -> pd.DataFrame:
    """`sales_df` con ventas enteras (int64), como llegan de una hoja sin decimales: las sumas
    son enteros de numpy (np.int64), no `int` de Python."""
    df = sales_df()
    df["ventas"] = df["ventas"].astype("int64")
    return df


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


def examples_ctx() -> SheetContext:
    """La hoja con todas las columnas que usan los ejemplos del prompt de la IA."""
    df = pd.DataFrame({
        "producto": ["A"], "vendedor": ["Ana"], "categoria": ["Hogar"], "mes": ["Ene"],
        "respuesta": ["Sí"], "anio": [2026], "ventas": [1.0], "costo": [1.0], "plan": [1.0],
    })
    return SheetContext.from_dataframe(df, "0")


def agg(alias: str = "total_ventas", agg: str = "sum", field: str | None = "ventas", **extra) -> dict:
    """Métrica agregada. `count` no lleva campo."""
    metric = {"field": field, "agg": agg, "alias": alias, **extra}
    if agg == "count" or field is None:
        metric.pop("field", None)
    return metric


def calc(alias: str, expression: str, field: str = "ventas") -> dict:
    """Métrica calculada sobre columnas ya agregadas (`type: formula`)."""
    return {"type": "formula", "alias": alias, "expression": expression, "field": field}


def fields(**overrides) -> dict:
    """`WidgetFields` plano con los defaults de siempre."""
    base = {
        "dimensions": ["categoria"],
        "pivots": [],
        "metrics": [agg()],
        "filters": [],
        "sort_by": None,
        "limit": None,
    }
    return {**base, **overrides}


def table_fields(columns=("categoria", "ventas"), **overrides) -> dict:
    """`WidgetFields` de la Tabla: solo columnas a mostrar, en orden (no agrupa ni resume)."""
    base = {
        "dimensions": [],
        "pivots": [],
        "metrics": [],
        "filters": [],
        "columns": [c if isinstance(c, dict) else {"field": c} for c in columns],
        "sort_by": None,
        "limit": None,
    }
    return {**base, **overrides}


def form(fields_data: dict | None = None, style: dict | None = None) -> WidgetForm:
    return WidgetForm.from_dict({"fields": fields_data or fields(), "style": style or {}})


def render(widget_type: str, fields_data: dict | None = None, style: dict | None = None,
           df: pd.DataFrame | None = None) -> dict:
    """`BaseWidget.render` completo: `render_data` + `widget_form`, o `error`."""
    return WIDGETS.get(widget_type).render(
        sales_df() if df is None else df,
        {"fields": fields_data or fields(), "style": style or {}},
    )


def compiled(widget_type: str, fields_data: dict | None = None, style: dict | None = None,
             df: pd.DataFrame | None = None) -> dict:
    """Solo el JSON que dibuja el frontend; falla si el form produjo error."""
    out = render(widget_type, fields_data, style, df)
    assert "error" not in out, out["error"]
    return out["render_data"]


def execute(df: pd.DataFrame, fields_data: dict):
    """Los pasos del motor, sin pasar por un widget."""
    return run_steps(df, WidgetFields.from_dict(fields_data))


def errors_for(widget_type: str, fields_data: dict, style: dict | None = None,
               ctx: SheetContext | None = None, title: str = "Ventas") -> list[str]:
    """Validación de un `WidgetForm` contra la hoja y las capacidades del tipo."""
    return form_errors(
        {"widget_type": widget_type, "title": title, "fields": fields_data, "style": style or {}},
        ctx or sales_ctx(),
        widget_type,
    )


def make_board(owner, nombre="Ventas", sheet_id="abc", gid="0", **source_fields):
    """Un tablero con una fuente de datos (la hoja `sheet_id`/`gid`): (dashboard, source)."""
    from sheets_reports.models import Dashboard, DataSource

    dashboard = Dashboard.objects.create(nombre=nombre, owner=owner)
    source = DataSource.objects.create(dashboard=dashboard, sheet_id=sheet_id, gid=gid, **source_fields)
    return dashboard, source
