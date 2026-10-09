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
  sheets_reports/
    source_manager.html       # Gestor de fuentes del tablero (bottom sheet)
    source_picker.html        # Selector de hoja → pestaña → columnas (crear, agregar, editar, cambiar hoja)

static/sheets_reports/js/board_editor/
  widget-registry.js          # WidgetRegistry (Map tipo → clase)
  base-widget.js              # BaseWidget (lifecycle, DOM, resize, chart)
  dashboard-store.js          # $store.dashboard (editor): estado + CRUD + drawer + IA
  filters.js                  # Filtros de tablero (extiende el store)
  source-manager.js           # sourceManager(): fuentes del tablero, actualizar datos, eliminar
  source-picker.js            # sourcePicker(): elegir/editar/reemplazar la hoja de una fuente
  formula-builder.js          # FormulaBlocks: árbol ↔ texto, bloques y arrastre de los campos calculados
  board-editor-init.js        # Bootstrap: palette, drag-drop, Sortable, resize
  board-view-init.js          # Bootstrap read-only: store mínimo + mount
  utils/
    formatearEtiquetaApex.js  # Wrap de labels largos para ApexCharts
    number-format.js          # formatNumber: el formato de todo valor de datos
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
  utils/number-format.js → base-widget.js → widget-registry.js → widgets/*.js → dashboard-store.js → filters.js → board-editor-init.js
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
  schema: { all_fields, numeric_fields, dimension_fields, time_fields, sample_values },
  widgetManifest: window.WIDGET_MANIFEST,
  drawerDraft: { title, fields, style, prompt },
  // ... estados de UI (loading, errores, etc.)
}
```

### Constantes

- `EMPTY_FIELDS()`: factory de `{dimensions, pivots, metrics, filters, columns, trend_by, sort_by, limit}`.
- `FILTER_OPS`: 12 operadores con `{value, label, needsValue, isList?, isRange?}`.
- `RELATIVE_VALUES`: `current_year`, `previous_year`, `current_month` (del reloj) y los
  periodos de los datos (`data: true`) `latest` «El periodo más reciente», `previous` «El
  periodo anterior», `earliest` «El periodo más antiguo». `relativeOptionsFor(c)` ofrece los
  periodos solo si la columna de la condición está en `schema.time_fields`; al cambiar de
  columna, `onFilterFieldChange` borra un periodo que ya no aplica.
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
  └── applyRender(entries[w.id])   → widget.draw(data, style, title)
```

El tablero no se refresca solo: los datos de cada hoja quedan en caché en el servidor hasta
que el usuario pulsa «Actualizar datos» al editar la fuente.

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
| `_trend`, `_filters`, `_sort` | resto de `fields` (el N del ranking es un input propio de su partial) | — |
| `_metrics` · campos agregados | `metric.field` + `metric.agg` | el select de columna suma un grupo «Campos calculados agregados» (`schema.aggregated_fields`); al elegir uno, `onMetricFieldChange` pone `agg: 'auto'` y el select de agregación muestra solo «Automática» (deshabilitado). `metricName` usa el nombre del campo. Al cambiar campo o agregación se regenera el alias y `_rebindAlias` reapunta a él los controles de estilo con `options_from: 'metrics'` y `sort_by` (misma dirección); al quitar la métrica, se vacían |
| `_metrics` · «Mostrar como» | `metric.window` de cada métrica de agregación | select con `metricWindowOptions` (`WINDOW_OPTIONS`: «% del total de la columna», «% del total de la fila», «Acumulado», «Variación vs anterior», más «Valor» = sin ventana), mostradas con el prefijo «Mostrar: …» (ej. «Mostrar: valor») para que se lea junto a la agregación. Solo las de `capabilities.windows`; con pivotes, solo los dos porcentajes (`PIVOT_WINDOWS`, se calculan por celda), y «% del total de la fila» solo con pivotes (mismas reglas que `form_errors`). Si la ventana guardada deja de valer, avisa «se quitará al guardar» |
| `_condition_row` | una condición `{field, op, valor}` dentro de un `x-for="(c, ci) in …"` | `with list="…"`: la lista que la contiene. La usan `_filters` (filtros del widget) y `_metrics` (condiciones propias de cada métrica de agregación, plegables en «Solo filas donde…», solo si `hasMetricFilters`: el widget admite más de una métrica) |
| `_style_checkbox`, `_style_text`, `_style_number`, `_style_palette` | una clave de `style` | `key`, `label` (+ `placeholder` / `min`, `max`, `step`) |

Lo propio de un tipo va escrito en su partial con Alpine. Por ejemplo, el bloque «Tarjeta KPI»
de `_kpi_config.html`: el plegable «Meta» (cerrado por defecto, con resumen en la cabecera)
es un `x-data` local con `hasMeta` / `isFixed` / `summary`, deshabilita lo que depende de la
meta y borra `style.target` cuando la meta deja de ser «Valor fijo».

`openDrawer(id)`:
1. Construye `drawerDraft` desde el widget + defaults del manifest.
2. `_normalizeDraft()`: asegura arrays, dedupes columnas, normaliza tipos y pasa a borrador
   (`conditionFromPayload`) los filtros del widget y los de cada métrica. Los cálculos entre
   totales no son métricas del widget: son campos calculados agregados de la fuente (ver
   «Fuentes de datos»), que la métrica usa con agregación «Automática».
3. Auto-pick: columnas/dimensiones/métricas iniciales si está vacío.
4. `initListSortables()` en el siguiente tick (`Alpine.nextTick`): el `x-if` crea el panel
   del tipo después de cambiar `editingType`.

`saveDrawer()`:
1. Limpia campos vacíos (dimensions, pivots, columns).
2. Mapea filtros a formato backend (`conditionToPayload`), también los de cada métrica; una
   métrica sin condiciones no lleva la clave `filters`. Quita el `window` de las métricas
   cuya ventana ya no vale (`metricWindowInvalid`), así guardar no choca con `form_errors`.
3. Normaliza `trend_by`, `sort_by`, `limit`; un `sort_by` que ya no está en `sortOptions` (columna, dimensión o métrica quitada) no se envía; `limit` solo se envía si el tipo lo admite (`capabilities.limit`, solo el ranking).
4. `_saveWidget(w)`: `style` va tal cual lo dejó el panel.

### Getters de capacidades

Límites y opciones que usan los bloques (qué bloques muestra cada tipo lo decide su partial):

```javascript
get hasColumns()      // capabilities.columns[1] > 0 (auto-pick al abrir)
get maxColumns() / maxDimensions() / maxPivots() / maxMetrics()   // tope de cada lista
get metricsLimit()        // maxMetrics, o pivot_max_metrics si hay un pivote elegido (barras, líneas)
get pivotsBlocked()       // pivot_max_metrics y más métricas que ese tope: «Agregar» pivote deshabilitado
get hasMetricFilters()    // maxMetrics > 1: cada métrica admite sus propias condiciones
get metricWindowOptions() // «Mostrar como»: capabilities.windows válidas con los pivotes del borrador
get trendOptions()    // dimension_fields (fallback: all_fields)
metricRoleOptions(base)   // opciones fijas del partial + las métricas del borrador (roles del KPI)
```

### Asistente IA (chat del panel)

El bloque `_assistant.html` es un chat por widget. Cada widget tiene su hilo en
`assistantThreads[id]` (en memoria: sobrevive a cerrar y reabrir el panel, se pierde al
recargar; borrar el widget borra su hilo; un widget nuevo lo conserva al recibir su id real).
`drawerThread` es el hilo del widget que se edita. Mensajes:
`{role: 'user', text}`, `{role: 'assistant', proposal, baseStyle, applied, undo}` o `{role: 'assistant', error}`.

`askAssistant()` (Enter o «Enviar»): POST `/api/dashboard/{id}/table-assistant/` con
`{prompt, widget_type, current, history}`:
- `current` = `_draftPayload()`: el borrador del panel tal como lo guardaría «Guardar»
  (`{title, fields, style}`, sin filas a medio elegir). La IA lo
  **ajusta** en vez de crear desde cero.
- `history` = los mensajes previos del hilo (pedidos y propuestas; los errores no viajan).

La respuesta (`{widget_type, calculated_fields, fields, style}`; `title` va vacío: el título de la tarjeta lo
pone el usuario, la IA no lo genera) se agrega al hilo y **no** toca el borrador.
`adviceSteps(proposal, baseStyle)` la convierte en pasos legibles en el orden del panel
y con el nombre de cada bloque (los campos calculados que propone crear, `columnsLabel`,
«Dimensiones» o `dimensions_label` (con etiqueta propia, el detalle es solo la lista de columnas, sin «Agrupa»),
«Pivotes», «Filtros», «Métricas», tendencia, orden y límite). Del
`style` solo lista lo que **cambia** respecto a `baseStyle` (el estilo del panel al pedir,
guardado en el mensaje: la IA devuelve el estilo completo), con las etiquetas del
`style_schema` y agrupado como en el panel: las claves que un partial de «Configurar»
registró con `registerStyleGroup(grupo, claves)` (ej. `_kpi_config.html` → «Tarjeta KPI»;
`_dimensions` / `_pivots` con `totals` → «Totales») van bajo ese grupo; el resto, bajo
«Personalizar». El grupo es maquetación: vive en el partial (`styleGroups[tipo]`), no en el
`style_schema`, que solo declara datos. Cada propuesta lleva su botón «Aplicar y guardar»:
`applyAdvice(message)` primero crea en la fuente los `calculated_fields` propuestos
(`_createCalculatedFields`: `POST /api/sources/{id}/calculated-fields/` y recarga el schema),
luego copia `fields` al borrador, **suma** el `style` propuesto al actual
(el título no cambia) y guarda con `saveDrawer({fromAssistant: true})`, así el widget se
redibuja al momento. El mensaje guarda en `undo` el `_draftPayload()` previo: «Deshacer»
(`undoAdvice(message)`) lo restaura en el borrador y lo guarda. Solo la última propuesta
aplicada tiene `undo`, y un «Guardar» manual lo borra (`_clearAdviceUndo()`); deshacer no
borra los campos calculados creados, que quedan en la fuente. Si el guardado
falla, el borrador queda con la propuesta y el error en el pie del panel.
«Nueva conversación» (`clearThread()`) vacía el hilo del widget.

El chat muestra siempre arriba del hilo, como tags, los **pedidos sugeridos** para el tipo de
widget (siguen visibles después de usar uno).
`openDrawer()` llama a `loadSuggestions(type)` sin esperar: GET
`/api/dashboard/{id}/widget-suggestions/?widget_type=…`, una vez por tipo y sesión
(`assistantSuggestions[type]`; `_suggestionsLoading` evita pedidos repetidos; si falla queda
`[]` y no hay tags). Si el usuario abre el chat antes de que lleguen, los tags aparecen al
llegar. El botón ↻ («Otras ideas», `refreshSuggestions()`) llama a
`loadSuggestions(type, {refresh: true})`: pide con `refresh=1` y las actuales como `avoid`;
si no llegan nuevas se quedan las actuales. Mientras carga, el ícono gira
(`drawerSuggestionsLoading`). `drawerSuggestions` es la lista del `editingType`; un clic en un tag
(`askSuggestion(text)`) lo envía con `askAssistant()`.

Mientras la IA responde, el icono de «Enviar» es un spinner (`drawerAsking`).

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

## Formato de los números (`utils/number-format.js`)

El motor manda los valores sin redondear; todo valor de datos que se muestra (celdas de las
tablas, KPI, etiquetas y ejes de los gráficos, vista previa de los campos calculados) pasa por
`formatNumber(valor, opciones)`, así la misma cifra se ve igual en todas partes:

- Hasta 2 decimales por defecto; `decimals` fija una cantidad exacta.
- `percent` agrega «%» (el valor ya viene × 100); `currency` antepone «$» con 2 decimales.
- `compact` (1,2 mil), `signed` (signo también en positivos), `prefix` / `suffix`.
- Vacío → `empty` (`-` por defecto); un texto no numérico se devuelve tal cual.
- Locale: `NUMBER_LOCALE` (el del navegador), en un solo lugar.

El servidor no formatea: dice **qué formato** tiene cada métrica (`payload.formats` /
`payload.percent` en tablas y gráficos, `payload.format` en el KPI) y el front aplica
`formatNumber`.

## Tipos de widget (frontend)

### KpiWidget

Renderiza: número grande + etiqueta + badge de comparación + barra de progreso (meta) + sparkline (tendencia).

- `format(value, {percent, signed})`: `formatNumber` con el formato de la métrica principal
  (`payload.format`: porcentaje, moneda…) y los decimales, abreviar, prefijo y sufijo del estilo.
- `_compareHTML()`: delta con flecha y color.
- `_targetHTML()`: barra de progreso con porcentaje.
- `_renderTrend()`: ApexCharts area sparkline (height 48).

### BarWidget

Renderiza: gráfico de barras con apilado, modo horizontal, data labels, líneas de referencia y leyenda arrastrable.

- `_wireLegendDrag()`: SortableJS en la leyenda. Al reordenar, guarda `style.seriesOrder` (lista de nombres de serie) y `draw()` ordena las series desde `style`.
- Colores estables por serie (`_seriesColors` Map).

### LineWidget

Renderiza: gráfico de líneas con curva suave, markers, data labels y líneas de referencia.

### ScatterWidget

Renderiza: gráfico de dispersión (ApexCharts mixto): una serie `scatter` por grupo de `payload.groups` (color `CHART_COLORS[i]`) y, con `style.showTrend`, una serie `line` discontinua con la recta de cada grupo en su mismo color (sin color por categoría, la recta va en el color de las referencias). Sin animación (ApexCharts deja a medias el trazo discontinuo). Ejes numéricos con el nombre de cada columna; tooltip con el grupo, su `r` y los dos valores. Pie con las filas dibujadas («Muestra de N de M filas» si el backend muestreó) y la `r` global.

- Leyenda solo con color y solo con los grupos (`customLegendItems`); `_wireLegend()` oculta o muestra a la vez los puntos y la recta del grupo pulsado (listener delegado en el contenedor: ApexCharts rehace la leyenda en cada `toggleSeries`).

### DonutWidget

Renderiza: gráfico de dona con etiquetas centrales (valor + total), leyenda opcional y modo de labels (valor o porcentaje).

### DynamicTableWidget

Renderiza: tabla dinámica con jerarquía de filas, subtotales por nivel, total general, columnas reordenables y formatos por columna.

- `toTabulator()`: constructor recursivo de columnas (grupos, subtotales, totales).
- `_displayRows()`: filas con subtotales visibles/ocultos según `style.showTotals`.
- Formato de cada columna: `payload.formats` (el de su métrica o el de la columna en la fuente) y
  `payload.percent`; no hay formato por columna en la tabla.
- `_wireTableEvents()`: `columnMoved` → guarda `style.columnOrder`.

### TableWidget

Extiende `DynamicTableWidget`. Tabla simple sin jerarquía: columnas planas, paginación, formatos por columna.

### FilterWidget

Barra de filtros del tablero. `placement: 'header'`, singleton. Usa Virtual Select para multi-select. Al cambiar, llama `store.setBoardFilter()`.

## Fuentes de datos

Cada tablero tiene una o varias fuentes (una pestaña de una hoja de Google); cada widget
elige la suya en su panel. Se gestionan en el bottom sheet «Fuentes de datos».

**Gestor** (`source-manager.js`, `source_manager.html`): tabla de fuentes con su nombre (el
propio, con «Documento · Pestaña» debajo, o el original), columnas incluidas («7 de 9
incluidas»), widgets que la usan («Sin usar» si ninguno) y «Actualizado hace X»
(`timeAgoLabel`).
Al cargar, pide el **estado** de cada fuente (`GET …/status/`, en paralelo) y marca las que
tienen problema («Sin acceso», «Pestaña no encontrada»). Acciones por fuente:

- **Actualizar** → `POST …/refresh/` (como «Actualizar datos» del selector, sin abrirlo).
- **Cambiar fuente** → selector en `edit` con `changeSheet` (pasa a `replace` cuando
  cargan las columnas guardadas).
- **Abrir origen** → la hoja de Google en la pestaña de la fuente (`#gid=`), en otra pestaña.
- **Editar** → selector en modo `edit` (columnas y campos calculados).
- **Eliminar** → `DELETE ?dry_run=1` lista los widgets que se quedan sin datos y pide
  confirmación.

**Selector** (`source-picker.js`, `source_picker.html`), modos `create` (tablero nuevo),
`add`, `edit` y `replace`. Pasos: fuente → documento de Drive → pestaña → columnas. En el paso
de columnas:

- «Usar la primera fila como encabezado» (`?headers=0|1`): sin ella, la fila 1 es un dato y
  las columnas se llaman «Columna A», «Columna B»…
- Por columna: incluir, **nombre a mostrar** (reemplaza al encabezado en todo el tablero) y tipo.
- En `edit`, pestaña **Campos calculados**: los campos como fichas (el elegido se arma abajo),
  nombre, el tipo que decide la fórmula («Por fila» / «Agregado») y, en los agregados, formato
  Número o Porcentaje. La fórmula se **arma con bloques** (ver abajo); con cada cambio,
  `previewCalculated` pide a `POST /api/sources/{id}/formula/` los primeros valores (o el total
  de la hoja) o el error, con lo que hay en el editor sin guardar. El ícono de ayuda abre
  `source_formula_help.html`: los dos tipos con ejemplos, cuál elegir, cómo calcula cada
  función y cómo se arma.

- En `edit` y `replace`, nombre opcional de la fuente (vacío = el original).
- **Actualizar datos**, junto al nombre (`refreshColumns()`): relee la pestaña de Google
  (`?refresh=1`, pisa el caché del servidor) y muestra su estructura actual sin perder los
  cambios todavía sin guardar (`_keepEdits`, por encabezado). Las columnas nuevas entran
  incluidas; las que ya no están desaparecen y, al guardar, el aviso de impacto lista los
  widgets que las usaban. Si se sale sin guardar tras actualizar, el tablero igual se recalcula
  (`sources:changed`).
- **Cambiar hoja**, junto a «Actualizar datos» en `edit` (`changeSheet()`): pasa a modo
  `replace` (documento → pestaña → columnas). Las columnas con el mismo encabezado conservan lo
  elegido en la edición, aunque no estuviera guardado; «Cancelar» vuelve a la edición tal
  como estaba (`_backToEdit`).
- Antes de guardar en `edit` o `replace` se pide `dry_run`: si algún widget usa columnas que
  se quitan o cambian de tipo, se listan con «Guardar igual» / «Volver».
- El email de la cuenta de servicio (con quien compartir las hojas) se muestra con botón
  copiar.

#### Constructor de fórmulas (`formula-builder.js`)

La fórmula es un árbol `{kind, value, args}` — el mismo del parser del servidor; la fuente lo
trae en `calculated_fields[].tree` (`formula_tree`) — donde un hueco es `null`. La gramática vive
solo en el servidor: el JS no escribe ni lee texto de fórmulas. Las piezas (funciones con sus
huecos y rótulos, operadores con su símbolo y título) vienen de `BUILDER_CATALOG`
(`engine/formulas.py`), publicado en la página como `#formula-catalog` (`FormulaBlocks.catalog`).

- **Panel izquierdo** (`aside[data-fb-trash]`): *Columnas* (`builderColumns`: las incluidas con
  su nombre a mostrar y los campos por fila anteriores, con buscador) y su subpanel *Valores*
  (Número/Texto → `valuePiece`); *Condición* (comparación, Y, O, NO); *Funciones* (SI y las
  agregaciones). Bajo el canvas, la barra de operadores `+ − × ÷`.
- **Canvas**: `FormulaBlocks.render` dibuja el árbol (Alpine no tiene templates recursivos)
  desde un `x-effect`. SI es un bloque vertical con tres huecos rotulados; las agregaciones,
  `SUM( [ ] )`; comparaciones, aritmética y Y/O, `[ ] op [ ]` con el operador en un `select`
  (cambia dentro de su familia).
- **Colocar** (`FormulaBlocks.place`): en un hueco, la pieza entra; sobre un bloque lleno, una
  pieza con huecos lo envuelve (pasa a su primer hueco) y una sin huecos lo reemplaza. Mover un
  bloque deja su hueco y no puede caer dentro de sí mismo; soltarlo en el panel lo quita.
  Después queda elegido el siguiente hueco (`nextHole`), así también se arma solo con clics
  (clic en hueco + clic en pieza; `fbSelected`).
- **Generar con IA** (barra sobre el canvas): `generateWithAI(field)` manda el pedido con la hoja
  como está en el editor (`_draftBody`, lo mismo que la vista previa) a
  `POST /api/sources/{id}/formula/ai/`. Con éxito, el árbol que vuelve reemplaza el del campo
  (`_setTree`) y, si el campo no tiene nombre, toma el que propone la IA; si no pudo, su motivo
  queda en `field._aiError` y el lienzo no cambia.
- **Arrastre**: interact.js con selectores delegados (`[data-fb-piece]` del panel, JSON de la
  pieza; `[data-fb-drag]` de los bloques, su ruta). El destino es el `[data-fb-drop]` más
  interno bajo el puntero; al soltar se emite `formula:drop` y `onFormulaDrop` aplica el cambio.
- **Al servidor va el árbol**: la vista previa (`POST …/formula/` con `tree`) responde también
  con el texto (`formula_text`), que queda en `field.formula`; al guardar, cada campo manda su
  `tree` y el servidor guarda el texto. Una fórmula guardada ilegible que no se tocó va como
  `formula`. Con huecos (`hasHoles`) no se pide vista previa y guardar avisa «Completa los
  huecos». `field._rev` descarta respuestas de vistas previas viejas.
- Renombres: al abrir la pestaña (y al guardar) `syncColumnNames` lleva a los árboles los nombres
  a mostrar cambiados en *Columnas*; renombrar un campo (`renameField`) actualiza las fórmulas
  que lo usan. Una fórmula guardada que no se puede leer (`tree: null`) se avisa con «Empezar de
  nuevo».

Tras guardar, `afterChange()` recarga las fuentes, vacía los schemas cacheados del store,
recalcula el tablero y reabre el panel abierto. `refreshData()` toma `fields`/`style` del
servidor para los widgets sin cambios locales: al renombrar una columna el servidor reescribe
los widgets de esa fuente.

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
  └── Usuario actualiza, edita o reemplaza una fuente (gestor de fuentes)
        └── afterChange() → resetSources() + refreshData() + openDrawer() si hay panel abierto
```
