# Arquitectura del frontend

## Visión general

El frontend es una aplicación **Alpine.js + Django templates** con renderizado imperativo de widgets vía **ApexCharts** (gráficos) y **Tabulator** (tablas). No usa un framework de build: los JS se cargan como scripts globales en orden de dependencia, y Alpine se inicializa con `defer`.

**Principios de diseño:**

1. **Store central único.** `$store.dashboard` (Alpine.store) es la única fuente de verdad para el estado del tablero, el drawer de edición, el schema y los filtros.
2. **Widget registry.** Un `Map` estático conecta los tipos declarados por el backend con las clases que los renderizan.
3. **Un panel por tipo.** El panel de edición de cada widget es un partial de Django escrito a mano (`templates/sheets_reports/widgets/config/_<tipo>_config.html`) con sus dos pestañas. El `widget_manifest` (desde Django) aporta capacidades (límites), `style_schema` (contrato de datos) y defaults; el store no interpreta layouts.
4. **Renderizado imperativo.** Los widgets se montan como instancias de clase con `mount()` que devuelve un elemento DOM. Alpine nunca gestiona el contenido interno de los widgets (ApexCharts/Tabulator necesitan contenedores reales).
5. **Dos vistas, dos stores.** El editor y la vista compartida definen su propio `Alpine.store('dashboard')`. El editor es completo (CRUD, drawer, schema, IA); la vista es mínima (widgets + refresh). `filters.js` extienda ambos.

## Estructura de archivos

```
templates/
  base.html                   # Tailwind, design system, apiUrl(), header
  board_editor.html           # Editor: rail + canvas + drawer (pestañas, pie y el panel del tipo)
  sheets_reports/widgets/config/
    _<tipo>_config.html       # Panel de cada tipo: «Configurar» + «Personalizar» (7 partials)
    blocks/                   # Piezas compartidas que incluyen los paneles (ver «Drawer de edición»)
  board_view.html             # Vista compartida (read-only)
  home.html                   # Lista de tableros (Alpine component local)

static/sheets_reports/js/board_editor/
  widget-registry.js          # WidgetRegistry (Map tipo → clase)
  base-widget.js              # BaseWidget (lifecycle, DOM, resize, chart)
  dashboard-store.js          # $store.dashboard (editor): estado + CRUD + drawer + IA
  filters.js                  # Filtros de tablero (extiende el store)
  board-editor-init.js        # Bootstrap: palette, drag-drop, Sortable, resize
  board-view-init.js          # Bootstrap read-only: store mínimo + mount
  utils/
    formatearEtiquetaApex.js  # Wrap de labels largos para ApexCharts
  widgets/
    kpi-widget.js             # KpiWidget (número + comparación + meta + sparkline)
    bar-widget.js             # BarWidget (barras + leyenda arrastrable)
    line-widget.js            # LineWidget (líneas)
    donut-widget.js           # DonutWidget (dona)
    table-widget.js           # TableWidget (extiende DynamicTableWidget)
    dynamic-table-widget.js   # DynamicTableWidget (pivot con subtotales)
    filter-widget.js          # FilterWidget (barra de filtros, singleton)
```

## Carga de scripts

En `board_editor.html` (y `board_view.html`), los scripts se cargan en orden:

```
base.html:  formatearEtiquetaApex.js → Alpine.js (defer)
board_editor.html:
  SortableJS → interact.js → ApexCharts → Tabulator → Virtual Select
  base-widget.js → widget-registry.js → widgets/*.js → dashboard-store.js → filters.js → board-editor-init.js
```

Alpine carga con `defer` en `base.html`, pero los stores y widgets se registran antes de que Alpine arranque (los scripts inline se ejecutan antes que el defer).

## Store central (dashboard-store.js)

Es el archivo más grande (~1000 líneas). Contiene todo el estado y la lógica del editor.

### Estado

```javascript
{
  widgets: [],              // instancias BaseWidget
  editingId: null,          // id del widget en edición
  editingType: null,        // tipo del widget en edición
  dashboardId: window.DASHBOARD_ID,
  drawerTab: 'data',        // 'data' | 'style'
  schema: { all_fields, numeric_fields, dimension_fields, sample_values },
  widgetManifest: window.WIDGET_MANIFEST,
  drawerDraft: { title, fields, style, prompt },
  // ... estados de UI (loading, errores, etc.)
}
```

### Constantes

- `EMPTY_FIELDS()`: factory de `{dimensions, pivots, metrics, filters, columns, trend_by, sort_by, limit}`.
- `FILTER_OPS`: 12 operadores con `{value, label, needsValue, isList?, isRange?}`.
- `RELATIVE_VALUES`: `current_year`, `previous_year`, `current_month`, `max`, `second_max`, `min`.
- `AGG_OPTIONS`: `sum`, `avg`, `median`, `min`, `max`, `std`, `count`, `count_distinct`.
- `CALC_OP_OPTIONS`: `sub`, `add`, `mul`, `div`, `ratio_pct`, `diff_pct`.

### Flujo de carga

```
DOMContentLoaded
  ├── renderPalette() + initRail()
  ├── store.loadSchema()           → GET /api/dashboard/{id}/schema/
  ├── store.loadBoard()            → GET /api/dashboard/{id}/render/
  │     └── BaseWidget.fromServer(w) → instancias
  ├── widget.mount() → DOM
  ├── applyRender(entries[w.id])   → widget.draw(data, style, title)
  └── setInterval(refreshData, REFRESH_MINUTES * 60000)
```

### Widget CRUD

- `addWidget(type)`: crea con ID negativo local, `WidgetRegistry.create()`.
- `_saveWidget(w)`: POST (nuevo) o PUT (existente). Al guardar uno nuevo, reemplaza el ID local por el del servidor y llama `rebuildElement()`.
- `scheduleLayoutSave()`: debounce 600ms. Guarda todos los widgets `_dirty` en paralelo.
- `flushLayoutSave({keepalive})`: envía con `navigator.sendBeacon` en `pagehide`.
- `removeWidget(id)`: DELETE si persistido, limpia filtros de tablero si es un widget header.

### Drawer de edición

`board_editor.html` dibuja la cabecera, las pestañas («Configurar» = `data`, «Personalizar» =
`style`), el aviso de error de la hoja, los `datalist` de valores de ejemplo y el pie. El cuerpo
es el panel del tipo, uno por `<template x-if="$store.dashboard.editingType === '<tipo>'">`
con su `{% include %}`. Cada partial tiene un único elemento raíz con dos `<div x-show>`
(uno por pestaña) y se arma con los bloques de `widgets/config/blocks/`:

| Bloque | Qué edita | Parámetros |
|---|---|---|
| `_title`, `_assistant`, `_json` | título, asistente de IA, visor JSON | — |
| `_columns`, `_dimensions`, `_pivots`, `_metrics` | listas de `fields` (arrastrables) | `_dimensions` / `_pivots`: `with totals=True` pinta «Mostrar totales» por nivel (tabla dinámica) |
| `_trend`, `_filters`, `_sort`, `_limit` | resto de `fields` | — |
| `_condition_row` | una condición `{field, op, valor}` dentro de un `x-for="(c, ci) in …"` | `with list="…"`: la lista que la contiene. La usan `_filters` (filtros del widget) y `_metrics` (condiciones propias de cada métrica de agregación, plegables en «Solo filas donde…», solo si `hasMetricFilters`: el widget admite más de una métrica) |
| `_style_checkbox`, `_style_text`, `_style_number`, `_style_palette` | una clave de `style` | `key`, `label` (+ `placeholder` / `min`, `max`, `step`) |

Lo propio de un tipo va escrito en su partial con Alpine. Por ejemplo, el bloque «Tarjeta KPI»
de `_kpi_config.html`: el plegable «Meta» (cerrado por defecto, con resumen en la cabecera)
es un `x-data` local con `hasMeta` / `isFixed` / `summary`, deshabilita lo que depende de la
meta y borra `style.target` cuando la meta deja de ser «Valor fijo».

`openDrawer(id)`:
1. Construye `drawerDraft` desde el widget + defaults del manifest.
2. `_normalizeDraft()`: asegura arrays, dedupes columnas, normaliza tipos y pasa a borrador
   (`conditionFromPayload`) los filtros del widget y los de cada métrica.
3. Auto-pick: columnas/dimensiones/métricas iniciales si está vacío.
4. `initListSortables()` en el siguiente tick (`Alpine.nextTick`): el `x-if` crea el panel
   del tipo después de cambiar `editingType`.

`saveDrawer()`:
1. `pruneFormulas()`: descarta fórmulas incompletas.
2. Limpia campos vacíos (dimensions, pivots, columns).
3. Mapea filtros a formato backend (`conditionToPayload`), también los de cada métrica; una
   métrica sin condiciones no lleva la clave `filters`.
4. Normaliza `trend_by`, `sort_by`, `limit`.
5. `_saveWidget(w)`: `style` va tal cual lo dejó el panel.

### Getters de capacidades

Límites y opciones que usan los bloques (qué bloques muestra cada tipo lo decide su partial):

```javascript
get hasColumns()      // capabilities.columns[1] > 0 (auto-pick al abrir)
get maxColumns() / maxDimensions() / maxPivots() / maxMetrics()   // tope de cada lista
get hasFormulaMetrics()   // admite métricas de tipo fórmula
get hasMetricFilters()    // maxMetrics > 1: cada métrica admite sus propias condiciones
get trendOptions()    // dimension_fields (fallback: all_fields)
metricRoleOptions(base)   // opciones fijas del partial + las métricas del borrador (roles del KPI)
```

### Asistente IA (chat del panel)

El bloque `_assistant.html` es un chat por widget. Cada widget tiene su hilo en
`assistantThreads[id]` (en memoria: sobrevive a cerrar y reabrir el panel, se pierde al
recargar; borrar el widget borra su hilo; un widget nuevo lo conserva al recibir su id real).
`drawerThread` es el hilo del widget que se edita. Mensajes:
`{role: 'user', text}`, `{role: 'assistant', proposal, applied, undo}` o `{role: 'assistant', error}`.

`askAssistant()` (Enter o «Enviar»): POST `/api/dashboard/{id}/table-assistant/` con
`{prompt, widget_type, current, history}`:
- `current` = `_draftPayload()`: el borrador del panel tal como lo guardaría «Guardar»
  (`{title, fields, style}`, sin filas a medio elegir ni cálculos incompletos). La IA lo
  **ajusta** en vez de crear desde cero.
- `history` = los mensajes previos del hilo (pedidos y propuestas; los errores no viajan).

La respuesta (`{widget_type, fields, style}`; `title` va vacío: el título de la tarjeta lo
pone el usuario, la IA no lo genera) se agrega al hilo y **no** toca el borrador.
`adviceSteps(proposal)` la convierte en pasos legibles en el orden del panel
(columnas/filas/columnas cruzadas, condiciones, valores, tendencia, orden, límite y apariencia
con las etiquetas del `style_schema`). Cada propuesta lleva su botón «Aplicar y guardar»:
`applyAdvice(message)` copia `fields` al borrador, **suma** el `style` propuesto al actual
(el título no cambia) y guarda con `saveDrawer({fromAssistant: true})`, así el widget se
redibuja al momento. El mensaje guarda en `undo` el `_draftPayload()` previo: «Deshacer»
(`undoAdvice(message)`) lo restaura en el borrador y lo guarda. Solo la última propuesta
aplicada tiene `undo`, y un «Guardar» manual lo borra (`_clearAdviceUndo()`). Si el guardado
falla, el borrador queda con la propuesta y el error en el pie del panel.
«Nueva conversación» (`clearThread()`) vacía el hilo del widget.

## Widget registry

```javascript
WidgetRegistry.register(WidgetClass)  // por tipo
WidgetRegistry.create(type, raw)      // new (clase)(raw)
WidgetRegistry.getPaletteEntries()    // para el rail
```

Cada widget se auto-registra al final de su IIFE. El registry es la capa de desacoplamiento entre los tipos declarados por el backend y las clases del frontend.

## BaseWidget

Clase abstracta base para todos los widgets. Define el lifecycle, construcción de DOM, resize y renderizado.

### Propiedades estáticas

| Propiedad | Descripción |
|---|---|
| `type` | Identificador del tipo |
| `palette` | `{icon, category, label, description}` para el rail |
| `defaults` | `{title, width, height}` |
| `placement` | `'canvas'` o `'header'` (filtro) |
| `singleton` | `max_per_dashboard === 1` |

### Lifecycle

```
fromServer(w) → new WidgetClass(raw)
  │
  ├── mount() → buildElement() → DOM element
  │     ├── _attachCommonEvents()  (edit/delete buttons)
  │     ├── initResize()           (interact.js)
  │     └── setLoading(true)
  │
  ├── applyRender(entry)
  │     ├── setLoading(false)
  │     └── draw(entry.data, entry.style, entry.title)
  │
  └── destroy()
```

### DOM estándar

`buildStandardCardElement()` genera:
- `.widget-card` con `data-widget-id`, `data-type`
- `.drag-handle` (Sortable handle)
- `.widget-title` (barra de título)
- `#chart-{id}` (contenedor de contenido)
- `.widget-loader` (spinner overlay)
- `.edit-widget-btn` / `.delete-widget-btn`
- `.resize-handle` (esquina inferior derecha)

### Resize

`initResize()`: `interact.js` con `edges: {bottom, right}`. Snap a:
- Altura: pasos de 20px (mín `minHeight`, máx 3000).
- Ancho: columnas enteras del grid (12 columnas).

Al soltar: `position` se actualiza, `_dirty = true`, `store.scheduleLayoutSave()`.

### Renderizado de gráficos

`renderApexChart(container, options)`: destruye el chart anterior, crea `new ApexCharts`, devuelve `.render()`.

Utilidades compartidas:
- `percentAwareFormatter()`: añade `%` a series de porcentaje.
- `referenceAnnotations()`: líneas de referencia (meta, promedio, máximo, mínimo).
- `chartExportToolbar()`: toolbar de exportación (CSV, SVG, PNG).
- `escapeHTML()`: escape de HTML.

## Tipos de widget (frontend)

### KpiWidget

Renderiza: número grande + etiqueta + badge de comparación + barra de progreso (meta) + sparkline (tendencia).

- `format(value, {percent, signed})`: decimales, abreviar, prefijo, sufijo.
- `_compareHTML()`: delta con flecha y color.
- `_targetHTML()`: barra de progreso con porcentaje.
- `_renderTrend()`: ApexCharts area sparkline (height 48).

### BarWidget

Renderiza: gráfico de barras con apilado, modo horizontal, data labels, líneas de referencia y leyenda arrastrable.

- `_wireLegendDrag()`: SortableJS en la leyenda. Al reordenar, guarda `style.seriesOrder`.
- Colores estables por serie (`_seriesColors` Map).

### LineWidget

Renderiza: gráfico de líneas con curva suave, markers, data labels y líneas de referencia.

### DonutWidget

Renderiza: gráfico de dona con etiquetas centrales (valor + total), leyenda opcional y modo de labels (valor o porcentaje).

### DynamicTableWidget

Renderiza: tabla dinámica con jerarquía de filas, subtotales por nivel, total general, columnas reordenables y formatos por columna.

- `toTabulator()`: constructor recursivo de columnas (grupos, subtotales, totales).
- `_displayRows()`: filas con subtotales visibles/ocultos según `style.showTotals`.
- `applyFormatter()`: cambio de formato por columna (texto, moneda, porcentaje, barra de progreso).
- `_wireTableEvents()`: `columnMoved` → guarda `style.columnOrder`.

### TableWidget

Extiende `DynamicTableWidget`. Tabla simple sin jerarquía: columnas planas, paginación, formatos por columna.

### FilterWidget

Barra de filtros del tablero. `placement: 'header'`, singleton. Usa Virtual Select para multi-select. Al cambiar, llama `store.setBoardFilter()`.

## Sistema de filtros

Dos niveles:

1. **Filtros del widget** (`fields.filters`): se guardan con el widget. Se envían en el POST/PUT del widget.
2. **Filtros del tablero** (`boardFilters`): estado global, compartido por todos los widgets. Se persisten en la URL (`?filters=`) y se envían al endpoint `/render/`.

`filters.js` extiende el store con:
- `boardFilters: {}` — `{columna: [valores]}`.
- `setBoardFilter(field, values)`: actualiza y despacha `dashboard:filters-changed`.
- `getFilterQueryString()`: `filters=<JSON codificado>`.
- `initBoardFiltersFromURL()`: parsea la URL al cargar.

El evento `dashboard:filters-changed` dispara `store.refreshData()`, que re-fetch `/render/` con los filtros y llama `applyRender` en cada widget.

## Drag and drop

Tres instancias de SortableJS:

1. **Palette → Canvas**: `pull: 'clone'`, `put: false`. Al soltar, crea el widget y abre el drawer.
2. **Canvas**: reordena widgets. `onEnd` → `reorderWidgets()` → `scheduleLayoutSave()`.
3. **Listas del drawer**: reordena columnas/dimensiones/pivotes/métricas. `onEnd` → actualiza el array del draft.

Todas usan `forceFallback: true` + `fallbackOnBody: true` para arrastre consistente entre contenedores.

## Design system

- **Tailwind CSS** vía CDN con paleta personalizada: `ink`, `paper`, `header`, `line`, `moss` (50-700 + tint), `clay` (50-600).
- **Tabler Icons** (`ti ti-*`).
- **CSS custom properties** en `:root` para toda la paleta.
- **`[x-cloak]`** global para ocultar elementos antes de que Alpine arranque.

## apiUrl y despliegue

`window.apiUrl(path)` en `base.html` antepone `window.SCRIPT_NAME` (de `REPORT_PATH` en settings). Esto permite que la app corra bajo un sub-path en producción (ej. `/sheets-reports`) sin hardcodear URLs en el JS.

## Flujo de datos completo

```
Usuario abre /board/{id}/
  │
  ├── board_editor.html carga
  │     ├── window.WIDGET_MANIFEST (inline JSON)
  │     └── Alpine.js (defer)
  │
  ├── board-editor-init.js (DOMContentLoaded)
  │     ├── renderPalette() ← WidgetRegistry.getPaletteEntries()
  │     ├── store.loadSchema() → GET /schema/
  │     ├── store.loadBoard() → GET /render/
  │     │     └── BaseWidget.fromServer(w) → instancias
  │     ├── widget.mount() → DOM
  │     └── applyRender() → widget.draw(data, style, title)
  │
  ├── Usuario edita widget
  │     ├── openDrawer(id) → drawerDraft
  │     ├── Cambios en el drawer → drawerDraft.*
  │     └── saveDrawer() → PUT /api/widget/{id}/
  │           └── _saveWidget() → applyRender()
  │
  ├── Usuario cambia filtros de tablero
  │     └── setBoardFilter() → dashboard:filters-changed
  │           └── refreshData() → GET /render/?filters=...
  │                 └── applyRender() en cada widget
  │
  └── setInterval → refreshData() cada REFRESH_MINUTES
```
