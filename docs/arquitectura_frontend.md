# Arquitectura del frontend

## Visión general

El frontend es una aplicación **Alpine.js + Django templates** con renderizado imperativo de widgets vía **ApexCharts** (gráficos), una tabla HTML propia (`DataTable`, widgets de tabla) y **Tabulator** (tablas de fuentes y del envío de correos). No usa un framework de build: los JS se cargan como scripts globales en orden de dependencia, y Alpine se inicializa con `defer`.

**Principios de diseño:**

1. **Store central único.** `$store.dashboard` (Alpine.store) es la única fuente de verdad para el estado del tablero, el drawer de edición, el schema y los filtros.
2. **Widget registry.** Un `Map` estático conecta los tipos declarados por el backend con las clases que los renderizan.
3. **Un panel por tipo.** El panel de edición de cada widget es un partial de Django escrito a mano (`templates/sheets_reports/widgets/config/_<tipo>_config.html`) con sus dos pestañas. El `widget_manifest` (desde Django) aporta capacidades (límites), `style_schema` (contrato de datos) y defaults; el store no interpreta layouts.
4. **Renderizado imperativo.** Los widgets se montan como instancias de clase con `mount()` que devuelve un elemento DOM. Alpine nunca gestiona el contenido interno de los widgets (ApexCharts/Tabulator necesitan contenedores reales).
5. **Dos vistas, dos stores.** El editor y la vista compartida definen su propio `Alpine.store('dashboard')`. El editor es completo (CRUD, drawer, schema, IA); la vista es mínima (widgets + refresh). `filters.js` extienda ambos.

## Estructura de archivos

```
templates/
  base.html                   # Tailwind (nombres de tokens), tokens.css, apiUrl(), header
  sheets_reports/_app_ui.html # Componentes ui-* de la app (capa components de Tailwind)
  sheets_reports/_board_theme_attrs.html  # data-board-theme + colores propios del tablero
  board_editor.html           # Editor: rail + canvas + drawer (pestañas, pie y el panel del tipo)
  sheets_reports/widgets/config/
    _<tipo>_config.html       # Panel de cada tipo: «Configurar» + «Personalizar» (7 partials)
    blocks/                   # Piezas compartidas que incluyen los paneles (ver «Drawer de edición»)
  board_view.html             # Vista compartida (read-only)
  home.html                   # Lista de tableros (Alpine component local)
  sheets_reports/
    source_manager.html       # Gestor de fuentes del tablero (bottom sheet)
    source_picker.html        # Selector de hoja → pestaña → columnas (crear, agregar, editar, cambiar hoja)
    email_campaign.html       # Envío de correos: destinatarios, remitentes, programación y mensaje

static/sheets_reports/css/
  tokens.css                  # Tokens de la app: marca → semánticos → componentes
  tablero-theme.css           # Tema de los tableros (--tb-*) y estilos de lo que va en el lienzo
  app-tabulator.css           # Tabulator de la app (home, fuentes, seguimiento de correos)
  board-editor.css            # Layout propio del editor (riel, arrastre, fórmulas, correo)

static/sheets_reports/js/
  chart-theme.js              # ChartTheme: colores de ApexCharts desde las variables --tb-*

static/sheets_reports/js/board_editor/
  widget-registry.js          # WidgetRegistry (Map tipo → clase)
  base-widget.js              # BaseWidget (lifecycle, DOM, resize, chart)
  dashboard-store.js          # $store.dashboard (editor): estado + CRUD + drawer + IA
  filters.js                  # Filtros de tablero (extiende el store)
  vselect.js                  # Directiva x-vselect: selects del editor con buscador (VirtualSelect)
  source-manager.js           # sourceManager(): fuentes del tablero, actualizar datos, eliminar
  source-picker.js            # sourcePicker(): elegir/editar/reemplazar la hoja de una fuente
  formula-builder.js          # FormulaBlocks: árbol ↔ texto, bloques y arrastre de los campos calculados
  ai-board.js                 # aiBoard(): modal «Generar con IA» (lista propuesta y creación)
  data-chat.js                # dataChat(): chatbot analítico de datos (Gemini + Code Execution)
  board-editor-init.js        # Bootstrap: palette, drag-drop, Sortable, resize
  email-campaign.js           # emailCampaign(): configurar el envío de correos del tablero
  board-view-init.js          # Bootstrap read-only: store mínimo + mount
  email-report.js             # Vista compartida: pestañas y reporte del envío de correos
  utils/
    formatearEtiquetaApex.js  # Wrap de labels largos para ApexCharts
    number-format.js          # formatNumber: el formato de todo valor de datos
    data-table.js             # DataTable: la <table> de los widgets table y dynamic_table
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
  SortableJS → interact.js → ApexCharts → chart-theme.js → Tabulator → Virtual Select
  utils/number-format.js → utils/data-table.js → base-widget.js → widget-registry.js → widgets/*.js → dashboard-store.js → filters.js → board-editor-init.js
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
- `RELATIVE_VALUES`: `current_year`, `previous_year`, `current_month`, `previous_month` (del reloj) y los
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
| `_metrics` · «Mostrar como» | `metric.window` de cada métrica de agregación | select con `metricWindowOptions` (`WINDOW_OPTIONS`: «% del total» —con pivotes, «% del total de la columna» en la tabla dinámica (`pivotLabel`) y en los gráficos «% del total de cada {pivote}» (`chartBy`), porque ahí no se ven columnas ni filas—, «% del total de la fila» (en gráficos, «% del total de cada {dimensión}»), «Acumulado», «Variación vs anterior», más «Valor» = sin ventana), mostradas con el prefijo «Mostrar: …» (ej. «Mostrar: valor») para que se lea junto a la agregación. Solo las de `capabilities.windows`; con pivotes, solo los dos porcentajes (`PIVOT_WINDOWS`, se calculan por celda), y «% del total de la fila» solo con pivotes (mismas reglas que `form_errors`). Si la ventana guardada deja de valer, avisa «se quitará al guardar» |
| `_condition_row` | una condición `{field, op, valor}` dentro de un `x-for="(c, ci) in …"` | `with list="…"`: la lista que la contiene. La usan `_filters` (filtros del widget) y `_metrics` (condiciones propias de cada métrica de agregación, plegables en «Solo filas donde…», solo si `hasMetricFilters`: el widget admite más de una métrica) |
| `_style_checkbox`, `_style_text`, `_style_number` | una clave de `style` | `key`, `label` (+ `placeholder` / `min`, `max`, `step`) |

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
get columnFields()        // columnas elegibles: las numéricas con columns_numeric; con columns_scaled, las que se correlacionan (isCorrelatable: numéricas, ordinales y nominales)
```

### Escala de las variables (matriz de correlación)

Con `capabilities.columns_scaled`, cada fila de `_columns.html` muestra la escala de la columna
(`columnScale(field)`, de `schema.scales`, con aviso si es `uncertain`). La escala vive en la
fuente, no en el widget:

- `openScaleEditor(field)` abre el editor (`scaleEditor: {field, scale, order, saving, error}`)
  en el panel de la matriz. `scaleOptions(field)` ofrece continua, discreta, nominal o «no se
  analiza» a una numérica, y nominal, ordinal o «no se analiza» a una categoría. Al pasar a
  ordinal, el orden arranca con el detectado o con los valores de la columna;
  `moveOrderValue(i, ±1)` lo reordena.
- `saveScaleEditor()` → `saveScales(source, {columna: {scale, order?}})`: `PUT
  /api/sources/{id}/scales/`, descarta el schema cacheado y lo recarga.
- «Clasificar con IA» (`classifyScales()`): `POST /api/sources/{id}/scales/ai/` con las
  variables elegidas. `scaleProposal.items` son solo las que cambian, con su motivo;
  `acceptScaleProposal()` las guarda con `saveScales`.
- `_autoPickColumns()` arranca con 4 variables numéricas u ordinales (una nominal suma una fila
  por categoría). Una variable que pasa a «no se analiza» (con el editor o con la IA) sale de
  la matriz.

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

La respuesta (`{widget_type, calculated_fields, column_scales, fields, style}`; `title` va vacío: el título de la tarjeta lo
pone el usuario, la IA no lo genera) se agrega al hilo y **no** toca el borrador.
`adviceSteps(proposal, baseStyle)` la convierte en pasos legibles en el orden del panel
y con el nombre de cada bloque (los campos calculados que propone crear, las escalas que
corrige en la fuente, `columnsLabel`,
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
(`_createCalculatedFields`: `POST /api/sources/{id}/calculated-fields/` y recarga el schema)
y guarda las `column_scales` (`saveScales`), luego copia `fields` al borrador, **suma** el `style` propuesto al actual
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

### Modal «Generar con IA» (`ai-board.js`)

El componente Alpine `aiBoard()` gestiona la creación de un tablero a partir de un pedido:
1. **Plan (`propose()`)**: `POST /api/dashboard/{id}/board-plan/` envía el pedido y la fuente seleccionada. El backend devuelve la lista de widgets ya configurados por la IA y validados, cada uno con título, descripción y su formulario (`fields`, `style`, `calculated_fields`, `column_scales`); los que no se pudieron configurar no llegan. En el paso `'plan'` el usuario revisa la lista, desmarca los que no quiere y cambia los títulos; `note` explica lo que la IA ajustó o quitó.
2. **Creación (`build()`)**: reparte los widgets en filas (`_layoutQueue`), crea en la fuente de una vez los campos calculados que usan las métricas de los widgets marcados (`_calculatedFor`, aunque los defina un widget desmarcado) y luego crea cada widget con `createWidgetFromProposal()` (`_buildOne`, varios a la vez), guardando antes sus `column_scales`. No hay más llamadas a la IA en este paso.

### Chatbot analítico sobre los datos (`data-chat.js`)

Componente Alpine `dataChat()` (`data_chat.html`), solo en la vista compartida (`board_view.html`); el editor no lo carga:
- Se abre mediante un botón flotante en la esquina inferior derecha.
- Envía las consultas a `POST /api/dashboard/{id}/data-chat/` con `{prompt, source_id?}`.
- La respuesta incluye:
  - **Texto explicativo**: Formateado en Markdown y parseado mediante `marked.js` (o fallback nativo).
  - **Gráficos**: Imágenes PNG en Base64 generadas por `matplotlib`/`seaborn` durante la ejecución de código en Gemini Sandbox.
  - **Código ejecutado**: Acordeón desplegable con los bloques de código Python utilizados para derivar los resultados.
- Cada pregunta sube a Gemini solo las columnas que necesita (ver `arquitectura_backend.md` §9.3). Si el navegador corta la espera (`DATA_CHAT_FETCH_TIMEOUT_MS`), muestra un aviso para acotar la pregunta.

### Modal de Insight IA por Widget (`widget_ai_insight_modal.html`)

Componente Alpine `widgetAiInsightModal()`, disponible en el editor y en la vista compartida:
- En cada tarjeta de widget (construida en `base-widget.js`), se renderiza un botón con icono ✨ en la esquina superior izquierda (salvo widgets de tipo `filter`).
- Al hacer clic, dispara el evento global `widget:ai-insight` con `{id, title, type}`.
- El modal se abre y consulta bajo demanda `GET /api/widget/{id}/ai-insight/?filters=...`, enviando los filtros activos del tablero.
- Mientras carga, muestra un indicador animado; al recibir la respuesta, renderiza el análisis en Markdown formateado con `marked.js` (negritas, viñetas y destacados).
- Incluye botón para copiar el texto al portapapeles y botón para cerrar la ventana.

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
- `.tb-widget` con `data-widget-id`, `data-type`; el título es `.tb-widget-title`
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

`renderApexChart(container, options)`: destruye el chart anterior, crea `new ApexCharts` con
`mergeOptions(themeOptions(), options)` y devuelve `.render()`. Los widgets que montan su
propio gráfico (dispersión, matrices, sparkline del KPI) hacen la misma mezcla.

Colores (ver «Tema de los tableros»):
- `themeColors()`: `ChartTheme.read(this.el)`, los colores del tablero ya resueltos.
- `themeOptions()`: opciones base de ApexCharts del tema (paleta, ejes, cuadrícula, leyenda).
- `seriesColors(n)` / `seriesTextColors(n)`: la paleta categórica repetida hasta `n` y el color
  de texto legible sobre cada uno (etiquetas dentro de porciones y barras apiladas).
- `mergeOptions(base, extra)`: mezcla profunda de objetos; arrays y valores los pisa `extra`.

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
- `_compareHTML()`: delta con flecha (`.tb-delta` + `-up` / `-down` / `-flat` según `better`).
- `_targetHTML()`: barra de progreso (`.tb-progress`) con porcentaje; el estado contra la meta
  elige el relleno (`.tb-fill-good` / `-warn` / `-bad`) y el color del valor (`.tb-status-*`).
- `_renderTrend()`: ApexCharts area sparkline (height 48).

### BarWidget

Renderiza: gráfico de barras con apilado, modo horizontal, data labels, líneas de referencia y leyenda arrastrable.

- `_wireLegendDrag()`: SortableJS en la leyenda. Al reordenar, guarda `style.seriesOrder` (lista de nombres de serie) y `draw()` ordena las series desde `style`.
- Colores estables por serie (`_seriesColors` Map, de `seriesColors()`).

### LineWidget

Renderiza: gráfico de líneas con curva suave, markers, data labels y líneas de referencia.

### ScatterWidget

Renderiza: gráfico de dispersión (ApexCharts mixto): una serie `scatter` por grupo de `payload.groups` (color `seriesColors()[i]`) y, con `style.showTrend`, una serie `line` discontinua con la recta de cada grupo en su mismo color (sin color por categoría, la recta va en el color de las referencias). Sin animación (ApexCharts deja a medias el trazo discontinuo). Ejes numéricos con el nombre de cada columna; tooltip con el grupo, su `r` y los dos valores. Pie con las filas dibujadas («Muestra de N de M filas» si el backend muestreó) y la `r` global.

- Leyenda solo con color y solo con los grupos (`customLegendItems`); `_wireLegend()` oculta o muestra a la vez los puntos y la recta del grupo pulsado (listener delegado en el contenedor: ApexCharts rehace la leyenda en cada `toggleSeries`).

### CorrelationWidget

Renderiza: mapa de calor (ApexCharts) de `payload.matrix`, coloreado con
`ChartTheme.divergingScale(el, CUTS)`: los cortes de la escala de Evans (`SCALE`), un neutro
(«muy débil») y un tono por nivel y signo, nueve tramos de −1 a +1. La misma `SCALE` da la
fuerza en palabras del tooltip, y `window.correlationScale()` devuelve sus niveles con sus
colores para «Cómo se lee» del panel. El valor escrito en la celda toma el color de texto de su
tono (`--tb-div-*-on`).
Cada fila es una de `payload.variables`: una variable o una categoría de una nominal (dummy,
con `value` y `group`). El tooltip nombra el par, la fuerza, la medida (`measures[i][j]`:
Pearson o Spearman), las filas usadas y qué significa el 0/1 de una dummy, y avisa de las filas
de una ordinal con valores fuera de su orden (`variables[i].unmatched`). Entre dos categorías de
la misma variable (`same_variable`) dice que no se comparan.

### DonutWidget

Renderiza: gráfico de dona con etiquetas centrales (valor + total), leyenda opcional y modo de labels (valor o porcentaje).

### DynamicTableWidget

Renderiza: tabla dinámica con jerarquía de filas, subtotales por nivel, total general y formatos por columna, con el componente `DataTable` (`utils/data-table.js`).

- `_visibleColumns()` / `_visibleRows()`: columnas y filas de subtotal visibles según los flags de totales del estilo.
- Formato de cada columna: `payload.formats` (el de su métrica o el de la columna en la fuente) y
  `payload.percent`; no hay formato por columna en la tabla.
- `_mountTable(options)`: monta el `DataTable` en la tarjeta y conecta la descarga CSV (la comparte `TableWidget`).
- Tabla plana (sin pivotes, una dimensión): orden por clic en el encabezado; un `style.columnOrder` guardado se respeta.
- Orden de las columnas de los pivotes: `_orderPivots()` reordena cada lista de hermanos
  según `style.pivotOrder` (`{pivots, groups: {ruta del padre en JSON: [valores]}}`; se ignora
  si los pivotes del payload son otros). Cada valor se mueve con su «Total <valor>» (misma ruta
  `pivot`); los valores sin orden guardado van después, en el orden de la hoja, y las columnas
  sin `pivot` no se mueven. En el editor marca cada valor con `move` y la tabla dibuja sus
  flechas; `_movePivot()` lo cambia de lugar con su vecino dentro del grupo, guarda el widget
  (`_saveWidget`, así queda en la vista del tablero), copia el orden al borrador del panel si
  está abierto y redibuja sin pedir datos. En la vista de solo lectura no hay flechas.

**`DataTable`** recibe `columns` (árbol con `children`, `subtotal`, `total`), `rows`, `rowFields`,
`totals`, `formatOf(field)`, `repeatRowLabels`, `paginate`/`pageSize`, `sortable`, `footerNote`
y `onMove(id, dir)`: una columna con `move: {id, left, right}` lleva botones ‹ › en su
encabezado que llaman a `onMove` con -1 o 1.
Cabecera multinivel con `colspan`/`rowspan`; las dimensiones se agrupan con `rowspan` y quedan
fijas al hacer scroll (`position: sticky`, `data-dim`); el «Total general» va en el `<tfoot>`.
`csv()` devuelve `{headers, rows}` para `downloadRowsAsCSV`. Estilos: `.tb-table` en
`tablero-theme.css`, con las variables `--tb-table-*`. Las celdas de progreso llevan
`.tb-progress` con el relleno por tercio (`DataTable.PROGRESS.fills`).

### TableWidget

Extiende `DynamicTableWidget`. Tabla simple sin jerarquía (`DataTable` con `rowFields: []`): columnas planas, orden por clic, paginación, formatos por columna y aviso de filas truncadas.

### FilterWidget

Barra de filtros del tablero. `placement: 'header'`, singleton. Usa Virtual Select para multi-select. Al cambiar, llama `store.setBoardFilter()`.

## Fuentes de datos

Cada tablero tiene una o varias fuentes (una pestaña de una hoja de Google); cada widget
elige la suya en su panel. Se gestionan en el bottom sheet «Fuentes de datos».

**Gestor** (`source-manager.js`, `source_manager.html`): tabla de fuentes con su nombre (el
propio, con «Documento · Pestaña» debajo, o el original), columnas incluidas («7 de 9
incluidas», contando la columna de sistema `ID` igual que el editor), widgets que la usan («Sin usar» si ninguno) y «Actualizado hace X»
(`timeAgoLabel`).
Al cargar, pide el **estado** de cada fuente (`GET …/status/`, en paralelo) y marca las que
tienen problema («Sin acceso», «Pestaña no encontrada»). Acciones por fuente:

- **Actualizar** → `POST …/refresh/` (como «Actualizar datos» del selector, sin abrirlo).
- **Cambiar fuente** → selector en `edit` con `changeSheet` (pasa a `replace` cuando
  cargan las columnas guardadas).
- **Abrir origen** → la hoja de Google en la pestaña de la fuente (`#gid=`), en otra pestaña.
- **Ver** → `viewData`: los datos de la fuente en una tabla (Tabulator) con carga progresiva
  (`progressiveLoad: 'scroll'`): pide `GET …/rows/?page&size&sort&dir` de 100 en 100 filas y,
  al llegar con el scroll al final, suma el bloque siguiente a los ya cargados. El orden es
  remoto y reinicia la carga. El encabezado muestra «Mostrando N de M filas» (`loadedRows`).
- **Editar** → selector en modo `edit` (columnas y campos calculados).
- **Eliminar** → `DELETE ?dry_run=1` lista los widgets que se quedan sin datos y pide
  confirmación.

**Selector** (`source-picker.js`, `source_picker.html`), modos `create` (tablero nuevo),
`add`, `edit` y `replace`. Pasos: fuente → documento de Drive → pestaña → columnas. En el paso
de columnas:

- «Usar la primera fila como encabezado» (`?headers=0|1`): sin ella, la fila 1 es un dato y
  las columnas se llaman «Columna A», «Columna B»…
- **Buscar columna…** (`columnFilter`): filtra las filas de la tabla por el nombre de la
  columna, sin mayúsculas ni acentos (`matchesColumn`). Oculta las filas con `x-show`, sin
  quitarlas del `x-for`. Con el buscador activo, la casilla «Incluir» del encabezado actúa
  solo sobre las columnas a la vista (`shownColumns`).
- El ancho de la columna «Columna» se ajusta arrastrando el borde derecho de su encabezado
  (`startNameColumnResize`; flechas con el teclado, doble clic o Inicio para volver al
  original). Va en la variable CSS `--name-column-width` de la tabla y se recuerda en
  `localStorage`.
- Por columna: incluir, **nombre a mostrar** (reemplaza al encabezado en todo el tablero) y tipo.
- En `edit`, pestaña **Campos calculados**: los campos como fichas (el elegido se arma abajo),
  nombre, el tipo que decide la fórmula («Por fila» / «Agregado») y, en los agregados, formato
  Número o Porcentaje. La fórmula se **arma con bloques** (ver abajo); con cada cambio,
  `previewCalculated` pide a `POST /api/sources/{id}/formula/` los primeros valores (o el total
  de la hoja) o el error, con lo que hay en el editor sin guardar. El ícono de ayuda abre
  el tópico `campos-calculados` del centro de ayuda: los dos tipos con ejemplos, cuál elegir,
  cómo calcula cada función y cómo se arma.

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

- **Panel izquierdo** (`aside[data-fb-trash]`), con todas las piezas, de arriba abajo:
  *Columnas* (`builderColumns`: las incluidas con su nombre a mostrar y los campos por fila
  anteriores, con buscador; la lista tiene su propio scroll y poca altura para que una hoja
  con muchas columnas no empuje fuera de la vista a las secciones de abajo); su subpanel
  *Valores* (Número/Texto → `valuePiece`, y la ficha «Vacío» → `EMPTY_PIECE`, que deja la
  celda en blanco y solo vale como resultado de un SI: el servidor rechaza cualquier otro
  lugar con su mensaje); *Operadores* (`+ − × ÷`); *Condición* (SI, comparación, Y, O, NO);
  *Agregaciones* (SUM, AVG, COUNT, COUNT_DISTINCT, MIN, MAX), *Texto* (CONCATENAR, MINÚSCULAS, MAYÚSCULAS, QUITAR
  ESPACIOS, CONTIENE) y *Fecha* (AÑO, MES, NOMBRE DEL MES, TRIMESTRE, DÍA, DÍA DE LA SEMANA,
  AÑO-MES, DIFERENCIA DE FECHAS, HOY). Los grupos salen del catálogo (`FUNCTION_GROUPS`, por el
  campo `group` de cada función). El panel tiene scroll propio. Una función sin huecos (HOY) se
  dibuja como `HOY()`.
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

En la vista compartida, un indicador de carga de la página (`#page-loader`) está visible mientras hay algún request a `/render/` en curso: la carga inicial y cada refresco por filtros. Lo controla `setPageLoading()` en `board-view-init.js`, que cuenta los requests pendientes.

## Drag and drop

Tres instancias de SortableJS:

1. **Palette → Canvas**: `pull: 'clone'`, `put: false`. Al soltar, crea el widget y abre el drawer.
2. **Canvas**: reordena widgets. `onEnd` → `reorderWidgets()` → `scheduleLayoutSave()`.
3. **Listas del drawer**: reordena columnas/dimensiones/pivotes/métricas. `onEnd` → actualiza el array del draft.

Todas usan `forceFallback: true` + `fallbackOnBody: true` para arrastre consistente entre contenedores.

## Centro de ayuda (`help-panel.js`, `help/`)

La ayuda del editor es un panel a la derecha del lienzo (`help/_panel.html`), en el lugar del
panel de edición. Se abre desde el menú del tablero › **Ayuda** o desde los botones
<kbd>?</kbd> del panel del widget y del editor de la fuente.

- **Tópicos**: `sheets_reports/help.py` los lista en orden (`SECTIONS` y `HELP_TOPICS`: slug,
  sección, título y palabras para el buscador). Cada uno es `templates/sheets_reports/help/<slug>.html`.
  Para agregar uno: una entrada en `HELP_TOPICS` y su partial. El índice, «Anterior / Siguiente»
  y el buscador salen solos de ese orden.
- **Enlaces**: en el contenido, `<a href="#help:<slug>">` o `#help:<slug>/<id>` (a una parte del
  tópico, por su `id`). Desde un botón, `$store.help.open('<slug>')`. Un manejador delegado
  intercepta los enlaces, así que el contenido es HTML sin JS. `test_help` falla si alguno apunta
  a un tópico que no existe.
- **Store** `Alpine.store('help')`:
  - `open(slug, anchor)`, `back()` (historial), `showIndex()`, `close()`;
  - `query` y `matches(topic)`: el buscador sobre título, palabras clave y texto, sin tildes;
  - `prev` / `next`.
- **Ancho**: arrastrando el borde izquierdo (o con las flechas, con el borde enfocado), entre
  320 y 960 px, sin dejar el lienzo por debajo de 360 px; doble clic vuelve a 480. Se recuerda
  en `localStorage['tableia:help-width']`. Al soltar se dispara `resize` para que los gráficos
  vuelvan a medirse.
- **Con el panel de edición**: abrir la ayuda oculta el panel del widget sin cerrarlo (su
  borrador queda); al cerrarla vuelve tal como estaba. Abrir el panel de otro widget
  (`openDrawer`) cierra la ayuda.
- **Estilo del contenido**: `text-sm`, títulos `h4` y tablas como en los tópicos existentes; los
  enlaces `#help:` se ven subrayados.
- **Videos**: `HELP_VIDEOS` (en `help.py`) asocia un tópico a su video de demostración
  (`static/sheets_reports/help/videos/<nombre>.webm` y su portada `.png`) y su duración.
  - El tópico muestra **Ver video**: un `<video preload="none">` que se descarga recién al reproducirlo.
  - Cambiar de tópico o cerrar la ayuda lo pausa.
  - En el índice, `ti-movie` marca los tópicos con video.
  - Los videos los graba `tests/e2e/demo_videos.py` (`DEMO=1`, ver `docs/tests.md`). Para sumar uno: un
    `test_NN_*` nuevo en ese archivo y su entrada en `HELP_VIDEOS`. `test_help` verifica que
    existan el `.webm` y el `.png`.

## Design system

Dos hojas de estilo con variables propias: la de la **app** (todo lo que rodea al tablero) y la
de los **tableros** (todo lo que va dentro del lienzo). Hoy el tablero hereda los valores de la
app, pero sus variables son independientes: un tablero puede cambiar las suyas sin tocar la app.

- **Tailwind CSS** vía CDN (sin build). `tailwind.config` (en `base.html`) solo da nombres que
  apuntan a variables: colores `primary`, `accent`, `app`, `surface`, `line`, `fg`, `heading`,
  `overlay`, `scrim`, `success`, `danger`, `warning`; la escala `fontSize` (`xs`…`xl`),
  `borderRadius`, `boxShadow` y `tracking-label`. Ningún valor vive en la config.
- **Tabler Icons** (`ti ti-*`) y la fuente de la marca (Google Fonts) en `base.html`.
- Orden de carga: `tokens.css` → CDN de Tailwind → `_app_ui.html` → (editor y vista)
  CSS de Tabulator → `app-tabulator.css` → `tablero-theme.css`; ApexCharts → `chart-theme.js`
  → widgets. Los CSS de cada página van en `{% block extra_head %}`.

Reglas:
- Ningún color fuera de `tokens.css` y `tablero-theme.css`: ni en plantillas, ni en JS, ni en
  `style=""`, ni en clases arbitrarias. Las excepciones son los logos de terceros (Google) y el
  `style` con las variables del tablero (`_board_theme_attrs.html`).
- Si falta un color o un tamaño se agrega como token, de abajo hacia arriba.
- Los modificadores de opacidad (`bg-primary/50`) no funcionan con estas variables: una
  transparencia es un token (`--color-overlay`, `--color-scrim`).
- Nombres: `--{componente o categoría}-{parte}-{variante}-{estado}`.

### Tokens de la app (`tokens.css`)

Tres capas en `:root`; plantillas y componentes nunca usan la primera:
1. **Marca** (`--brand-*`, `--neutral-*`, `--status-*`): los valores.
2. **Semánticos** (`--color-*`, `--fs-*`, `--fw-*`, `--radius-*`, `--shadow-*`): el rol.
3. **Componentes** (`--header-*`, `--sidebar-*`, `--btn-*`, `--table-*`, `--tab-*`, `--input-*`,
   `--dropzone-*`): lo que leen `_app_ui.html`, `app-tabulator.css` y `board-editor.css`.

Contraste: el texto sobre el acento usa `--color-on-accent`, nunca el blanco; el texto con el
color del acento sobre fondo claro usa `--color-accent-text`. `--color-text-subtle` es para
placeholders, deshabilitados e íconos, no para texto que haya que leer.

### Tema de los tableros (`tablero-theme.css`, `chart-theme.js`)

El contenedor del tablero lleva `class="tablero"` y los atributos de
`_board_theme_attrs.html`: en el editor `#board-scroll` (filtros + lienzo), en la vista
`#tab-charts`. La muestra del tema en Configuración es otro `.tablero` con los mismos atributos.
Prioridad, de menor a mayor:
1. Valores por defecto en `.tablero` (heredan de `tokens.css`).
2. Preset: `.tablero[data-board-theme="…"]` (`Dashboard.theme`).
3. En línea: `style="--tb-…"` (`Dashboard.style_variables`).

Dentro del lienzo solo se usan variables `--tb-*` y clases `.tb-*`; las utilidades de layout de
Tailwind sí. Los controles de edición de la tarjeta (asa, editar, borrar, IA, descargar) y la
regla y las filas del editor son de la app y usan sus clases.

| Grupo | Variables | Clases |
|---|---|---|
| Tarjeta y texto | `--tb-card-*`, `--tb-title-text`, `--tb-text*`, `--tb-surface-muted`, `--tb-scrim`, `--tb-link` | `.tb-widget`, `.tb-widget-title`, `.tb-text`, `.tb-text-muted`, `.tb-text-subtle`, `.tb-text-bad`, `.tb-link`, `.tb-surface-muted`, `.tb-border`, `.tb-track`, `.tb-scrim`, `.tb-swatch`, `.tb-spinner`, `.tb-retry` |
| Estados | `--tb-status-good/warn/bad(-bg)` | `.tb-status-*`, `.tb-fill-*` |
| KPI | `--tb-kpi-*` | `.tb-kpi-value`, `.tb-kpi-label`, `.tb-kpi-sub`, `.tb-delta-*`, `.tb-progress` |
| Tablas y ranking | `--tb-table-*`, `--tb-bar-*` | `.tb-table` (y sus partes), `.tb-pager`, `.tb-bar`, `.tb-bar-lead`, `.tb-rank-pos` |
| Filtros | `--tb-control-*` | VirtualSelect dentro de `.tablero` |
| Gráficos | `--tb-chart-1…6` (+ `-on`), `--tb-chart-ref(-text)`, `--tb-chart-stroke`, `--tb-seq-1…5` (+ `-on`), `--tb-div-*`, `--tb-chart-axis/grid/label` | — |

`chart-theme.js` expone `window.ChartTheme`. ApexCharts necesita colores resueltos (calcula
tonos y rangos a partir de ellos), así que `read(el)` lee con `getComputedStyle` las variables
del `.tablero` que contiene a `el` (o el primero de la página). No hay opciones globales
(`window.Apex`): cada gráfico las pide al armarse.
- `read(el)`: paleta categórica y sus colores de texto, secuencial, divergente, ejes, referencia,
  tarjeta, texto, pista.
- `color(name, el)`: un token suelto, resuelto.
- `baseOptions(el)`: opciones base de ApexCharts (paleta, fuente, ejes, cuadrícula, leyenda).
- `sequentialScale(min, max, el)` / `divergingScale(el, cuts?)`: rangos de `colorScale` para
  mapas de calor. La divergente tiene un neutro y cuatro tonos por signo; sus tonos positivos
  son los de la secuencial (`--tb-seq-2…5`), así el tono más fuerte de la matriz de correlación
  es el mismo que el de la V de Cramér. La V de Cramér usa los tonos secuenciales con sus
  propios cortes de fuerza (`window.cramerScale()`).
- `refresh(el, charts)`: reaplica las opciones a gráficos existentes si cambian las variables.

La miniatura de plantillas (`generateRealBoardThumbnail`) es un SVG que se muestra como
`<img>` y no ve las variables CSS: toma los colores de `ChartTheme.read()` y se los pasa a cada
widget en `ctx.theme`, `ctx.colors` y `ctx.primary`.

### Estilos de la app (`ui-*`)

Las pantallas de la app usan los componentes de `templates/sheets_reports/_app_ui.html`, no
cadenas de utilidades sueltas. El tablero y los widgets no los usan.

El archivo es un `<style type="text/tailwindcss">` con `@layer base` y `@layer components`
incluido en el `<head>` de `base.html`, que el CDN de Tailwind procesa sin build. No es un
`.css` aparte porque el preflight de Tailwind (`[type=button] { background: transparent }`) le
ganaría a los fondos de los botones. Al quedar en la capa `components`, una utilidad suelta
(`w-64`, `hover:text-danger`) ajusta un caso puntual. Los colores salen de las variables de
componente de `tokens.css`.

| Pieza | Clases |
|---|---|
| Botón | `ui-btn` + `ui-btn-primary` / `-secondary` / `-dark` / `-ghost` / `-danger` / `-danger-outline` / `-icon`; tamaños `ui-btn-sm`, `ui-btn-xs` |
| Header y navegación | `ui-app-header` (variante `ui-app-header-dark`: header de la vista del tablero), `ui-sidebar`, `ui-nav-item` (enlace con el aspecto de `ui-btn-secondary`; activo con `.is-active` o `aria-current`), `ui-sidebar-label`, `ui-module-card` + `ui-module-icon` |
| Campo | `ui-label`, `ui-help`, `ui-input` (también para `<select>`), `ui-input-sm`, `ui-textarea`, `ui-check` |
| Contenedor | `ui-card`, `ui-card-title`, `ui-eyebrow` (rótulo en mayúsculas), `ui-code-chip` |
| Aviso | `ui-alert` + `ui-alert-error` / `-warn` / `-info` |
| Badge | `ui-badge` + `ui-badge-neutral` / `-success` / `-danger` / `-warn` / `-accent` (`-info` es un alias) |
| Modal | `ui-modal-backdrop` > `ui-modal` > `ui-modal-header` (`ui-modal-title`), `ui-modal-body`, `ui-modal-footer` |
| Panel inferior | `ui-panel`, `ui-panel-header`, `ui-panel-title`, `ui-danger-zone` |
| Pestañas | `ui-tabs` > `ui-tab`, activa con `aria-selected="true"` (o `.is-active`) |
| Tabla HTML | `ui-table`: encabezado fijo, rótulos y celdas ya espaciados |
| Otros | `ui-page-loader` (barra de carga), `ui-dot-grid` (fondo del lienzo del editor y del login), `.vselect` |

Jerarquía de botones:
- `ui-btn-primary`: una acción principal por zona («Generar con IA», «Crear tablero»,
  «Guardar», «Editar» en la vista de una plantilla).
- `ui-btn-secondary`: «Gestionar fuentes», «Vista previa», «Compartir» y acciones de apoyo.
- `ui-btn-dark`: una acción fuerte que no es la principal («Guardar igual» ante el impacto de
  un cambio de columnas).
- `ui-btn-ghost` / `ui-btn-icon`: cerrar, editar, duplicar y eliminar en filas.
- `ui-btn-danger`: solo «Eliminar tablero definitivamente» y confirmaciones destructivas.

Tabulator solo se usa en la app (`app-tabulator.css`); el listado del home lleva además
`app-tabulator-list` (filas más altas). Las tablas de los widgets son `DataTable`, no Tabulator.

## Selects con buscador (`x-vselect`)

Los `<select>` del editor llevan la directiva `x-vselect` (`vselect.js`), que monta un
VirtualSelect con caja de búsqueda en su lugar. El select nativo sigue en el DOM, oculto
(`.vselect-native`), como fuente de verdad: Alpine maneja sus opciones (`x-for`), su valor,
`disabled` y `x-show`, y la directiva los refleja. Al elegir, pone el valor en el select y
despacha `input` y `change`, así que `x-model` y `x-on:change` funcionan igual que antes.

Un select nuevo solo necesita el atributo:

```html
<select x-vselect x-model="c.type">…</select>
<select x-vselect="metric.format || ''" x-bind:value="metric.format || ''" x-on:change="…">…</select>
```

Con `x-bind:value` (sin `x-model`) la directiva lleva la misma expresión, que es lo que le
avisa de que el valor cambió. La opción de valor vacío («Sin orden», «Todas las filas») viaja
con un valor centinela, porque VirtualSelect trata el vacío como «nada elegido». Los estilos
(`.vselect`) están en `_app_ui.html`.

La excepción es la tabla de columnas de la fuente (`source_picker.html`): sus selects de
«Tipo» y «Formato», uno por columna de la hoja, son nativos.

## Envío de correos

El envío de correos está documentado en [`configuracion_correos.md`](configuracion_correos.md).

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
