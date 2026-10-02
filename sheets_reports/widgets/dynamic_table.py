from sheets_reports.dsl.parts import Dimensions, Metrics, Pivots
from sheets_reports.dsl.schema import MAX_DIMENSIONS, MAX_PIVOTS
from sheets_reports.dsl.spec import DataSpec, GroupedSpec, with_parts
from sheets_reports.engine.plans import PivotTableResult
from sheets_reports.widgets.base import WIDGETS, ViewOptions, WidgetType, percent_metrics
from sheets_reports.widgets.presentation import TOTAL_LABEL, cell_field, total_field


def _pivot_columns(result: PivotTableResult, options: ViewOptions) -> list[dict]:
    """
    Columnas de los pivotes, anidadas como en Sheets: cada valor del primer pivote agrupa
    los del segundo, seguido de su columna "Total <valor>" (subtotal). Con una métrica, todo
    va bajo un grupo con el nombre de la métrica; con varias, cada columna hoja se abre en
    una subcolumna por métrica. Al final, la columna "Total general".
    """
    metrics, depth = result.metrics, len(result.pivots)
    single = len(metrics) == 1

    def node(header, col_key, **flags):
        if single:
            return {"header": header, "field": cell_field(col_key, metrics[0]), **flags}
        return {"header": header, **flags, "children": [
            {"header": options.label(m), "field": cell_field(col_key, m)} for m in metrics
        ]}

    top: list[dict] = []
    containers = {(): top}
    for key in result.column_keys:
        parent = key[:-1]
        if parent not in containers:
            group = {"header": str(parent[-1]), "children": []}
            containers[parent[:-1]].append(group)
            containers[parent] = group["children"]
        if len(key) == depth:
            containers[parent].append(node(str(key[-1]), key))
        else:
            containers[parent].append(node(f"Total {key[-1]}", key, subtotal=True))

    if single:
        metric = metrics[0]
        return [
            {"header": options.label(metric), "children": top},
            {"header": TOTAL_LABEL, "field": total_field(metric), "total": True},
        ]
    return [*top, {"header": TOTAL_LABEL, "total": True, "children": [
        {"header": options.label(m), "field": total_field(m)} for m in metrics
    ]}]


class DynamicTableSpec(GroupedSpec):
    """Filas y columnas anidadas, y varias métricas a la vez que un pivote."""
    parts = with_parts(GroupedSpec, Dimensions(1, MAX_DIMENSIONS), Pivots(0, MAX_PIVOTS),
                       Metrics(multi_with_pivot=True))


@WIDGETS.register
class DynamicTableWidget(WidgetType[PivotTableResult, ViewOptions]):
    """Tabla dinámica: siempre agrupa por al menos una fila (hasta 3 anidadas) y opcionalmente
    por columnas (hasta 2), con subtotales; varias métricas aun con pivote (una subcolumna por
    métrica en cada valor)."""
    key = "dynamic_table"
    label = "Tabla dinámica"
    spec_cls = DynamicTableSpec
    plan_key = "pivot_table"
    ai_doc = ('tabla dinámica que agrupa (resúmenes por fila/columna, varias métricas por fila, '
              '"tabla de ventas por ..."). Las filas (dimensions) van de la más general a la más '
              'detallada (ej. ["sede", "carrera"]); las columnas (pivots) igual (ej. ["anio", "mes"]).')
    ai_examples = (
        ("Tabla de vendedores que no llegaron a su plan, con ventas, plan y % de cumplimiento", {
            "widget_type": "dynamic_table", "title": "Vendedores bajo el plan",
            "data_spec": {"dimensions": ["vendedor"], "pivots": [], "columns": [], "filters": [], "limit": None,
                          "trend_by": None,
                          "metrics": [{"type": "agg", "as": "ventas", "agg": "sum", "field": "ventas"},
                                      {"type": "agg", "as": "plan", "agg": "sum", "field": "plan"},
                                      {"type": "calc", "as": "cumplimiento", "op": "ratio_pct",
                                       "left": "ventas", "right": "plan"}],
                          "having": [{"left": "ventas", "op": "lt", "right": "plan"}],
                          "sort": {"by": "cumplimiento", "dir": "asc"}},
            "view_options": {"labels": [{"name": "cumplimiento", "label": "% cumplimiento"}]},
        }),
        ("Tabla de respuestas por categoría con la cantidad y el porcentaje de cada respuesta", {
            "widget_type": "dynamic_table", "title": "Respuestas por categoría",
            "data_spec": {"dimensions": ["categoria"], "pivots": ["respuesta"], "columns": [], "filters": [],
                          "having": [], "sort": None, "limit": None, "trend_by": None,
                          "metrics": [{"type": "agg", "as": "cantidad", "agg": "count"},
                                      {"type": "agg", "as": "pct_cantidad", "agg": "count", "show_as": "pct_row"}]},
            "view_options": {"labels": [{"name": "cantidad", "label": "Cant."}, {"name": "pct_cantidad", "label": "%"}]},
        }),
    )

    def data_view(self, spec: DataSpec) -> dict:
        return {}

    def compile(self, result: PivotTableResult, options, spec):
        """
        - una columna por cada fila de la dimensión (`rowFields`), luego las métricas (sin
          pivote) o las columnas anidadas de los pivotes (_pivot_columns);
        - las filas de subtotal llevan "__subtotal": True y la etiqueta "Total <valor>";
        - la fila de totales va aparte en `totals` (el frontend la muestra como fila de pie);
        - `percent`: campos que son porcentajes, para que la tabla les ponga el formato %.
        """
        dimensions, pivots, metrics = result.dimensions, result.pivots, result.metrics
        columns = [{"header": options.label(d), "field": d} for d in dimensions]
        if pivots:
            columns += _pivot_columns(result, options)
        else:
            columns += [{"header": options.label(m), "field": m} for m in metrics]

        def value_fields(cells, totals):
            record = {}
            for col_key, values in cells.items():
                for m, v in values.items():
                    record[cell_field(col_key, m)] = v
            for m, v in totals.items():
                record[total_field(m) if pivots else m] = v
            return record

        rows = []
        for row in result.rows:
            key = row.key
            record = {}
            for i, d in enumerate(dimensions):
                if i >= len(key):
                    record[d] = None
                elif i == len(key) - 1 and row.subtotal:
                    record[d] = f"Total {key[i]}"
                else:
                    record[d] = key[i]
            if row.subtotal:
                record["__subtotal"] = True
            record.update(value_fields(row.cells, row.totals))
            rows.append(record)

        compiled = {
            "columns": columns,
            "rows": rows,
            "rowFields": dimensions,
            "totals": {dimensions[0]: TOTAL_LABEL, **value_fields(result.grand.cells, result.grand.totals)},
        }
        percent = [m for m in metrics if m in percent_metrics(spec)]
        if pivots:
            percent_fields = [
                field for m in percent
                for field in [*(cell_field(k, m) for k in result.column_keys), total_field(m)]
            ]
        else:
            percent_fields = percent
        if percent_fields:
            compiled["percent"] = percent_fields
        return compiled
