"""
El lenguaje de los widgets (DSL): piezas reutilizables, sin conocer ningún widget.

- conditions:   FilterOperator, RelativeValue y Condition (filas que entran)
- aggregations: Aggregation (sum, avg, count, ...)
- calc_ops:     CalcOp (add, div, ratio_pct, ...)
- groups:       GroupCondition (condiciones sobre grupos)
- metrics:      Metric y sus tipos (agg, calc, grouped)
- spec:         DataSpec (la consulta completa) y su JSON Schema
- rules:        reglas semánticas genéricas
"""
