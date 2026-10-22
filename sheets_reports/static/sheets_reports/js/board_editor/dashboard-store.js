const AI_FETCH_TIMEOUT_MS = 120000;
const RENDER_FETCH_TIMEOUT_MS = 60000;
const LAYOUT_SAVE_DELAY_MS = 600;

async function fetchJsonSafe(url, options = {}, timeoutMs = AI_FETCH_TIMEOUT_MS) {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const r = await fetch(url, { ...options, signal: controller.signal });
    const data = await r.json().catch(() => null);
    return { r, data };
  } finally {
    clearTimeout(timeoutId);
  }
}

// Barra de carga superior (_page_loader.html): visible desde el primer pintado, se oculta cuando
// no queda ningún request del tablero en curso.
let pendingLoads = 1;
function setPageLoading(isLoading) {
  pendingLoads = Math.max(0, pendingLoads + (isLoading ? 1 : -1));
  const bar = document.getElementById('page-loader');
  if (bar) bar.classList.toggle('hidden', pendingLoads === 0);
}

// `window.apiUrl` lo define base.html con el prefijo de despliegue (SCRIPT_NAME).

const EMPTY_FIELDS = () => ({
  dimensions: [],
  pivots: [],
  metrics: [],
  filters: [],
  columns: [],
  trend_by: '',
  sort_by: null,
  limit: null,
});

// Operadores del motor de filtros (engine/steps/filter.py) con su etiqueta para el editor.
const FILTER_OPS = [
  { value: 'eq', label: 'es igual a', needsValue: true },
  { value: 'ne', label: 'es distinto de', needsValue: true },
  { value: 'lt', label: 'es menor que', needsValue: true },
  { value: 'lte', label: 'es menor o igual que', needsValue: true },
  { value: 'gt', label: 'es mayor que', needsValue: true },
  { value: 'gte', label: 'es mayor o igual que', needsValue: true },
  { value: 'in', label: 'está en', needsValue: true, isList: true },
  { value: 'not_in', label: 'no está en', needsValue: true, isList: true },
  { value: 'between', label: 'entre', needsValue: true, isRange: true },
  { value: 'contains', label: 'contiene', needsValue: true },
  { value: 'is_empty', label: 'está vacío', needsValue: false },
  { value: 'not_empty', label: 'no está vacío', needsValue: false },
];

const RELATIVE_VALUES = [
  { value: '', label: 'Un valor…' },
  { value: 'current_year', label: 'Este año' },
  { value: 'previous_year', label: 'El año anterior' },
  { value: 'current_month', label: 'Este mes' },
  { value: 'previous_month', label: 'El mes anterior' },
  // Periodos de los datos (`data`): solo en columnas de tiempo (schema.time_fields).
  { value: 'latest', label: 'El periodo más reciente', data: true },
  { value: 'previous', label: 'El periodo anterior', data: true },
  { value: 'earliest', label: 'El periodo más antiguo', data: true },
];

const AGG_OPTIONS = [
  { value: 'sum', label: 'Suma' },
  { value: 'avg', label: 'Promedio' },
  { value: 'median', label: 'Mediana' },
  { value: 'min', label: 'Mínimo' },
  { value: 'max', label: 'Máximo' },
  { value: 'std', label: 'Desviación estándar' },
  { value: 'count', label: 'Conteo' },
  { value: 'count_distinct', label: 'Valores distintos' },
];

// «Mostrar como» de una métrica (`metric.window`): cómo se muestra cada valor respecto a los demás.
// `pivotLabel`: el texto con pivotes, donde el % del total (por columna) se distingue del de la fila.
// `chartBy`: en un gráfico con pivote no se ven columnas ni filas; el % se nombra con la columna
// que reparte («% del total de cada Sexo»: el pivote, cada serie; la dimensión, cada categoría).
const WINDOW_OPTIONS = [
  { value: 'percent_of_total', label: '% del total', pivotLabel: '% del total de la columna', chartBy: 'pivots' },
  { value: 'percent_of_row', label: '% del total de la fila', chartBy: 'dimensions' },
  { value: 'running_total', label: 'Acumulado' },
  { value: 'pct_change', label: 'Variación vs anterior' },
];

// Formato de una métrica (`metric.format`, METRIC_FORMATS en engine/steps/aggregation.py).
// «Automático» hereda el de la columna en la fuente o el del campo calculado.
const METRIC_FORMAT_OPTIONS = [
  { value: '', label: 'automático' },
  { value: 'number', label: 'número' },
  { value: 'currency', label: 'moneda' },
  { value: 'percent', label: 'porcentaje (%)' },
  { value: 'progress', label: 'barra (0-100)' },
];

// Las reglas de qué se puede elegir NO van aquí: las manda el servidor en
// `widget_manifest[tipo].panel_options` (services/ai_spec.panel_options), calculadas con la
// misma validación que aplica al guardar. Aquí solo quedan los textos de cada opción.

let CONDITION_SEQ = 0;

// Nombre visible sin «Nombre a mostrar»: el mismo texto que arma el backend (`humanize`).
// Escalas de medida de una columna (utils/scales.py): de ellas depende cómo la matriz de
// correlación mide cada par.
const SCALE_LABELS = {
  continuous: 'Continua',
  discrete: 'Discreta',
  ordinal: 'Ordinal',
  nominal: 'Nominal',
  none: 'No se analiza',
};

const DEFAULT_IGNORED_VALUES = 'No aplica, N/A, No corresponde, No utilizo este medio, No se utiliza, No he recibido este servicio, Sin respuesta';

function humanizeName(name) {
  const text = String(name || '').replace(/_/g, ' ').trim();
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : '';
}

function opMeta(value) {
  return FILTER_OPS.find(o => o.value === value) || FILTER_OPS[0];
}

// --- condición: lo que se edita ({_k, field, op, mode, value, value2, relative}) →
// la forma plana que valida el backend ({field, op, value} | {field, op, relative}).
function conditionToPayload(c, numericFields) {
  const numeric = numericFields.includes(c.field);
  const coerce = (raw) => {
    const text = String(raw ?? '').trim();
    if (text === '') return '';
    return numeric && !Number.isNaN(Number(text)) ? Number(text) : text;
  };

  if (c.mode === 'relative' && c.relative) return { field: c.field, op: c.op, relative: c.relative };

  const meta = opMeta(c.op);
  if (!meta.needsValue) return { field: c.field, op: c.op };

  if (meta.isRange) {
    return { field: c.field, op: c.op, value: [coerce(c.value), coerce(c.value2)] };
  }
  if (meta.isList) {
    const items = String(c.value ?? '').split(',').map(v => v.trim()).filter(v => v !== '');
    return { field: c.field, op: c.op, value: items.map(v => (numeric && !Number.isNaN(Number(v)) ? Number(v) : v)) };
  }
  return { field: c.field, op: c.op, value: coerce(c.value) };
}

function conditionFromPayload(c) {
  const base = { _k: ++CONDITION_SEQ, field: c.field || '', op: c.op || 'eq', mode: 'value', value: '', value2: '', relative: '' };
  if (c.relative) {
    base.mode = 'relative';
    base.relative = c.relative;
    return base;
  }
  const value = c.value;
  if (Array.isArray(value)) {
    base.value = String(value[0] ?? '');
    base.value2 = String(value[1] ?? '');
  } else if (value != null) {
    base.value = String(value);
  }
  return base;
}

document.addEventListener('alpine:init', () => {
  Alpine.store('dashboard', {
    widgets: [],
    // El tablero ya se pidió al servidor: hasta entonces el lienzo vacío no es «vacío».
    boardLoaded: false,
    editingId: null,
    editingType: null,
    dashboardId: window.DASHBOARD_ID,
    drawerTab: 'data',
    // Fuentes de datos del tablero ([{id, label}]) y el schema de cada una, pedido al abrir el
    // panel de un widget. `schema` es siempre el de la fuente del widget que se edita.
    sources: [],
    schemas: {},
    schema: { all_fields: [], numeric_fields: [], dimension_fields: [], time_fields: [], calculated_fields: [], aggregated_fields: [], sample_values: {} },
    widgetManifest: window.WIDGET_MANIFEST || {},
    schemaError: '',
    _nextId: -1,
    listSortables: {},

    // Las virtuales de las cuadrículas de un formulario («P · Aspecto», «P · Respuesta») salen de
    // las listas de columnas y quedan en `grid_columns`: el panel las ofrece en su propio grupo
    // y solo en los widgets que las aceptan (`grid_fields` del manifiesto). `numeric_fields` las
    // conserva: una Respuesta numérica se promedia.
    _withGridColumns(schema) {
      const names = (schema.grid_fields || []).flatMap(g => [g.aspect, g.value]).filter(Boolean);
      const virtual = new Set(names);
      const real = list => (list || []).filter(f => !virtual.has(f));
      return { ...schema, grid_columns: names, all_fields: real(schema.all_fields),
               dimension_fields: real(schema.dimension_fields) };
    },

    // Schema de una fuente (cacheado por id). Lo deja en `schema` para el panel.
    async loadSchema(sourceId) {
      const empty = { all_fields: [], numeric_fields: [], dimension_fields: [], time_fields: [], calculated_fields: [], aggregated_fields: [], sample_values: {} };
      if (!sourceId) {
        this.schema = empty;
        this.schemaError = 'Elige la fuente de datos del widget.';
        return;
      }
      if (this.schemas[sourceId]) {
        this.schema = this.schemas[sourceId];
        this.schemaError = '';
        return;
      }
      this.schema = empty;
      try {
        const r = await fetch(apiUrl(`/api/sources/${sourceId}/schema/`));
        const data = await r.json().catch(() => null);
        if (r.ok && data) {
          const { widget_manifest: manifest, ...raw } = data;
          const schema = this._withGridColumns(raw);
          this.schemas[sourceId] = schema;
          this.schema = schema;
          if (manifest) {
            window.WIDGET_MANIFEST = manifest;
            this.widgetManifest = manifest;
          }
          this.schemaError = '';
        } else {
          this.schemaError = (data && data.error) || 'No se pudieron leer las columnas de la hoja.';
        }
      } catch (e) {
        this.schemaError = 'No se pudo conectar con el servidor.';
      }
    },

    // Tras agregar, editar o eliminar fuentes: otras columnas, otros schemas.
    resetSources(sources) {
      if (sources) this.sources = sources;
      this.schemas = {};
      this.assistantSuggestions = {};
    },

    _renderUrl() {
      const url = apiUrl(`/api/dashboard/${this.dashboardId}/render/`);
      const qs = this.getFilterQueryString ? this.getFilterQueryString() : '';
      return qs ? `${url}?${qs}` : url;
    },

    _reportFilterErrors(data) {
      const errors = (data && data.filter_errors) || [];
      this._reportedFilterErrors = this._reportedFilterErrors || new Set();
      errors.filter(e => !this._reportedFilterErrors.has(e)).forEach(e => {
        this._reportedFilterErrors.add(e);
        if (typeof window.showToast === 'function') window.showToast(e);
      });
    },

    normalizeWidget(w) {
      const pos = w.position ? { ...w.position } : { order: 0, w: 6, h: 300, row_id: 'row_1' };
      delete pos.x;
      delete pos.y;
      return {
        id: w.id,
        type: w.type,
        source_id: w.source_id ?? null,
        title: w.title || 'Nuevo Widget',
        position: pos,
        fields: { ...EMPTY_FIELDS(), ...(w.fields || {}) },
        style: w.style || {},
        data: w.data || null,
        error: w.error || null,
        _dirty: false,
        _loading: false,
        el: null,
        _chart: null,
      };
    },

    async loadBoard() {
      let r, data;
      try {
        ({ r, data } = await fetchJsonSafe(this._renderUrl(), {}, RENDER_FETCH_TIMEOUT_MS));
      } finally {
        setPageLoading(false);
      }
      if (!r.ok || !data) throw new Error((data && data.error) || 'No se pudo cargar el tablero');
      this._reportFilterErrors(data);
      this.sources = (data.dashboard && data.dashboard.sources) || [];
      // Instancias reales (mount/applyRender/setLoading), no objetos planos.
      this.widgets = data.widgets
        .map(w => BaseWidget.fromServer(this.normalizeWidget(w)))
        .filter(Boolean)
        .sort((a, b) => (a.position?.order ?? 0) - (b.position?.order ?? 0));
      this._initRowsFromWidgets();
      this.boardLoaded = true;
      return Object.fromEntries(data.widgets.map(w => [w.id, w]));
    },

    async refreshData() {
      const saved = this.widgets.filter(w => w.id > 0 && w.fields);
      saved.forEach(w => w.setLoading(true));
      setPageLoading(true);
      try {
        const { r, data } = await fetchJsonSafe(this._renderUrl(), {}, RENDER_FETCH_TIMEOUT_MS);
        if (!r.ok || !data) {
          const message = (data && data.error) || `Error ${r.status} al cargar los datos`;
          saved.forEach(w => { w.setLoading(false); w.renderError(message, { retryable: true }); });
          return;
        }
        this._reportFilterErrors(data);
        this.sources = (data.dashboard && data.dashboard.sources) || this.sources;
        const byId = Object.fromEntries(data.widgets.map(w => [w.id, w]));
        saved.forEach(w => {
          const entry = byId[w.id];
          // El servidor puede reescribir un widget (ej. una columna renombrada en su fuente):
          // sin cambios locales pendientes, manda lo guardado.
          if (entry && !w._dirty) {
            w.fields = entry.fields;
            w.style = entry.style;
            w.source_id = entry.source_id;
          }
          w.applyRender(entry);
        });
      } catch (e) {
        saved.forEach(w => { w.setLoading(false); w.renderError('No se pudo conectar con el servidor', { retryable: true }); });
      } finally {
        setPageLoading(false);
      }
    },

    rows: [],
    _nextRowSeq: 1,

    get canvasWidgets() {
      return this.widgets.filter(w => w.constructor?.placement !== 'header');
    },
    get hasCanvasWidgets() {
      return this.canvasWidgets.length > 0;
    },

    getRowWidgets(rowId) {
      return this.widgets.filter(w => (w.position?.row_id || 'row_1') === rowId && w.constructor?.placement !== 'header');
    },

    getRowWidth(rowId, excludeWidgetId = null) {
      return this.getRowWidgets(rowId)
        .filter(w => w.id !== excludeWidgetId)
        .reduce((sum, w) => {
          const wUnits = typeof w.position?.w === 'number' ? w.position.w : 6;
          return sum + wUnits;
        }, 0);
    },

    canAddWidgetToRow(rowId, widgetWidth, excludeWidgetId = null) {
      const currentWidth = this.getRowWidth(rowId, excludeWidgetId);
      return (currentWidth + widgetWidth) <= 12;
    },

    addRowAtTop() {
      const newRowId = `row_${this._nextRowSeq++}`;
      this.rows.unshift({ id: newRowId });
      return newRowId;
    },

    addRowBelow(targetRowId) {
      const newRowId = `row_${this._nextRowSeq++}`;
      const index = targetRowId ? this.rows.findIndex(r => r.id === targetRowId) : -1;
      if (index >= 0) {
        this.rows.splice(index + 1, 0, { id: newRowId });
      } else {
        this.rows.push({ id: newRowId });
      }
      return newRowId;
    },

    removeRow(rowId) {
      const widgets = this.getRowWidgets(rowId);
      // La única fila no se quita: el lienzo siempre tiene dónde soltar un widget.
      if (widgets.length === 0 && this.rows.length > 1) {
        this.rows = this.rows.filter(r => r.id !== rowId);
      }
    },

    _initRowsFromWidgets() {
      const canvasWidgets = this.widgets.filter(w => w.constructor?.placement !== 'header');
      // Tablero vacío: la primera fila ya está a la vista para empezar a añadir widgets.
      if (!canvasWidgets.length) {
        this.rows = [{ id: 'row_1' }];
        this._nextRowSeq = 2;
        return;
      }

      const existingRowIds = [];
      canvasWidgets.forEach(w => {
        if (w.position && w.position.row_id && !existingRowIds.includes(w.position.row_id)) {
          existingRowIds.push(w.position.row_id);
        }
      });

      if (existingRowIds.length > 0) {
        this.rows = existingRowIds.map(id => ({ id }));
        this._nextRowSeq = Math.max(...existingRowIds.map(id => parseInt(String(id).replace(/\D/g, ''), 10) || 1)) + 1;
        return;
      }

      const rows = [];
      let currentRowId = 'row_1';
      let currentSum = 0;
      rows.push({ id: currentRowId });

      canvasWidgets.forEach(w => {
        const wUnits = typeof w.position?.w === 'number' ? w.position.w : 6;
        if (currentSum + wUnits > 12) {
          currentRowId = `row_${rows.length + 1}`;
          rows.push({ id: currentRowId });
          currentSum = 0;
        }
        w.position = { ...(w.position || {}), row_id: currentRowId, w: wUnits };
        currentSum += wUnits;
      });

      this.rows = rows;
      this._nextRowSeq = rows.length + 1;
    },

    addOrOpenFilter() {
      const existing = this.widgets.find(w => w.type === 'filter' || w.chart_type === 'filter');
      if (existing) {
        this.openDrawer(existing.id);
        return existing;
      }
      const w = this.addWidget('filter');
      const container = containerFor(w);
      if (container) {
        const chipBtn = container.querySelector('#add-filter-chip-btn');
        const widgetEl = w.mount();
        if (chipBtn) {
          container.insertBefore(widgetEl, chipBtn);
        } else {
          container.appendChild(widgetEl);
        }
      }
      this.openDrawer(w.id);
      return w;
    },

    addWidget(type, targetRowId = null) {
      const manifest = this.widgetManifest[type] || {};
      const WidgetClass = WidgetRegistry.get(type);
      const defaultWStr = WidgetClass?.defaults?.width || 'md:col-span-6';
      const defaultW = parseInt(defaultWStr.match(/col-span-(\d+)/)?.[1] || '6', 10);

      let destRowId = targetRowId;
      if (WidgetClass?.placement !== 'header' && !destRowId) {
        const availableRow = this.rows.find(r => this.canAddWidgetToRow(r.id, defaultW));
        if (availableRow) {
          destRowId = availableRow.id;
        } else {
          const lastRow = this.rows.length ? this.rows[this.rows.length - 1].id : null;
          destRowId = this.addRowBelow(lastRow);
        }
      }

      const widget = WidgetRegistry.create(type, {
        id: this._nextId--,
        source_id: this.sources.length ? this.sources[0].id : null,
        position: { order: this.widgets.length, w: defaultW, h: 300, row_id: destRowId },
        title: manifest.label || 'Nuevo Widget',
        style: { ...(manifest.style_defaults || {}) },
        _dirty: false,
      });
      this.widgets.push(widget);
      return widget;
    },

    // «Generar con IA»: crea en el servidor un widget ya configurado (la propuesta del
    // asistente) al final del tablero y lo monta. → {ok, error?}
    async createWidgetFromProposal({ type, source, title, width, rowId, order, fields, style }) {
      const WidgetClass = WidgetRegistry.get(type);
      const manifest = this.widgetManifest[type] || {};
      const defaultWStr = WidgetClass?.defaults?.width || 'md:col-span-6';
      const defaultW = width || parseInt(defaultWStr.match(/col-span-(\d+)/)?.[1] || '6', 10);
      const isHeader = WidgetClass?.placement === 'header';
      let destRowId = (this.rows.some(r => r.id === rowId) && rowId)
        || this.rows.find(r => this.canAddWidgetToRow(r.id, defaultW))?.id;
      if (!destRowId && !isHeader) {
        const lastRow = this.rows.length ? this.rows[this.rows.length - 1].id : null;
        destRowId = this.addRowBelow(lastRow);
        if (typeof Alpine !== 'undefined' && Alpine.nextTick) {
          await Alpine.nextTick();
        }
      }
      const widget = WidgetRegistry.create(type, {
        id: this._nextId--,
        source_id: source,
        position: {
          order: typeof order === 'number' ? order : this.widgets.length,
          w: defaultW, h: WidgetClass.defaults.height || 300, row_id: destRowId,
        },
        title: title || manifest.label || 'Nuevo Widget',
        fields: { ...EMPTY_FIELDS(), ...(fields || {}) },
        style: { ...(manifest.style_defaults || {}), ...(style || {}) },
        _dirty: true,
      });
      // Se ve enseguida (con su indicador de carga); al guardarse toma su id real y sus datos.
      this.widgets.push(widget);
      // Con `order` (varios creándose a la vez) entra antes del primero que va después de él.
      const container = containerFor(widget);
      const after = typeof order !== 'number' ? null : Array.from(container.children).find(el => {
        const other = this.widgets.find(w => w.el === el);
        return other && (other.position?.order ?? -1) > order;
      });
      container.insertBefore(widget.mount(), after || null);
      widget.setLoading(true);
      const result = await this._saveWidget(widget);
      if (!result.ok) {
        if (widget.el) widget.el.remove();
        this.widgets = this.widgets.filter(w => w !== widget);
        return result;
      }
      requestAnimationFrame(() => window.dispatchEvent(new Event('resize')));
      return result;
    },

    // Reparte parejo entre los widgets de cada fila las columnas que le sobran (hasta 12) y
    // guarda solo la posición: no se recalculan sus datos.
    async fillRows(rowIds) {
      const changed = [];
      (rowIds || []).forEach(rowId => {
        const widgets = this.getRowWidgets(rowId);
        const spare = 12 - this.getRowWidth(rowId);
        if (!widgets.length || spare <= 0) return;
        const each = Math.floor(spare / widgets.length);
        const extra = spare % widgets.length;
        widgets.forEach((w, index) => {
          const grow = each + (index < extra ? 1 : 0);
          if (!grow) return;
          const current = typeof w.position?.w === 'number' ? w.position.w : 6;
          w.position = { ...(w.position || {}), w: current + grow };
          w.updateChrome?.();
          changed.push(w);
        });
      });
      if (!changed.length) return;
      requestAnimationFrame(() => window.dispatchEvent(new Event('resize')));
      await Promise.all(changed.filter(w => w.id > 0).map(w =>
        fetch(apiUrl(`/api/widget/${w.id}/`), {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ position: w.position }),
        }).catch(() => null)));
    },

    // Crea o guarda según el id: un widget con id local todavía no existe en el servidor.
    async _saveWidget(w, { keepalive = false } = {}) {
      if (!w || !w.fields) return { ok: true, data: null };
      const isNew = w.id < 0;
      const url = isNew
        ? apiUrl(`/api/dashboard/${this.dashboardId}/widgets/`)
        : apiUrl(`/api/widget/${w.id}/`);
      try {
        const r = await fetch(url, {
          method: isNew ? 'POST' : 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            type: w.type,
            source: w.source_id,
            title: w.title,
            position: w.position,
            fields: w.fields,
            style: w.style,
          }),
          keepalive,
        });
        const data = await r.json().catch(() => null);
        if (!r.ok) {
          return { ok: false, error: (data && data.error) || `Error ${r.status}` };
        }
        if (isNew && data && data.id) {
          const oldId = w.id;
          w.id = data.id;
          if (this.assistantThreads[oldId]) {
            this.assistantThreads[data.id] = this.assistantThreads[oldId];
            delete this.assistantThreads[oldId];
          }
          if (this.editingId === oldId) this.editingId = data.id;
          w.rebuildElement();
        }
        w._dirty = false;
        const localPos = { ...(w.position || {}) };
        w.applyRender(data);
        if (localPos && localPos.row_id) {
          w.position = { ...(w.position || {}), ...localPos };
        }
        return { ok: true, data };
      } catch (e) {
        return { ok: false, error: 'No se pudo conectar con el servidor.' };
      }
    },

    _layoutTimer: null,
    _layoutSaving: null,
    isSavingLayout: false,

    scheduleLayoutSave() {
      this.isSavingLayout = true;
      clearTimeout(this._layoutTimer);
      this._layoutTimer = setTimeout(() => this.flushLayoutSave(), LAYOUT_SAVE_DELAY_MS);
    },

    async flushLayoutSave({ keepalive = false } = {}) {
      this.isSavingLayout = true;
      clearTimeout(this._layoutTimer);
      this._layoutTimer = null;
      if (this._layoutSaving && !keepalive) await this._layoutSaving;
      const pending = this.widgets.filter(w => w._dirty && w.id > 0);
      if (!pending.length) {
        this.isSavingLayout = false;
        return;
      }
      this._layoutSaving = Promise.all(pending.map(w => this._saveWidget(w, { keepalive })));
      const results = await this._layoutSaving;
      this._layoutSaving = null;
      this.isSavingLayout = false;
      if (results.some(r => !r.ok) && typeof window.showToast === 'function') {
        window.showToast('No se pudo guardar el diseño. Se reintentará con el próximo cambio.');
      }
    },

    get hasPendingLayout() {
      return !!this._layoutTimer || this.widgets.some(w => w._dirty && w.id > 0);
    },

    async removeWidget(id) {
      const removed = this.widgets.find(w => w.id === id);
      if (removed && removed.constructor.placement === 'header' && this.clearBoardFilters) {
        this.clearBoardFilters((removed.fields && removed.fields.dimensions) || []);
      }
      // El panel no queda abierto con un widget que ya no existe.
      if (this.editingId === id) this.closeDrawer();
      if (id > 0) {
        try { await fetch(apiUrl(`/api/widget/${id}/`), { method: 'DELETE' }); } catch (e) {}
      }
      this.widgets = this.widgets.filter(w => w.id !== id);
      delete this.assistantThreads[id];
    },

    reorderWidgets() {
      const canvasEl = document.getElementById('dashboard-canvas');
      if (!canvasEl) return;

      const rowEls = canvasEl.querySelectorAll('.canvas-row[data-row-id]');
      if (rowEls.length) {
        const orderedRowIds = Array.from(rowEls).map(el => el.dataset.rowId);
        this.rows = orderedRowIds.map(id => ({ id }));
      }

      let seq = 0;
      this.rows.forEach(r => {
        const rowBody = canvasEl.querySelector(`.canvas-row-body[data-row-id="${r.id}"]`);
        if (!rowBody) return;
        const widgetEls = rowBody.querySelectorAll('[data-widget-id]');
        widgetEls.forEach((el, i) => {
          const id = parseInt(el.dataset.widgetId, 10);
          const w = this.widgets.find(widget => widget.id === id || widget.el === el);
          if (w) {
            if (w.position?.row_id !== r.id || w.position?.order !== seq) {
              w.position = { ...(w.position || {}), row_id: r.id, order: seq };
              w._dirty = true;
            }
            seq++;
          }
        });
      });

      this.widgets = [...this.widgets];
      this.rows = [...this.rows];
      this.scheduleLayoutSave();
    },

    // ------------------------------------------------------------------ editor
    get editingWidget() {
      return this.widgets.find(w => w.id === this.editingId) || null;
    },

    get drawerManifest() {
      return this.widgetManifest[this.editingType] || {};
    },

    get drawerCapabilities() {
      return this.drawerManifest.capabilities || {};
    },

    // Límites de las listas del panel (capabilities del backend).
    get hasColumns() { return (this.drawerCapabilities.columns || [0, 0])[1] > 0; },
    get maxColumns() { return (this.drawerCapabilities.columns || [0, 0])[1]; },
    get columnsLabel() { return this.drawerCapabilities.columns_label || 'Columnas a mostrar'; },
    get hasMetrics() { return (this.drawerCapabilities.metrics || [0, 0])[1] > 0; },
    get maxMetrics() { return (this.drawerCapabilities.metrics || [0, 0])[1]; },
    // Gráficos (`pivot_max_metrics`): con pivote cada serie es un valor del pivote, así que
    // pivote y varias métricas se excluyen. La tabla dinámica no lo declara.
    get pivotMetricsCap() { return this.drawerCapabilities.pivot_max_metrics || 0; },
    get metricsLimitedByPivot() {
      return !!this.pivotMetricsCap && (this.drawerDraft.fields.pivots || []).some(Boolean);
    },
    get metricsLimit() {
      return this.metricsLimitedByPivot ? Math.min(this.maxMetrics, this.pivotMetricsCap) : this.maxMetrics;
    },
    get pivotsBlocked() {
      return !!this.pivotMetricsCap && (this.drawerDraft.fields.metrics || []).length > this.pivotMetricsCap;
    },
    // Condiciones propias de una métrica: solo sirven junto a otras métricas (Electrónica vs
    // total); con una sola equivalen a los filtros del widget.
    get hasMetricFilters() { return this.maxMetrics > 1; },
    get minDimensions() { return (this.drawerCapabilities.dimensions || [0, 0])[0]; },
    get maxDimensions() { return (this.drawerCapabilities.dimensions || [0, 0])[1]; },
    get maxPivots() { return (this.drawerCapabilities.pivots || [0, 0])[1]; },
    // Columnas para la mini tendencia: las de tiempo por las que se agrupa (año, mes, fecha…).
    // Ni categorías (Campus: la línea no sería una evolución) ni montos (también ordenables).
    get trendOptions() {
      const time = this.schema.time_fields || [];
      const options = (this.schema.dimension_fields || []).filter(c => time.includes(c));
      // La guardada se conserva aunque ya no valga: se ve en el select y se puede quitar.
      const current = this.drawerDraft.fields.trend_by;
      return current && !options.includes(current) ? [...options, current] : options;
    },
    get trendInvalid() {
      const current = this.drawerDraft.fields.trend_by;
      return !!current && !(this.schema.time_fields || []).includes(current);
    },
    // Lo que el servidor permite elegir en las métricas de este widget (ver arriba).
    get panelOptions() {
      return this.drawerManifest.panel_options || {};
    },
    // Opciones de «Mostrar como» de una métrica: las que el servidor acepta para el widget, su
    // agregación y si hay pivotes; [] = sin select.
    metricWindowOptions(metric) {
      const hasPivots = (this.drawerDraft.fields.pivots || []).some(Boolean);
      const byAgg = ((this.panelOptions.windows || {})[hasPivots ? 'pivot' : 'flat']) || {};
      const allowed = byAgg[metric && metric.agg] || [];
      const options = WINDOW_OPTIONS.filter(o => allowed.includes(o.value))
        .map(o => ({ value: o.value, label: this._windowLabel(o, hasPivots) }));
      return options.length ? [{ value: '', label: 'Valor' }, ...options] : [];
    },
    _windowLabel(option, hasPivots) {
      if (!hasPivots) return option.label;
      const fields = this.drawerDraft.fields;
      const by = this.editingType !== 'dynamic_table' && option.chartBy
        && (fields[option.chartBy] || []).find(Boolean);
      if (by) return `% del total de cada ${humanizeName(by)}`;
      return option.pivotLabel || option.label;
    },
    // ¿La ventana guardada en la métrica ya no vale (ej. se agregó un pivote o se cambió a
    // promedio)? Se quita al guardar.
    metricWindowInvalid(metric) {
      const type = metric && metric.window && metric.window.type;
      return !!type && !this.metricWindowOptions(metric).some(o => o.value === type);
    },
    setMetricWindow(metric, type) {
      if (type) metric.window = { type };
      else delete metric.window;
    },
    // Opciones de un select que elige una métrica del widget (roles del KPI): las fijas que
    // pasa el partial (`base`, ej. «Primera métrica») + las métricas del borrador.
    metricRoleOptions(base = []) {
      const metrics = (this.drawerDraft.fields.metrics || []).filter(m => m && m.alias);
      return [...base, ...metrics.map(m => ({ value: m.alias, label: this.metricName(m) }))];
    },

    openDrawer(id) {
      const w = this.widgets.find(x => x.id === id);
      if (!w) return;
      // La ayuda ocupa el mismo lugar: editar un widget la cierra.
      Alpine.store('help')?.close();
      this.editingId = id;
      this.editingType = w.type;
      this.drawerTab = 'data';
      this.drawerAskOpen = false;
      this.drawerSaveError = '';
      this.drawerSaving = false;
      this.scaleEditor = null;
      this.scaleProposal = null;
      const manifest = this.widgetManifest[w.type] || {};
      const title = w.title || manifest.label || 'Nuevo Widget';
      this.drawerDraft = {
        title,
        source: w.source_id ?? null,
        // Copias: lo que se edita en el panel no toca el widget hasta «Guardar».
        fields: { ...EMPTY_FIELDS(), ...JSON.parse(JSON.stringify(w.fields || {})) },
        style: { ...(manifest.style_defaults || {}), ...JSON.parse(JSON.stringify(w.style || {})), title },
      };
      this._normalizeDraft();
      // Las columnas por defecto salen del schema de la fuente: se eligen cuando llega.
      this.loadSchema(this.drawerDraft.source).then(() => {
        if (this.editingId === id) {
          this._prefillDraft();
          this._checkCorrelationMethod();
        }
      });
      // Sin esperar: los tags del chat aparecen cuando llegan.
      this.loadSuggestions(w.type);
      // El panel del tipo lo crea un `x-if`: sus listas existen en el siguiente tick.
      this.destroyListSortables();
      Alpine.nextTick(() => this.initListSortables());
    },

    // Un widget recién arrastrado (o con otra fuente) empieza con columnas y una métrica.
    _prefillDraft() {
      if (this.hasColumns) {
        if (!this.drawerDraft.fields.columns.length) this._autoPickColumns();
      } else if (!this.drawerDraft.fields.dimensions.length) {
        this._autoPickDimensions();
      }
      // No auto-agregar métrica si ya hay métricas en el widget (evita duplicados al editar).
      if ((this.drawerCapabilities.metrics || [0, 0])[0] > 0 && !this.drawerDraft.fields.metrics.length) {
        this.addMetric();
      }
    },

    // Cambiar la fuente del widget en el panel: otro schema y otras sugerencias. Otra hoja tiene
    // otras columnas: los campos (y los roles del KPI, que apuntan a sus métricas) se reinician
    // y se vuelven a elegir de la nueva fuente.
    async changeDrawerSource(sourceId) {
      const id = sourceId ? Number(sourceId) : null;
      if (id === this.drawerDraft.source) return;
      this.drawerDraft.source = id;
      this.drawerDraft.fields = EMPTY_FIELDS();
      const style = this.drawerDraft.style;
      ['primary', 'compare', 'targetMetric', 'target'].forEach(key => delete style[key]);
      await this.loadSchema(id);
      if (this.drawerDraft.source === id) this._prefillDraft();
      this.loadSuggestions(this.editingType);
    },

    // (Re)atan los arrastres de las listas del panel: se destruyen al cerrar.
    initListSortables() {
      ['columns', 'dimensions', 'pivots', 'metrics'].forEach(key => {
        const el = document.querySelector(`[data-column-list="${key}"]`);
        if (el) this.initListSortable(el, key);
      });
    },

    closeDrawer() {
      const w = this.editingWidget;
      if (w && w.id < 0 && (!w.fields || !w.fields.dimensions || !w.fields.dimensions.length)) {
        if (w.el) w.el.remove();
        this.widgets = this.widgets.filter(x => x !== w);
      }
      this.editingId = null;
      this.editingType = null;
      this.drawerAskOpen = false;
      this.drawerSaveError = '';
      this.drawerTab = 'data';
      this.scaleEditor = null;
      this.scaleProposal = null;
      this.destroyListSortables();
    },

    // Reordenar columnas / dimensiones / pivotes / métricas arrastrando por el asa. El `x-for` de
    // Alpine repinta al cambiar el array: al soltar se devuelve la fila a su sitio y manda
    // el array (índices originales, como Sortable).
    initListSortable(el, key) {
      if (!el || typeof Sortable === 'undefined') return;
      this.listSortables[key]?.destroy();
      this.listSortables[key] = new Sortable(el, {
        draggable: '[data-column-row]',
        handle: '.column-drag-handle',
        animation: 150,
        ghostClass: 'metric-ghost-preview',
        onEnd: (evt) => {
          const from = evt.oldDraggableIndex;
          const to = evt.newDraggableIndex;
          if (from == null || to == null || from === to) return;
          const rows = [...el.querySelectorAll('[data-column-row]')].filter(n => n !== evt.item);
          el.insertBefore(evt.item, rows[from] || null);
          const list = this.drawerDraft && this.drawerDraft.fields && this.drawerDraft.fields[key];
          if (!list) return;
          const [moved] = list.splice(from, 1);
          list.splice(to, 0, moved);
        },
      });
    },

    destroyListSortables() {
      Object.values(this.listSortables || {}).forEach(s => s.destroy());
      this.listSortables = {};
    },

    _normalizeDraft() {
      const f = this.drawerDraft.fields;
      ['dimensions', 'pivots', 'metrics', 'filters', 'columns'].forEach(k => {
        if (!Array.isArray(f[k])) f[k] = [];
      });
      // `columns`: {"field": ..., "label": ...} sin repetidas y sin vacías.
      const seen = new Set();
      const columns = [];
      f.columns.forEach(c => {
        const raw = typeof c === 'string' ? { field: c } : (c || {});
        const field = String(raw.field || '').trim();
        const label = String(raw.label || '').trim();
        if (!field || seen.has(field)) return;
        seen.add(field);
        columns.push(label ? { field, label } : { field });
      });
      f.columns = columns;
      if (typeof f.trend_by !== 'string') f.trend_by = f.trend_by == null ? '' : String(f.trend_by);
      if (typeof f.sort_by !== 'string') f.sort_by = null;
      if (f.limit != null && f.limit !== '') f.limit = Number(f.limit);
      const toDraft = c => (c && typeof c === 'object' && c._k ? c : conditionFromPayload(c || {}));
      f.filters = f.filters.map(toDraft);
      f.metrics.forEach(m => {
        if (!m) return;
        m.filters = (Array.isArray(m.filters) ? m.filters : []).map(toDraft);
      });
    },

    // Un widget recién arrastrado empieza con las primeras columnas ya elegidas.
    _autoPickDimensions() {
      const caps = this.drawerCapabilities;
      const max = Math.min(caps.dimensions ? caps.dimensions[1] : 0, 1);
      const source = this.schema.dimension_fields && this.schema.dimension_fields.length
        ? this.schema.dimension_fields
        : this.schema.all_fields;
      this.drawerDraft.fields.dimensions = (source || []).slice(0, max);
    },

    // Columnas que el widget puede elegir: todas, o solo las numéricas si calcula con ellas
    // (`columns_numeric`, la dispersión). Con `columns_scaled` (la matriz de correlación), las
    // que se correlacionan: numéricas, ordinales y nominales (una fila 0/1 por categoría).
    get columnFields() {
      if (this.drawerCapabilities.columns_scaled) {
        return (this.schema.all_fields || []).filter(f => this.isCorrelatable(f));
      }
      const virtual = new Set(this.schema.grid_columns || []);
      return ((this.drawerCapabilities.columns_numeric ? this.schema.numeric_fields : this.schema.all_fields) || [])
        .filter(f => !virtual.has(f));
    },

    // Un widget nuevo de columnas arranca con las primeras de la hoja (la Tabla, 8; la
    // correlación, 4 variables que no sean de tiempo ni identificadores, con las numéricas y
    // ordinales primero: un año o un ID no se correlacionan).
    _autoPickColumns() {
      const scaled = this.drawerCapabilities.columns_scaled;
      const numeric = this.drawerCapabilities.columns_numeric || scaled;
      const time = this.schema.time_fields || [];
      const isId = f => /(^|[_\s-])(id|codigo|código)([_\s-]|$)/i.test(f);
      let fields = numeric ? this.columnFields.filter(f => !time.includes(f) && !isId(f)) : this.columnFields;
      // La matriz: su escala ya deja fuera fechas e IDs (`time_fields` incluye cualquier número);
      // arranca con numéricas y ordinales (una nominal suma una fila por categoría).
      if (scaled) fields = this.columnFields.filter(f => this.columnScale(f) !== 'nominal');
      const max = Math.min(this.maxColumns || 0, numeric ? 4 : 8);
      this.drawerDraft.fields.columns = fields.slice(0, max).map(field => ({ field }));
    },

    // ---- dimensiones / pivotes
    _dimensionColumns() {
      return (this.schema.dimension_fields && this.schema.dimension_fields.length)
        ? this.schema.dimension_fields
        : (this.schema.all_fields || []);
    },

    // Columnas elegibles en la fila `idx`: las ya usadas por otra fila no se repiten.
    dimensionOptions(idx) {
      const list = this.drawerDraft.fields.dimensions;
      return this._dimensionColumns().filter(c => list[idx] === c || !list.includes(c));
    },

    dimensionSheetOptions(idx) {
      const available = this.dimensionOptions(idx);
      const calc = new Set(this.calculatedFieldNames);
      return available.filter(c => !calc.has(c));
    },

    dimensionCalculatedOptions(idx) {
      const available = this.dimensionOptions(idx);
      const calc = new Set(this.calculatedFieldNames);
      return available.filter(c => calc.has(c));
    },

    pivotOptions(idx) {
      const list = this.drawerDraft.fields.pivots;
      return (this.schema.all_fields || []).filter(c => list[idx] === c || !list.includes(c));
    },

    addDimension() {
      const list = this.drawerDraft.fields.dimensions;
      if (list.length >= this.maxDimensions) return;
      list.push(this._dimensionColumns().find(c => !list.includes(c)) || '');
    },

    removeDimension(index) {
      this.drawerDraft.fields.dimensions.splice(index, 1);
    },

    // ---- cuadrículas (columnas virtuales, ver engine/steps/grid.py)
    // Las virtuales que ofrece el widget que se edita (ninguna si no las acepta).
    get gridColumns() {
      return this.drawerManifest.grid_fields ? (this.schema.grid_columns || []) : [];
    },

    // Las virtuales elegibles en la fila `idx` de `list` (dimensiones o pivotes): sin repetir.
    gridOptions(list, idx) {
      return this.gridColumns.filter(c => list[idx] === c || !list.includes(c));
    },

    // Al elegir «P · Aspecto» como dimensión, con el pivote vacío: «P · Respuesta» como pivote
    // (la tabla Aspecto × Respuesta de un clic).
    onDimensionChange(idx) {
      const fields = this.drawerDraft.fields;
      const grid = (this.schema.grid_fields || []).find(g => g.aspect && g.aspect === fields.dimensions[idx]);
      if (!grid || !grid.value || !this.maxPivots || fields.pivots.some(Boolean)) return;
      fields.pivots = [grid.value];
    },

    addPivot() {
      const list = this.drawerDraft.fields.pivots;
      if (list.length >= this.maxPivots) return;
      list.push((this.schema.all_fields || []).find(c => !list.includes(c)) || '');
    },

    removePivot(index) {
      this.drawerDraft.fields.pivots.splice(index, 1);
    },

    // ---- columnas (tabla: se muestran tal cual, en orden)
    columnOptions(idx) {
      const list = this.drawerDraft.fields.columns;
      const current = (list[idx] || {}).field;
      return this.columnFields.filter(c => c === current || !list.some(x => x.field === c));
    },

    columnSheetOptions(idx) {
      const available = this.columnOptions(idx);
      const calc = new Set(this.calculatedFieldNames);
      return available.filter(c => !calc.has(c));
    },

    columnCalculatedOptions(idx) {
      const available = this.columnOptions(idx);
      const calc = new Set(this.calculatedFieldNames);
      return available.filter(c => calc.has(c));
    },

    addColumn() {
      const list = this.drawerDraft.fields.columns;
      if (list.length >= this.maxColumns) return;
      const field = this.columnFields.find(c => !list.some(x => x.field === c));
      if (field) list.push({ field });
      this._checkCorrelationMethod();
    },

    removeColumn(index) {
      this.drawerDraft.fields.columns.splice(index, 1);
      this._checkCorrelationMethod();
    },

    // Al cambiar la columna elegida, el «Nombre a mostrar» de esa fila deja de servir.
    onColumnFieldChange(index, field) {
      const column = this.drawerDraft.fields.columns[index];
      if (!column) return;
      if (field != null && column.field !== field) column.field = field;
      column.label = '';
      this._checkCorrelationMethod();
    },

    // Nombre que se muestra en la tabla: el «Nombre a mostrar» o la columna tal cual.
    columnLabel(column) {
      return (column && column.label) || humanizeName((column && column.field) || '');
    },

    // ---- escala de medida de las columnas (matriz de correlación)
    // La escala vive en la fuente: la guardada o, sin ella, la detectada (`schema.scales`).
    columnProfile(field) {
      return (this.schema.scales || {})[field] || null;
    },

    columnScale(field) {
      const profile = this.columnProfile(field);
      return profile ? profile.scale : null;
    },

    // ¿Entra en una correlación? Numéricas, ordinales y nominales (como dummies 0/1).
    isCorrelatable(field) {
      return ['continuous', 'discrete', 'ordinal', 'nominal'].includes(this.columnScale(field));
    },

    // ¿El método de correlación está forzado a Spearman?
    // Si alguna variable seleccionada es ordinal o nominal, el motor estadístico fuerza Spearman.
    get isCorrelationLockedToSpearman() {
      if (this.editingType !== 'correlation') return false;
      const cols = (this.drawerDraft?.fields?.columns || []).map(c => typeof c === 'object' ? c.field : c).filter(Boolean);
      return cols.some(f => ['ordinal', 'nominal'].includes(this.columnScale(f)));
    },

    _checkCorrelationMethod() {
      if (this.isCorrelationLockedToSpearman && this.drawerDraft && this.drawerDraft.style) {
        this.drawerDraft.style.method = 'spearman';
      }
    },

    scaleLabel(scale) {
      return SCALE_LABELS[scale] || '';
    },

    // Escalas que admite una columna: una numérica no es ordinal por texto (sus valores ya
    // tienen orden: discreta); una categoría no es continua ni discreta.
    scaleOptions(field) {
      const numeric = (this.schema.numeric_fields || []).includes(field);
      const keys = numeric ? ['continuous', 'discrete', 'nominal', 'none'] : ['nominal', 'ordinal', 'none'];
      return keys.map(value => ({ value, label: SCALE_LABELS[value] }));
    },

    // Editor de la escala de una variable: { field, scale, order, saving, error } o null.
    scaleEditor: null,

    openScaleEditor(field) {
      const profile = this.columnProfile(field);
      if (!profile) return;
      if (this.scaleEditor && this.scaleEditor.field === field) {
        this.scaleEditor = null;
        return;
      }
      const values = (profile.stats && profile.stats.values) || [];
      this.scaleEditor = { field, scale: profile.scale, order: [...(profile.order || values)], saving: false, error: '' };
    },

    // Al pasar a ordinal, arranca con el orden detectado o con los valores de la columna.
    onScaleEditorChange(scale) {
      const editor = this.scaleEditor;
      if (!editor) return;
      editor.scale = scale;
      if (scale === 'ordinal' && !editor.order.length) {
        const profile = this.columnProfile(editor.field) || {};
        editor.order = [...(profile.order || (profile.stats && profile.stats.values) || [])];
      }
    },

    moveOrderValue(index, delta) {
      const order = this.scaleEditor && this.scaleEditor.order;
      const to = index + delta;
      if (!order || to < 0 || to >= order.length) return;
      const [value] = order.splice(index, 1);
      order.splice(to, 0, value);
    },

    async saveScaleEditor() {
      const editor = this.scaleEditor;
      if (!editor || editor.saving) return;
      editor.saving = true;
      editor.error = '';
      try {
        const item = editor.scale === 'ordinal' ? { scale: 'ordinal', order: editor.order } : { scale: editor.scale };
        await this.saveScales(this.drawerDraft.source, { [editor.field]: item });
        this.scaleEditor = null;
        // Una variable que ya no se correlaciona («no se analiza») sale de la matriz.
        if (!this.isCorrelatable(editor.field)) {
          this.drawerDraft.fields.columns = this.drawerDraft.fields.columns.filter(c => c.field !== editor.field);
        }
      } catch (e) {
        editor.error = e.message;
      } finally {
        editor.saving = false;
      }
    },

    get defaultIgnoredValues() { return DEFAULT_IGNORED_VALUES; },

    get availableIgnoredOptions() {
      const defaults = DEFAULT_IGNORED_VALUES.split(',').map(s => s.trim()).filter(Boolean);
      const current = this.ignoredValuesList;
      return Array.from(new Set([...defaults, ...current]));
    },

    get ignoredValuesList() {
      const style = this.drawerDraft.style || {};
      const raw = style.ignoredValues;
      if (Array.isArray(raw)) return raw;
      if (typeof raw === 'string' && raw.trim()) {
        return raw.split(',').map(s => s.trim()).filter(Boolean);
      }
      return DEFAULT_IGNORED_VALUES.split(',').map(s => s.trim()).filter(Boolean);
    },

    set ignoredValuesList(list) {
      if (!this.drawerDraft.style) this.drawerDraft.style = {};
      const arr = Array.isArray(list) ? list : (list ? [list] : []);
      this.drawerDraft.style.ignoredValues = arr.join(', ');
    },

    // Guarda en la fuente la escala de esas columnas ({columna: {scale, order?}}) y relee su
    // schema (cambian `scales`). Lanza con el mensaje del servidor si falla.
    async saveScales(source, scales) {
      const style = this.drawerDraft.style || {};
      const ignoreNonApplicable = style.ignoreNonApplicable !== false;
      const ignoredValues = ignoreNonApplicable
        ? (this.ignoredValuesList.length ? this.ignoredValuesList.join(', ') : DEFAULT_IGNORED_VALUES)
        : null;
      const r = await fetch(apiUrl(`/api/sources/${source}/scales/`), {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ scales, ignored_values: ignoredValues }),
      });
      const data = await r.json().catch(() => null);
      if (!r.ok || !data) throw new Error((data && data.error) || `Error ${r.status}`);
      delete this.schemas[source];
      if (String(this.drawerDraft.source) === String(source)) await this.loadSchema(source);
      this._checkCorrelationMethod();
    },

    // «Clasificar con IA»: la IA propone la escala de las variables elegidas; el panel muestra
    // solo las que cambian y las guarda si el usuario las acepta.
    scaleProposal: null,   // { loading, error, items: [{field, scale, order?, reason}] }

    async classifyScales() {
      const columns = this.drawerDraft.fields.columns.map(c => c.field).filter(Boolean);
      if (!columns.length || (this.scaleProposal && this.scaleProposal.loading)) return;
      this.scaleProposal = { loading: true, error: '', items: [] };
      const style = this.drawerDraft.style || {};
      const ignoreNonApplicable = style.ignoreNonApplicable !== false;
      const ignoredValues = ignoreNonApplicable
        ? (this.ignoredValuesList.length ? this.ignoredValuesList.join(', ') : DEFAULT_IGNORED_VALUES)
        : null;
      try {
        const { r, data } = await fetchJsonSafe(apiUrl(`/api/sources/${this.drawerDraft.source}/scales/ai/`), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ columns, ignored_values: ignoredValues }),
        });
        if (!r.ok || !data) throw new Error((data && data.error) || 'El servidor no respondió correctamente.');
        const items = Object.entries(data.scales || {})
          .map(([field, p]) => ({ field, ...p }))
          .filter(p => {
            const current = this.columnProfile(p.field) || {};
            return p.scale !== current.scale
              || JSON.stringify(p.order || null) !== JSON.stringify(current.scale === 'ordinal' ? current.order || null : null);
          });
        this.scaleProposal = { loading: false, error: '', items };
      } catch (e) {
        this.scaleProposal = {
          loading: false, items: [],
          error: e.name === 'AbortError' ? 'La IA tardó demasiado en responder. Intenta de nuevo.' : e.message,
        };
      }
    },

    async acceptScaleProposal() {
      const proposal = this.scaleProposal;
      if (!proposal || !proposal.items.length || proposal.loading) return;
      const scales = Object.fromEntries(proposal.items.map(p => [p.field, p.order ? { scale: p.scale, order: p.order } : { scale: p.scale }]));
      proposal.loading = true;
      try {
        await this.saveScales(this.drawerDraft.source, scales);
        const dropped = proposal.items.filter(p => !this.isCorrelatable(p.field)).map(p => p.field);
        if (dropped.length) {
          this.drawerDraft.fields.columns = this.drawerDraft.fields.columns.filter(c => !dropped.includes(c.field));
        }
        this.scaleProposal = null;
      } catch (e) {
        proposal.loading = false;
        proposal.error = e.message;
      }
    },

    // Escalas que propone la IA con su widget ([{field, scale, order?}]) → {columna: {scale, order?}}.
    _scalesMap(items) {
      return Object.fromEntries((items || []).map(p => [p.field, p.order && p.order.length ? { scale: p.scale, order: p.order } : { scale: p.scale }]));
    },

    // ---- métricas
    addMetric() {
      const metrics = this.drawerDraft.fields.metrics;
      if (metrics.length >= (this.drawerCapabilities.metrics || [0, 99])[1]) return;
      // Medidas de verdad primero: numéricas que no son dimensiones (año, mes...
      // no se suman como métrica por defecto).
      const dims = this.schema.dimension_fields || [];
      const virtual = this.schema.grid_columns || [];
      const numeric = (this.schema.numeric_fields || []).filter(f => !virtual.includes(f));
      const used = f => metrics.some(m => m && m.field === f);
      const field = numeric.find(f => !dims.includes(f) && !used(f))
        || numeric.find(f => !used(f))
        || (this.schema.all_fields || [])[0] || '';
      metrics.push({ field, agg: 'count', alias: this._autoAlias('count', field, metrics), filters: [] });
    },

    removeMetric(index) {
      const removed = this.drawerDraft.fields.metrics.splice(index, 1)[0];
      this._rebindAlias((removed || {}).alias, '');
    },

    onMetricFieldChange(metric) {
      // Un campo calculado agregado trae su agregación («Automática»); al dejarlo, se vuelve a
      // una agregación normal.
      if (this.isAggregatedField(metric.field)) metric.agg = 'auto';
      else if (metric.agg === 'auto') metric.agg = 'count';
      // Una agregación numérica (Suma, Promedio…) sobre una columna de texto no vale: Conteo.
      if (!this.metricAggOptions(metric).some(o => o.value === metric.agg)) metric.agg = 'count';
      const taken = this.drawerDraft.fields.metrics.filter(m => m !== metric).map(m => m.alias);
      const oldAlias = metric.alias;
      metric.alias = this._autoAlias(metric.agg, metric.field, taken);
      if (oldAlias !== metric.alias) this._rebindAlias(oldAlias, metric.alias);
      // «Mostrar como» que ya no vale con la nueva agregación (ej. % del total con Promedio): se
      // quita, y el select se oculta si no queda ninguna opción.
      if (this.metricWindowInvalid(metric)) delete metric.window;
    },

    // Los controles que eligen una métrica (`options_from: 'metrics'`) y el orden apuntan al
    // alias: si cambia, siguen (el orden, con la misma dirección); si la métrica se quita, se
    // vacían.
    _rebindAlias(oldAlias, newAlias) {
      if (!oldAlias) return;
      const style = this.drawerDraft.style || {};
      (this.drawerManifest.style_schema || [])
        .filter(c => c.options_from === 'metrics')
        .forEach(c => { if (style[c.key] === oldAlias) style[c.key] = newAlias || ''; });
      const fields = this.drawerDraft.fields;
      if (this.sortValue === oldAlias) {
        fields.sort_by = newAlias ? (this.sortDirection === 'desc' ? `-${newAlias}` : newAlias) : null;
      }
    },

    // Nombre visible de la métrica: «Nombre a mostrar» o, si está vacío, el agg en español
    // con el campo («Promedio Ventas»), igual que en las cabeceras del backend.
    metricName(metric) {
      if (metric && metric.agg === 'auto') return metric.field || '';
      const agg = (this.aggOptions.find(o => o.value === (metric.agg || '')) || {}).label
        || (metric.agg || '');
      const name = `${agg} ${humanizeName(metric.field)}`.trim();
      const conditions = this.metricConditionsSummary(metric);
      return conditions ? `${name} · ${conditions}` : name;
    },

    // «Categoría es igual a Electrónica y Año es igual a 2026»: las condiciones completas de
    // la métrica (las a medio escribir no se cuentan).
    metricConditionsSummary(metric) {
      const numeric = this.schema.numeric_fields || [];
      return ((metric && metric.filters) || [])
        .filter(c => c && c.field && (!opMeta(c.op).needsValue || c.relative || String(c.value ?? '').trim() !== ''))
        .map(c => this._describeCondition(c._k ? conditionToPayload(c, numeric) : c))
        .join(' y ');
    },

    _autoAlias(agg, field, taken) {
      const base = (agg && field) ? `${agg}_${field}` : (field || 'metrica');
      const slug = base.toLowerCase().replace(/[^a-z0-9_]+/g, '_').replace(/^_+|_+$/g, '') || 'metrica';
      const truncated = slug.slice(0, 30).replace(/_+$/g, '') || 'metrica';
      let alias = /^[a-z]/.test(truncated) ? truncated : `m_${truncated}`;
      let n = 2;
      const used = new Set(taken || []);
      while (used.has(alias)) alias = `${truncated.slice(0, 26)}_${n++}`;
      return alias;
    },

    get aggOptions() { return AGG_OPTIONS; },
    // «Automático» más los formatos que el servidor acepta en una métrica.
    get metricFormatOptions() {
      const allowed = this.panelOptions.metric_formats || [];
      return METRIC_FORMAT_OPTIONS.filter(o => !o.value || allowed.includes(o.value));
    },
    // Campos calculados agregados de la fuente (`schema.aggregated_fields`): solo métricas.
    get aggregatedFields() { return (this.schema.aggregated_fields || []).map(f => f.name); },
    isAggregatedField(name) { return !!name && this.aggregatedFields.includes(name); },

    // Todos los nombres de campos calculados (por fila y agregados) de la fuente
    get calculatedFieldNames() {
      const fromSource = this.schema.calculated_fields || [];
      const agg = this.aggregatedFields;
      return Array.from(new Set([...fromSource, ...agg]));
    },
    isCalculatedField(name) { return !!name && this.calculatedFieldNames.includes(name); },

    // Columnas de la hoja (excluyendo los campos calculados por fila que están en all_fields)
    get sheetColumns() {
      const all = this.schema.all_fields || [];
      const calc = new Set(this.calculatedFieldNames);
      return all.filter(f => !calc.has(f));
    },
    // Agregaciones del select de una métrica: con un campo agregado, solo «Automática».
    metricAggOptions(metric) {
      if (metric && metric.agg === 'auto') return [{ value: 'auto', label: 'Automática' }];
      // Sobre una columna de texto solo se cuenta (las numéricas las dice el servidor).
      const field = metric && metric.field;
      const numericOnly = this.panelOptions.numeric_aggs || [];
      const isText = field && !(this.schema.numeric_fields || []).includes(field);
      return isText ? AGG_OPTIONS.filter(o => !numericOnly.includes(o.value)) : AGG_OPTIONS;
    },

    // ---- filtros (del widget y propios de cada métrica: la misma fila de condición)
    _addCondition(list) {
      if (list.length >= 20) return;
      list.push(conditionFromPayload({ field: (this.schema.all_fields || [])[0] || '', op: 'eq' }));
    },

    addFilter() {
      this._addCondition(this.drawerDraft.fields.filters);
    },

    addMetricFilter(metric) {
      if (!Array.isArray(metric.filters)) metric.filters = [];
      this._addCondition(metric.filters);
    },

    removeCondition(list, index) {
      list.splice(index, 1);
    },

    get filterOpOptions() { return FILTER_OPS; },
    // Los del reloj valen en cualquier columna; los periodos, solo en columnas de tiempo.
    relativeOptionsFor(c) {
      const isTime = (this.schema.time_fields || []).includes(c && c.field);
      return RELATIVE_VALUES.filter(o => !o.data || isTime);
    },

    filterNeedsValue(c) { return opMeta(c.op).needsValue; },
    filterIsRange(c) { return opMeta(c.op).isRange; },
    filterIsList(c) { return opMeta(c.op).isList; },

    onFilterFieldChange(c) {
      const numeric = (this.schema.numeric_fields || []).includes(c.field);
      const meta = opMeta(c.op);
      if (numeric && ['contains', 'in', 'not_in'].includes(c.op)) c.op = 'eq';
      if (!numeric && ['lt', 'lte', 'gt', 'gte', 'between'].includes(c.op)) c.op = 'eq';
      c.value = '';
      c.value2 = '';
      // Un periodo («el más reciente») que la nueva columna no admite se borra.
      if (c.relative && !this.relativeOptionsFor(c).some(o => o.value === c.relative)) c.relative = '';
      if (!meta.needsValue) return;
    },

    onFilterOpChange(c) {
      c.value = '';
      c.value2 = '';
      if (c.mode === 'relative') c.mode = 'value';
    },

    sampleListId(field) {
      return `samples-${String(field).replace(/[^a-z0-9_-]/gi, '_')}`;
    },

    get sampleLists() {
      const samples = this.schema.sample_values || {};
      return Object.entries(samples).map(([field, values]) => ({ id: this.sampleListId(field), values }));
    },

    // ---- orden y límite
    get sortOptions() {
      const f = this.drawerDraft.fields;
      const metricAliases = (f.metrics || []).map(m => m.alias).filter(Boolean);
      // Tablas: el orden va sobre sus columnas; el resto, sobre dimensiones y alias.
      const options = [
        ...(f.columns || []).filter(c => c && c.field)
          .map(c => ({ value: c.field, label: c.label || humanizeName(c.field) })),
        ...(f.dimensions || []).map(c => ({ value: c, label: c })),
        ...metricAliases.map(c => ({ value: c, label: c })),
      ];
      return options.filter((o, i, arr) => o.value && arr.findIndex(x => x.value === o.value) === i);
    },

    get sortValue() {
      const raw = this.drawerDraft.fields.sort_by;
      return raw ? String(raw).replace(/^-/, '') : '';
    },

    get sortDirection() {
      const raw = this.drawerDraft.fields.sort_by;
      return raw && String(raw).startsWith('-') ? 'desc' : 'asc';
    },

    setSortValue(value) {
      this.drawerDraft.fields.sort_by = value
        ? (this.sortDirection === 'desc' ? `-${value}` : value)
        : null;
    },

    setSortDirection(direction) {
      const value = this.sortValue;
      this.drawerDraft.fields.sort_by = value ? (direction === 'desc' ? `-${value}` : value) : null;
    },

    // ---- guardado
    // El borrador como lo guarda el backend (`{title, fields, style}`), sin modificarlo: lo
    // usan «Guardar» y el chat con la IA (el estado actual que la IA debe ajustar).
    _draftPayload(fallbackTitle = '') {
      const draft = this.drawerDraft;
      const fields = JSON.parse(JSON.stringify(draft.fields));
      // Una fila a medio elegir («— elegir —») no se envía al servidor.
      ['dimensions', 'pivots'].forEach(k => {
        fields[k] = (fields[k] || []).filter(v => v !== '' && v != null);
      });
      fields.metrics = fields.metrics || [];
      // Una ventana que ya no vale con los pivotes o el tipo actual no se envía (form_errors la rechazaría).
      fields.metrics.forEach(m => {
        if (m && m.window && this.metricWindowInvalid(m)) delete m.window;
      });
      // Condiciones de la métrica en formato del DSL; sin ninguna, la clave no se envía.
      fields.metrics.forEach(m => {
        if (!m || !Array.isArray(m.filters)) return;
        if (m.filters.length) m.filters = m.filters.map(c => conditionToPayload(c, this.schema.numeric_fields || []));
        else delete m.filters;
      });
      // Columnas: solo `field`, y `label` solo si escribió uno.
      fields.columns = (fields.columns || [])
        .map(c => (typeof c === 'string' ? { field: c } : (c || {})))
        .filter(c => String(c.field || '').trim() !== '')
        .map(c => {
          const field = String(c.field).trim();
          const label = String(c.label || '').trim();
          return label ? { field, label } : { field };
        });
      fields.filters = (fields.filters || []).map(c => conditionToPayload(c, this.schema.numeric_fields || []));
      // Tendencia: sin columna elegida, «sin tendencia» (null, no "").
      fields.trend_by = String(fields.trend_by || '').trim() || null;
      // Un orden que ya no está entre las opciones (columna, dimensión o métrica quitada) no se
      // envía: el select ya muestra «Sin orden».
      if (!fields.sort_by || !this.sortOptions.some(o => o.value === this.sortValue)) fields.sort_by = null;
      // Solo el ranking recorta filas: en el resto un `limit` guardado no se envía.
      if (!this.drawerCapabilities.limit || fields.limit === '' || fields.limit == null
          || Number.isNaN(Number(fields.limit))) fields.limit = null;
      else fields.limit = Number(fields.limit);

      const style = JSON.parse(JSON.stringify(draft.style || {}));
      const title = String(draft.title || '').trim() || fallbackTitle;
      style.title = title;
      return { title, fields, style };
    },

    // «Guardar» del panel (y «Aplicar y guardar» / «Deshacer» del chat, con `fromAssistant`).
    // Devuelve si se guardó. Un guardado manual deja sin «Deshacer» a la propuesta aplicada.
    async saveDrawer({ toast = 'Cambios guardados.', fromAssistant = false } = {}) {
      const w = this.editingWidget;
      if (!w) return false;
      if (!this.drawerDraft.source) {
        this.drawerSaveError = 'Elige la fuente de datos del widget.';
        return false;
      }
      this.drawerSaving = true;
      this.drawerSaveError = '';
      try {
        const { title, fields, style } = this._draftPayload(w.title);

        w.title = title;
        w.source_id = this.drawerDraft.source;
        w.fields = fields;
        w.style = style;
        w._dirty = true;
        w.updateChrome?.();

        const result = await this._saveWidget(w);
        if (!result.ok) {
          this.drawerSaveError = result.error || 'No se pudo guardar. Revisa tu conexión e inténtalo de nuevo.';
          return false;
        }
        if (!fromAssistant) this._clearAdviceUndo();
        // El panel se queda abierto: «Guardar» no cierra (lo hace «Cancelar» o la ✕).
        if (typeof window.showToast === 'function') window.showToast(toast);
        return true;
      } catch (e) {
        this.drawerSaveError = e.message;
        return false;
      } finally {
        this.drawerSaving = false;
      }
    },

    // ---- asistente con IA
    // Chat con la IA del widget que se edita. Cada pedido viaja con el borrador actual
    // (`current`) y los mensajes previos del hilo (`history`): la IA ajusta lo que hay en vez
    // de crear desde cero. La respuesta no toca el borrador: queda en el hilo como pasos, con
    // su botón «Aplicar y guardar».
    async askAssistant() {
      const prompt = (this.drawerDraft.prompt || '').trim();
      if (!prompt || this.drawerAsking || this.editingId == null) return;
      if (!this.assistantThreads[this.editingId]) this.assistantThreads[this.editingId] = [];
      const thread = this.assistantThreads[this.editingId];
      const history = thread
        .map(m => (m.role === 'user' ? { role: 'user', text: m.text }
          : m.proposal ? { role: 'assistant', proposal: m.proposal } : null))
        .filter(Boolean);
      const current = this._draftPayload(this.editingWidget ? this.editingWidget.title : '');
      thread.push({ role: 'user', text: prompt });
      this.drawerDraft.prompt = '';
      this.drawerAsking = true;
      try {
        const { r, data } = await fetchJsonSafe(apiUrl(`/api/dashboard/${this.dashboardId}/table-assistant/`), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ prompt, source: this.drawerDraft.source, widget_type: this.editingType, current, history }),
        });
        if (!r.ok || !data) throw new Error((data && data.error) || 'El servidor no respondió correctamente.');
        // `baseStyle`: el estilo del panel al pedir, para mostrar solo lo que la propuesta cambia.
        thread.push({ role: 'assistant', proposal: data, baseStyle: current.style || {}, applied: false, undo: null });
      } catch (e) {
        thread.push({
          role: 'assistant',
          error: e.name === 'AbortError' ? 'La IA tardó demasiado en responder. Intenta de nuevo.' : e.message,
        });
      } finally {
        this.drawerAsking = false;
      }
    },

    // El hilo del widget que se edita (vive en memoria mientras el tablero está abierto).
    get drawerThread() {
      return (this.editingId != null && this.assistantThreads[this.editingId]) || [];
    },

    clearThread() {
      if (this.editingId != null) this.assistantThreads[this.editingId] = [];
    },

    // Pedidos sugeridos por la IA para un tipo de widget sobre una fuente (dependen de la hoja
    // y del tipo, no del widget). Se piden una vez por tipo, fuente y sesión; si fallan, el
    // chat queda sin tags. Con `refresh` («otras ideas») el servidor salta su caché y evita las
    // que ya se ven; si no llegan nuevas, se quedan las actuales.
    _suggestionKey(type, source = this.drawerDraft && this.drawerDraft.source) {
      return `${type}:${source || ''}`;
    },

    async loadSuggestions(type, { refresh = false } = {}) {
      const source = this.drawerDraft && this.drawerDraft.source;
      const key = this._suggestionKey(type, source);
      if (!type || !source || this._suggestionsLoading[key]) return;
      if (!refresh && key in this.assistantSuggestions) return;
      const current = this.assistantSuggestions[key] || [];
      const params = new URLSearchParams({ widget_type: type, source });
      if (refresh) {
        params.set('refresh', '1');
        current.forEach(s => params.append('avoid', s));
      }
      this._suggestionsLoading[key] = true;
      try {
        const { r, data } = await fetchJsonSafe(
          apiUrl(`/api/dashboard/${this.dashboardId}/widget-suggestions/?${params}`));
        const list = (r.ok && data && Array.isArray(data.suggestions)) ? data.suggestions : [];
        this.assistantSuggestions[key] = list.length || !refresh ? list : current;
      } catch (e) {
        this.assistantSuggestions[key] = current;
      } finally {
        delete this._suggestionsLoading[key];
      }
    },

    get drawerSuggestionsLoading() {
      return !!(this.editingType && this._suggestionsLoading[this._suggestionKey(this.editingType)]);
    },

    refreshSuggestions() {
      this.loadSuggestions(this.editingType, { refresh: true });
    },

    get drawerSuggestions() {
      return (this.editingType && this.assistantSuggestions[this._suggestionKey(this.editingType)]) || [];
    },

    registerStyleGroup(group, keys) {
      if (!this.editingType) return;
      const groups = this.styleGroups[this.editingType] || (this.styleGroups[this.editingType] = {});
      (keys || []).forEach(key => { groups[key] = group; });
    },

    // Un clic en un tag lo envía al chat.
    askSuggestion(text) {
      if (this.drawerAsking) return;
      this.drawerDraft.prompt = text;
      this.askAssistant();
    },

    // Pasos de una propuesta de la IA, en el orden del panel y con el nombre de su bloque:
    // qué cambia si se aplica.
    adviceSteps(a, baseStyle = {}) {
      if (!a) return [];
      const f = a.fields || {};
      const list = (values) => values.join(' › ');
      const steps = [];
      // Campos calculados que la IA propone crear en la fuente (los usa en las métricas).
      if ((a.calculated_fields || []).length) {
        steps.push({ title: 'Campos calculados (se crean en la fuente)',
                     details: a.calculated_fields.map(cf => `«${cf.name}» = ${cf.formula}`) });
      }
      // Escalas que la IA corrige en la fuente (las usa la matriz de correlación).
      if ((a.column_scales || []).length) {
        steps.push({ title: 'Escala de las variables (se guarda en la fuente)',
                     details: a.column_scales.map(p => `${p.field}: ${this.scaleLabel(p.scale).toLowerCase()}${p.order && p.order.length ? ` (${p.order.join(' < ')})` : ''}`) });
      }
      const columns = (f.columns || []).map(c => (typeof c === 'string' ? c : c.label ? `${c.field} («${c.label}»)` : c.field));
      if (columns.length) steps.push({ title: this.columnsLabel, detail: `Muestra, en este orden: ${list(columns)}` });
      if ((f.dimensions || []).length) {
        // Un widget que da otro sentido a `dimensions` (dispersión, filtros, ranking) lo nombra
        // en su etiqueta: el detalle es solo la lista, sin «Agrupa».
        const label = this.drawerCapabilities.dimensions_label;
        steps.push({ title: label || 'Dimensiones', detail: label ? list(f.dimensions) : `Agrupa, en este orden: ${list(f.dimensions)}` });
      }
      if ((f.pivots || []).length) steps.push({ title: 'Pivotes', detail: `Desagrega por: ${list(f.pivots)}` });
      if ((f.filters || []).length) {
        steps.push({ title: 'Filtros', details: f.filters.map(c => this._describeCondition(c)) });
      }
      const metrics = f.metrics || [];
      if (metrics.length) {
        steps.push({ title: 'Métricas', details: metrics.map(m => {
          // `metricName` ya incluye las condiciones propias de la métrica.
          const what = this.metricName(m);
          return [what, m.label ? `como «${m.label}»` : ''].filter(Boolean).join(' ');
        }) });
      }
      if (f.trend_by) steps.push({ title: 'Tendencia', detail: `Mini línea por ${f.trend_by}` });
      if (f.sort_by) {
        const descending = f.sort_by.startsWith('-');
        const key = f.sort_by.replace(/^-/, '');
        const metric = metrics.find(m => m.alias === key);
        steps.push({ title: 'Orden', detail: `Ordena por ${metric ? (metric.label || this.metricName(metric)) : key}, ${descending ? 'de mayor a menor' : 'de menor a mayor'}` });
      }
      if (f.limit) steps.push({ title: 'Límite', detail: `Muestra solo las primeras ${f.limit} filas` });
      // Estilo: solo lo que cambia respecto al panel al pedir (la IA devuelve el estilo
      // completo), agrupado como en el panel: los controles con `group` se editan en
      // «Configurar» (ej. «Tarjeta KPI»); el resto, en «Personalizar».
      const changed = Object.entries(a.style || {})
        .filter(([key, value]) => key !== 'title' && JSON.stringify(value) !== JSON.stringify(baseStyle[key]));
      const groups = new Map();
      (this.drawerManifest.style_schema || []).forEach(control => {
        const entry = changed.find(([key]) => key === control.key);
        const detail = entry && this._describeStyle(entry[0], entry[1], metrics);
        if (!detail) return;
        const title = ((this.styleGroups[this.editingType] || {})[control.key]) || 'Personalizar';
        if (!groups.has(title)) groups.set(title, []);
        groups.get(title).push(detail);
      });
      const personalizar = groups.get('Personalizar');
      groups.delete('Personalizar');
      groups.forEach((details, title) => steps.push({ title, details }));
      if (personalizar) steps.push({ title: 'Personalizar', details: personalizar });
      return steps;
    },

    // «ventas es mayor que 100», «anio está en 2025, 2026», «mes es igual a Este mes (1-12)».
    _describeCondition(c) {
      const op = opMeta(c.op);
      if (!op.needsValue) return `${c.field} ${op.label}`;
      if (c.relative) {
        const relative = RELATIVE_VALUES.find(o => o.value === c.relative);
        return `${c.field} ${op.label} ${relative ? relative.label.toLowerCase() : c.relative}`;
      }
      const value = Array.isArray(c.value)
        ? c.value.join(op.isRange ? ' y ' : ', ')
        : String(c.value ?? '');
      return `${c.field} ${op.label} ${value}`;
    },

    // Una clave de `style` con su nombre del style_schema y su valor legible.
    _describeStyle(key, value, metrics) {
      const control = (this.drawerManifest.style_schema || []).find(c => c.key === key);
      if (!control || value === '' || value == null) return '';
      let shown = value;
      if (control.type === 'boolean') {
        shown = value ? 'sí' : 'no';
      } else if (control.type === 'list') {
        shown = value.join(', ');
      } else if (control.type === 'choice') {
        const option = (control.options || []).find(o => o.value === value);
        const metric = control.options_from === 'metrics' ? metrics.find(m => m.alias === value) : null;
        shown = option ? option.label : metric ? (metric.label || this.metricName(metric)) : value;
      }
      return `${control.label}: ${shown}`;
    },

    // Aplica la propuesta de un mensaje del hilo y guarda: el widget se redibuja al momento.
    // El estilo propuesto se suma al actual (no se pierde la personalización) y el título no
    // cambia: la IA no lo genera. El mensaje guarda el estado anterior para «Deshacer».
    async applyAdvice(message) {
      const a = message && message.proposal;
      if (!a || this.drawerSaving) return;
      // Los campos calculados que propone la IA se crean primero en la fuente: las métricas
      // de la propuesta los usan.
      if ((a.calculated_fields || []).length && !await this._createCalculatedFields(a.calculated_fields)) return;
      if ((a.column_scales || []).length) {
        try {
          await this.saveScales(this.drawerDraft.source, this._scalesMap(a.column_scales));
        } catch (e) {
          this.drawerSaveError = `No se pudo guardar la escala de las variables: ${e.message}`;
          return;
        }
      }
      const before = this._draftPayload(this.editingWidget ? this.editingWidget.title : '');
      const draft = this.drawerDraft;
      draft.fields = { ...EMPTY_FIELDS(), ...JSON.parse(JSON.stringify(a.fields || {})) };
      draft.style = { ...(draft.style || {}), ...JSON.parse(JSON.stringify(a.style || {})) };
      this._normalizeDraft();
      // Si no se guarda, el panel queda con la propuesta y el error en el pie: «Guardar» reintenta.
      if (!await this.saveDrawer({ toast: 'Propuesta aplicada y guardada.', fromAssistant: true })) return;
      this._clearAdviceUndo();
      message.applied = true;
      message.undo = before;
    },

    async _createCalculatedFields(fields) {
      const source = this.drawerDraft.source;
      this.drawerSaveError = '';
      try {
        await this.addCalculatedFields(source, fields);
      } catch (e) {
        this.drawerSaveError = `No se pudo crear el campo calculado: ${e.message}`;
        return false;
      }
      await this.loadSchema(source);
      return true;
    },

    // Crea en la fuente los campos calculados que propone la IA (los que ya existen con la
    // misma fórmula se dejan). Lanza con el mensaje del servidor si falla.
    async addCalculatedFields(source, fields) {
      const r = await fetch(apiUrl(`/api/sources/${source}/calculated-fields/`), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ fields }),
      });
      const data = await r.json().catch(() => null);
      if (!r.ok || !data) throw new Error((data && data.error) || `Error ${r.status}`);
      // El schema cacheado no tiene los campos nuevos.
      delete this.schemas[source];
    },

    // Vuelve al estado previo a la última propuesta aplicada y lo guarda.
    async undoAdvice(message) {
      const before = message && message.undo;
      if (!before || this.drawerSaving) return;
      const draft = this.drawerDraft;
      draft.title = before.title;
      draft.fields = { ...EMPTY_FIELDS(), ...JSON.parse(JSON.stringify(before.fields || {})) };
      draft.style = JSON.parse(JSON.stringify(before.style || {}));
      this._normalizeDraft();
      if (!await this.saveDrawer({ toast: 'Cambios deshechos.', fromAssistant: true })) return;
      message.undo = null;
      message.applied = false;
    },

    // Solo la última propuesta aplicada se puede deshacer, y hasta el siguiente guardado manual.
    _clearAdviceUndo() {
      this.drawerThread.forEach(m => { if (m.undo) m.undo = null; });
    },

    drawerDraft: { title: '', source: null, fields: { dimensions: [], pivots: [], metrics: [], filters: [], columns: [], sort_by: null, limit: null }, style: {}, prompt: '' },
    // Hilos del chat con la IA por id de widget: { [id]: [{role, text} | {role, proposal, applied, undo} | {role, error}] }.
    assistantThreads: {},
    // Bloque de «Configurar» de cada clave de style, por tipo: { [type]: { [key]: grupo } }.
    // Lo registra el partial que pinta esos controles (registerStyleGroup); el resto es de
    // «Personalizar». El chat agrupa así los cambios de estilo de una propuesta.
    styleGroups: {},
    // Pedidos sugeridos por tipo de widget: { [type]: [texto, texto] }.
    assistantSuggestions: {},
    _suggestionsLoading: {},
    drawerAskOpen: false,
    drawerAsking: false,
    drawerSaving: false,
    drawerSaveError: '',
  });
});
