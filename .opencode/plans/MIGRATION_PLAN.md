# Plan de Migración: Arquitectura de Widgets Simplificada

## Resumen Ejecutivo

Migrar desde la arquitectura DSL compleja (`DataSpec`, `SpecPart`, `ViewOptions`, `ViewSpec`, `SpecPart`, `Metrics`, `Dimensions`, etc.) hacia un sistema simplificado basado en **dataclasses planas** y **pipeline de ejecución secuencial**.

**Objetivo:** Eliminar indirecciones, reducir acoplamiento, simplificar el frontend, y hacer el backend 100% enfocado en ejecución de datos.

---

## Estado Actual (Resumen)

### Backend (Eliminar)
| Componente | Archivos | Estado |
|------------|----------|--------|
| `DataSpec`, `SpecPart`, `ViewOptions` | `widgets/base.py`, `dsl/spec.py`, `dsl/parts/*.py` | **Eliminar** |
| `Metrics`, `Dimensions`, `Dimensions`, `Pivots`, `Filters` | `dsl/parts/*.py`, `dsl/metrics/*.py` | **Eliminar** |
| `ViewOptions`, `ViewSpec`, `ViewOptions` | `widgets/base.py` | **Eliminar** |
| `SpecPart`, `SPEC_PARTS` | `dsl/parts/*.py` | **Eliminar** |
| `dsl/rules.py` (validación UI) | `dsl/rules.py` | **Eliminar** |
| Planes de ejecución (`engine/plans/*.py`) | `engine/plans/*.py` | **Reemplazar por Pipeline** |
| `engine/executor.py` | `engine/executor.py` | **Reemplazar por PipelineExecutor** |
| `dsl/parts/*.py` | 10+ archivos | **Eliminar** |
| `dsl/metrics/*.py` | 4 archivos | **Eliminar** |

### Backend (Conservar/Adaptar)
| Componente | Acción |
|------------|--------|
| `dsl/conditions.py` | **Conservar** como motor de filtrado técnico |
| `dsl/context.py` | Adaptar para `WidgetFields` |
| `dsl/context.py` | Adaptar para `SheetContext` |
| `services/widget_service.py` | Reescribir para nuevos modelos |
| `services/ai_spec.py` | Adaptar para generar `WidgetForm` |
| `services/sheets.py` | Mantener (caché de hojas) |

### Frontend (Reescribir)
| Componente | Acción |
|------------|--------|
| `dashboard-store.js` | Reescribir para `WidgetForm` + `style_schema` |
| `board-editor.html` | Editor genérico con `style_schema` |
| `panel.html` + partials | Eliminar (reemplazado por editor genérico) |
| `widget-registry.js` | Mantener + añadir `style_schema` |
| `panel.js` | Eliminar (reemplazado por renderizado genérico) |

---

## Nueva Arquitectura (Target)

### 1. Modelos de Datos (Dataclasses Planas)

```python
# widgets/schemas.py
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

@dataclass
class WidgetFields:
    """Parámetros de consulta (data layer)."""
    dimensions: List[str] = field(default_factory=list)
    metrics: List[Dict[str, Any]] = field(default_factory=list)  # [{"field": "monto", "agg": "sum", "alias": "total"}]
    filters: List[Dict[str, Any]] = field(default_factory=list)
    pivots: List[str] = field(default_factory=list)
    sort_by: Optional[str] = None
    limit: Optional[int] = None
    window: Optional[Dict[str, Any]] = None  # {"type": "percent_of_total"}

@dataclass
class WidgetStyle:
    """Configuración visual (presentation layer)."""
    values: Dict[str, Any] = field(default_factory=dict)  # {"title": "...", "stacked": true}

@dataclass
class WidgetForm:
    """Formulario completo del widget."""
    fields: WidgetFields
    style: WidgetStyle
```

### 2. WidgetType Simplificado (BaseWidget)

```python
# widgets/base.py
class BaseWidget:
    type_key: str
    style_schema: List[Dict[str, Any]] = []  # Backend-driven style schema
    
    def process_query(self, df, fields: WidgetFields) -> pd.DataFrame:
        raise NotImplementedError
    
    def render(self, df, form: WidgetForm) -> dict:
        data = self.process_query(df, form.fields)
        return {"data": data, "form": form.to_dict()}
```

### 3. Pipeline de Ejecución (PipelineExecutor)

```
Widget.process_query(df, fields)
   └── PipelineExecutor.execute(df, fields)
          ├── 1. FilterStep (dsl/conditions)
          ├── 2. CalculatedMetricsStep
          ├── 3. AggregationOrPivotStep
          └── 4. WindowFunctionsStep
```

---

## Plan de Implementación por Fases

### FASE 1: Fundación (Semana 1-2)
**Objetivo:** Nueva infraestructura de datos y modelos base

| Tarea | Archivos | Descripción |
|-------|----------|-------------|
| 1.1 | `widgets/schemas.py` | Crear `WidgetFields`, `WidgetStyle`, `WidgetForm` |
| 1.2 | `widgets/base.py` | Nuevo `BaseWidget` con `style_schema`, `process_query()` |
| 1.3 | `engine/pipeline.py` | Nuevo `PipelineExecutor` con pasos secuenciales |
| 1.4 | `engine/steps/` | Crear `filter.py`, `aggregation.py`, `pivot.py`, `window.py`, `calculated.py` |
| 1.5 | `dsl/context.py` | Adaptar `SheetContext` para `WidgetFields` |

### FASE 2: Migración de Widgets (Semana 2-3)
**Objetivo:** Migrar cada tipo de widget al nuevo sistema

| Widget | Archivo | `style_schema` | Complejidad |
|--------|---------|----------------|-------------|
| `bar_chart` | `widgets/bar.py` | horizontal, stacked, color_scheme, yAxisWidth, barWidth | Media |
| `line_chart` | `widgets/line.py` | reference_lines, color_scheme | Baja |
| `donut_chart` | `widgets/donut.py` | labelMode (percent/value) | Baja |
| `kpi` | `widgets/kpi.py` | decimals, prefix, suffix, abbreviate, roles (primary/compare/target) | Alta |
| `dynamic_table` | `widgets/dynamic_table.py` | pageSize, showPagination, boldLastRow | Media |
| `table` | `widgets/table.py` | pageSize, showPagination, formatters | Media |
| `filter` | `widgets/filter.py` | (solo title) | Baja |

### FASE 3: Servicios y API (Semana 3)
**Objetivo:** Adaptar servicios y endpoints

| Tarea | Archivos | Descripción |
|-------|----------|-------------|
| 3.1 | `services/widget_service.py` | Reescribir para `WidgetFields`/`WidgetStyle` |
| 3.2 | `views.py` | Endpoints `/config/`, `/schema/`, `/render/` |
| 3.3 | `services/ai_spec.py` | Generar `WidgetForm` en lugar de `view_spec` |
| 3.4 | `models.py` | Migración BD: `data_spec` → `fields` + `style` |

### FASE 4: Frontend Genérico (Semana 3-4)
**Objetivo:** Editor genérico con `style_schema`

| Tarea | Archivos | Descripción |
|-------|----------|-------------|
| 4.1 | `board_editor.html` | Reemplazar panel por editor genérico |
| 4.2 | `dashboard-store.js` | Eliminar `drawerFields`, usar `styleSchema` |
| 4.3 | `widget-registry.js` | Añadir `style_schema` al registro |
| 4.4 | `base-widget.js` | Añadir `static get styleSchema()` |
| 4.5 | Eliminar | `panel.html`, `panel.js`, `partials/panel/*.html` |

### FASE 5: Pipeline de Ejecución (Semana 4)
**Objetivo:** Implementar pasos del pipeline

| Paso | Archivo | Descripción |
|------|---------|-------------|
| 5.1 | `engine/steps/filter.py` | `apply_filters(df, fields.filters)` |
| 5.2 | `engine/steps/calculated.py` | Métricas calculadas (formula, window) |
| 5.3 | `engine/steps/aggregation.py` | GroupBy + agg + pivot |
| 5.4 | `engine/steps/window.py` | Window functions (pct_total, rolling, etc.) |
| 5.5 | `engine/pipeline.py` | Orquestador secuencial |

### FASE 6: Limpieza y Migración (Semana 5)
**Objetivo:** Eliminar código obsoleto y migrar datos

| Tarea | Descripción |
|-------|-------------|
| 5.1 | Eliminar `dsl/parts/*.py`, `dsl/metrics/*.py`, `dsl/rules.py` |
| 5.2 | Eliminar `engine/plans/*.py`, `engine/executor.py` |
| 5.3 | Eliminar `dsl/parts/*.py`, `dsl/spec.py`, `dsl/parts/__init__.py` |
| 5.3 | Script migración BD: `view_spec` → `fields` + `style` |
| 5.4 | Eliminar `panel.html`, `panel.js`, `partials/panel/*.html` |
| 5.5 | Tests de integración completos |

---

## Mapeo de Datos: Actual → Nuevo

### `view_spec` → `style`
```json
// Actual
{
  "widget": "bar",
  "x": "Año",
  "metrics": ["total_ventas"],
  "percent": [],
  "title": "Ventas por Año",
  "labels": {},
  "display": {"horizontal": true, "stacked": false}
}

// Nuevo
{
  "style": {
    "title": "Ventas por Año",
    "horizontal": true,
    "stacked": false
  }
}
```

### `data_spec` → `fields`
```json
// Actual
{
  "source": "0",
  "dimensions": ["Año"],
  "metrics": [{"type": "agg", "as": "total_ventas", "agg": "sum", "field": "Ventas"}],
  "filters": [],
  "having": [],
  "sort": null,
  "limit": null
}

// Nuevo
{
  "fields": {
    "dimensions": ["Año"],
    "metrics": [{"field": "Ventas", "agg": "sum", "alias": "total_ventas"}],
    "filters": []
  }
}
```

---

## Esquema `style_schema` por Widget

```python
# Ejemplos de style_schema por tipo

BAR_CHART = [
    {"key": "title", "label": "Título", "ui": "text"},
    {"key": "horizontal", "label": "Horizontal", "ui": "checkbox"},
    {"key": "stacked", "label": "Apilado", "ui": "checkbox"},
    {"key": "color_scheme", "label": "Paleta", "ui": "select", "options": [...]},
    {"key": "reference_lines", "label": "Líneas de referencia", "ui": "reference_lines"},
]

KPI = [
    {"key": "title", "label": "Título", "ui": "text"},
    {"key": "decimals", "label": "Decimales", "ui": "number", "min": 0},
    {"key": "abbreviate", "label": "Abreviar (1.2M)", "ui": "checkbox"},
    {"key": "prefix", "label": "Prefijo (ej. RD$)", "ui": "text"},
    {"key": "suffix", "label": "Sufijo (ej. uds.)", "ui": "text"},
    {"key": "primary", "label": "Métrica Principal", "ui": "metric_select"},
    {"key": "compare", "label": "Comparar con", "ui": "metric_select"},
    {"key": "target", "label": "Meta", "ui": "metric_or_number"},
]
```

---

## API Contract (Nuevo)

### `GET /api/widget/{id}/config/`
```json
{
  "id": 123,
  "type": "bar_chart",
  "fields": {"dimensions": ["Año"], "metrics": [{"field": "Ventas", "agg": "sum", "alias": "total"}]},
  "style": {"title": "Ventas", "horizontal": true, "stacked": false},
  "style_schema": [{"key": "title", "ui": "text", ...}, ...]
}
```

### `POST /api/dashboard/{id}/widgets/`
```json
{
  "type": "bar_chart",
  "fields": {"dimensions": ["Año"], "metrics": [{"field": "Ventas", "agg": "sum", "alias": "total"}]},
  "style": {"title": "Ventas", "horizontal": true},
  "position": {"x": 0, "y": 0, "w": 6, "h": 300}
}
```

---

## Archivos a Eliminar (Limpieza Final)

```
dsl/
├── parts/
│   ├── base.py
│   ├── columns.py
│   ├── dimensions.py
│   ├── filtering.py
│   ├── metrics.py
│   ├── pivots.py
│   ├── trend.py
│   ├── __init__.py
├── metrics/
│   ├── agg.py
│   ├── calc.py
│   ├── base.py
│   ├── evaluation.py
│   ├── grouped.py
│   ├── __init__.py
├── parts.py
├── spec.py
├── rules.py          ← ELIMINAR (validación UI)
├── parts.py
├── spec.py
├── parts.py

engine/
├── plans/
│   ├── base.py
│   ├── flat.py
│   ├── pivot_chart.py
│   ├── pivot_table.py
│   ├── rows.py
│   ├── scalar.py
│   ├── __init__.py
├── executor.py

widgets/
├── base.py (reescribir)
├── (mantener solo widgets específicos)
```

---

## Criterios de Aceptación

1. ✅ `/render/` devuelve solo datos de visualización (sin specs)
2. ✅ `/config/` devuelve `fields` + `style` + `style_schema`
3. ✅ Panel "Personalizar" se renderiza dinámicamente desde `style_schema`
5. ✅ Pipeline ejecuta: Filter → Calculated → Aggregation/Pivot → Window
6. ✅ Filtros funcionan con `dsl/conditions.py` (sin `rules.py`)
7. ✅ Pivotes, window functions, métricas calculadas funcionan
8. ✅ BD limpia (sin `config` column, `view_spec` → `style` + `fields`)
9. ✅ Tests de integración pasan para todos los 7 tipos de widget

---

## Riesgos y Mitigación

| Riesgo | Probabilidad | Impacto | Mitigación |
|--------|--------------|---------|------------|
| Pérdida de funcionalidad en KPI (roles complejos) | Media | Alto | Migración tardía de KPI, testing exhaustivo |
| Performance pipeline vs planes actuales | Baja | Medio | Benchmarks en cada fase |
| Frontend Alpine.js genérico complejo | Media | Alto | Prototipo temprano del editor genérico |
| Migración datos existentes | Media | Alto | Script de migración probado en staging |

---

## Próximos Pasos Inmediatos

1. **Crear `widgets/schemas.py`** con `WidgetFields`, `WidgetStyle`, `WidgetForm`
2. **Reescribir `widgets/base.py`** con nuevo `BaseWidget` + `style_schema`
3. **Crear `engine/pipeline.py`** con `PipelineExecutor` + pasos base
4. **Migrar `BarChartWidget`** como prueba de concepto
5. **Validar** con test de integración completo

---

¿Quieres que empiece por la **FASE 1** (crear `schemas.py`, `base.py`, `pipeline.py`) o prefieres que detalle más algún aspecto específico?