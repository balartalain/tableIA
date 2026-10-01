from sheets_reports.dsl.schema import MAX_COLUMNS
from sheets_reports.dsl.spec import DataSpec
from sheets_reports.dsl.values import to_python
from sheets_reports.engine.plans.rows import RowsResult
from sheets_reports.widgets.base import WIDGETS, DataCapabilities, ViewOptions, WidgetType


@WIDGETS.register
class TableWidget(WidgetType[RowsResult, ViewOptions]):
    """Tabla de datos: las filas de la hoja tal cual, con las columnas elegidas. No agrupa ni
    lleva métricas; admite filtros y orden por una columna."""
    key = "table"
    label = "Tabla"
    capabilities = DataCapabilities(
        dimensions=(0, 0), pivots=(0, 0), columns=(1, MAX_COLUMNS), metrics=(0, 0),
        metric_types=frozenset(), having=False, limit=False,
    )
    plan_key = "rows"

    def data_view(self, spec: DataSpec) -> dict:
        return {"columns": list(spec.columns)}

    def default_title(self, spec: DataSpec, options: ViewOptions) -> str:
        return "Datos"

    def compile(self, result: RowsResult, options, spec):
        """
        {"columns": [{"header", "field", "numeric"}], "rows": [{columna: valor}],
         "total_rows": n, "truncated"?: True}
        `numeric` marca las columnas numéricas (el frontend las alinea y formatea como número).
        La cabecera es el nombre de la columna tal cual en la hoja, salvo que tenga etiqueta.
        """
        numeric = {c for c in result.columns if result.rows[c].dtype.kind in "iuf"}
        compiled = {
            "columns": [{"header": options.labels.get(c) or str(c), "field": c, "numeric": c in numeric}
                        for c in result.columns],
            "rows": [{k: to_python(v) for k, v in row.items()}
                     for row in result.rows.to_dict(orient="records")],
            "total_rows": result.total_rows,
        }
        if result.truncated:
            compiled["truncated"] = True
        return compiled
