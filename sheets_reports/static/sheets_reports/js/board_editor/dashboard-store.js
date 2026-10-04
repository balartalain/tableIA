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

// `window.apiUrl` lo define base.html con el prefijo de despliegue (SCRIPT_NAME).

const EMPTY_FIELDS = () => ({
  dimensions: [],
  pivots: [],
  metrics: [],
  filters: [],
  columns: [],
  sort_by: null,
  limit: null,
});

// Operadores del motor de filtros (dsl/conditions.py) con su etiqueta para el editor.
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
  { value: 'current_month', label: 'Este mes (1-12)' },
  { value: 'max', label: 'El último valor de la columna' },
  { value: 'second_max', label: 'El anterior al último' },
  { value: 'min', label: 'El primer valor de la columna' },
];

const AGG_OPTIONS = [
  { value: 'sum', label: 'Suma' },
  { value: 'avg', label: 'Promedio' },
  { value: 'median', label: 'Mediana' },
  { value: 'min', label: 'Mínimo' },
  { value: 'max', label: 'Máximo' },
  { value: 'std', label: 'Desviación estándar' },
  { value: 'count', label: 'Conteo de filas' },
  { value: 'count_distinct', label: 'Valores distintos' },
];

let CONDITION_SEQ = 0;

// Nombre visible sin «Nombre a mostrar»: el mismo texto que arma el backend (`humanize`).
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
    editingId: null,
    editingType: null,
    dashboardId: window.DASHBOARD_ID,
    drawerTab: 'data',
    schema: { all_fields: [], numeric_fields: [], dimension_fields: [], sample_values: {} },
    widgetManifest: window.WIDGET_MANIFEST || {},
    schemaError: '',
    _nextId: -1,
    listSortables: {},

    async loadSchema() {
      try {
        const r = await fetch(apiUrl(`/api/dashboard/${this.dashboardId}/schema/`));
        const data = await r.json().catch(() => null);
        if (r.ok && data) {
          const { widget_manifest: manifest, ...schema } = data;
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
      return {
        id: w.id,
        type: w.type,
        title: w.title || 'Nuevo Widget',
        position: w.position || { x: 0, y: 0, w: 6, h: 300 },
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
      const { r, data } = await fetchJsonSafe(this._renderUrl(), {}, RENDER_FETCH_TIMEOUT_MS);
      if (!r.ok || !data) throw new Error((data && data.error) || 'No se pudo cargar el tablero');
      this._reportFilterErrors(data);
      // Instancias reales (mount/applyRender/setLoading), no objetos planos.
      this.widgets = data.widgets
        .map(w => BaseWidget.fromServer(this.normalizeWidget(w)))
        .filter(Boolean)
        .sort((a, b) => (a.position?.y ?? 0) - (b.position?.y ?? 0));
      return Object.fromEntries(data.widgets.map(w => [w.id, w]));
    },

    async refreshData() {
      const saved = this.widgets.filter(w => w.id > 0 && w.fields);
      saved.forEach(w => w.setLoading(true));
      try {
        const { r, data } = await fetchJsonSafe(this._renderUrl(), {}, RENDER_FETCH_TIMEOUT_MS);
        if (!r.ok || !data) {
          const message = (data && data.error) || `Error ${r.status} al cargar los datos`;
          saved.forEach(w => { w.setLoading(false); w.renderError(message, { retryable: true }); });
          return;
        }
        this._reportFilterErrors(data);
        const byId = Object.fromEntries(data.widgets.map(w => [w.id, w]));
        saved.forEach(w => w.applyRender(byId[w.id]));
      } catch (e) {
        saved.forEach(w => { w.setLoading(false); w.renderError('No se pudo conectar con el servidor', { retryable: true }); });
      }
    },

    addWidget(type) {
      const manifest = this.widgetManifest[type] || {};
      const widget = WidgetRegistry.create(type, {
        id: this._nextId--,
        position: { x: 0, y: this.widgets.length, w: 6, h: 300 },
        title: manifest.label || 'Nuevo Widget',
        style: { ...(manifest.style_defaults || {}) },
        _dirty: false,
      });
      this.widgets.push(widget);
      return widget;
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
          w.id = data.id;
          w.rebuildElement();
        }
        w._dirty = false;
        w.applyRender(data);
        return { ok: true, data };
      } catch (e) {
        return { ok: false, error: 'No se pudo conectar con el servidor.' };
      }
    },

    _layoutTimer: null,
    _layoutSaving: null,

    scheduleLayoutSave() {
      clearTimeout(this._layoutTimer);
      this._layoutTimer = setTimeout(() => this.flushLayoutSave(), LAYOUT_SAVE_DELAY_MS);
    },

    async flushLayoutSave({ keepalive = false } = {}) {
      clearTimeout(this._layoutTimer);
      this._layoutTimer = null;
      if (this._layoutSaving && !keepalive) await this._layoutSaving;
      const pending = this.widgets.filter(w => w._dirty && w.id > 0);
      if (!pending.length) return;
      this._layoutSaving = Promise.all(pending.map(w => this._saveWidget(w, { keepalive })));
      const results = await this._layoutSaving;
      this._layoutSaving = null;
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
      if (id > 0) {
        try { await fetch(apiUrl(`/api/widget/${id}/`), { method: 'DELETE' }); } catch (e) {}
      }
      this.widgets = this.widgets.filter(w => w.id !== id);
    },

    reorderWidgets() {
      const canvasEl = document.getElementById('dashboard-canvas');
      if (!canvasEl) return;
      const widgetEls = canvasEl.querySelectorAll('[data-widget-id]');
      widgetEls.forEach((el, i) => {
        const id = parseInt(el.dataset.widgetId);
        const w = this.widgets.find(w => w.id === id);
        if (w && w.position?.y !== i) {
          w.position.y = i;
          w._dirty = true;
        }
      });
      this.widgets.sort((a, b) => (a.position?.y ?? 0) - (b.position?.y ?? 0));
      this.scheduleLayoutSave();
    },

    fluidStartColOnDrop(widgetEl) {
      const id = parseInt(widgetEl.dataset.widgetId);
      const w = this.widgets.find(w => w.id === id);
      if (!w || !w.position?.x) return;
      w.position.x = 0;
      w._dirty = true;
      w.updateChrome?.();
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

    // Controles de estilo del tipo: el título vive en el propio widget, no en «Personalizar»
    // y los de tipo `hidden` (totales por nivel) se pintan en «Configurar», bajo su fila.
    get drawerStyleSchema() {
      return (this.drawerManifest.style_schema || [])
        .filter(c => c.key !== 'title' && !c.hidden);
    },

    // Clave de estilo del checkbox «Mostrar totales» de la fila `idx` de dimensiones o de
    // pivotes (0 = total general, 1 = subtotales del primer nivel, 2 = del segundo).
    totalsStyleKey(kind, idx) {
      if (kind === 'column') return idx === 0 ? 'showColumnTotals' : 'columnSubtotal1';
      return idx === 0 ? 'showTotals' : (idx === 1 ? 'rowSubtotal1' : 'rowSubtotal2');
    },

    totalsTitle(kind, idx) {
      const group = kind === 'column' ? 'pivots' : 'dimensions';
      const list = (this.drawerDraft.fields && this.drawerDraft.fields[group]) || [];
      const what = list[idx - 1] || 'grupo';
      if (idx === 0) {
        return kind === 'column'
          ? 'Columna «Total general» a la derecha de la tabla'
          : 'Fila «Total general» al pie de la tabla';
      }
      return `Subtotal «Total …» de cada ${what}`;
    },

    // El control solo se pinta si el tipo de widget declaró ese nivel en su style_schema.
    totalsControl(kind, idx) {
      const key = this.totalsStyleKey(kind, idx);
      return (this.drawerManifest.style_schema || [])
        .find(c => c.key === key && c.ui === 'checkbox') || null;
    },

    get drawerTitleControl() {
      return (this.drawerManifest.style_schema || []).find(c => c.key === 'title') || null;
    },

    // Qué bloques del editor tocan a este tipo (capabilities del backend).
    get hasColumns() { return (this.drawerCapabilities.columns || [0, 0])[1] > 0; },
    get maxColumns() { return (this.drawerCapabilities.columns || [0, 0])[1]; },
    get columnsLabel() { return this.drawerCapabilities.columns_label || 'Columnas a mostrar'; },
    get hasDimensions() { return (this.drawerCapabilities.dimensions || [0, 0])[1] > 0; },
    get hasPivots() { return (this.drawerCapabilities.pivots || [0, 0])[1] > 0; },
    get hasMetrics() { return (this.drawerCapabilities.metrics || [0, 0])[1] > 0; },
    get hasFilters() { return !!this.drawerCapabilities.filters; },
    get hasSort() { return !!this.drawerCapabilities.sort; },
    get hasLimit() { return !!this.drawerCapabilities.limit; },
    get maxMetrics() { return (this.drawerCapabilities.metrics || [0, 0])[1]; },
    get maxDimensions() { return (this.drawerCapabilities.dimensions || [0, 0])[1]; },
    get maxPivots() { return (this.drawerCapabilities.pivots || [0, 0])[1]; },

    openDrawer(id) {
      const w = this.widgets.find(x => x.id === id);
      if (!w) return;
      this.editingId = id;
      this.editingType = w.type;
      this.drawerTab = 'data';
      this.drawerAskError = '';
      this.drawerAskOpen = false;
      this.drawerSaveError = '';
      this.drawerSaving = false;
      const manifest = this.widgetManifest[w.type] || {};
      const title = w.title || manifest.label || 'Nuevo Widget';
      this.drawerDraft = {
        title,
        fields: { ...EMPTY_FIELDS(), ...(w.fields || {}) },
        style: { ...(manifest.style_defaults || {}), ...(w.style || {}), title },
      };
      this._normalizeDraft();
      if (this.hasColumns) {
        if (!this.drawerDraft.fields.columns.length) this._autoPickColumns();
      } else if (!this.drawerDraft.fields.dimensions.length) {
        this._autoPickDimensions();
      }
      this.initListSortables();
    },

    // (Re)atan los arrastres de las listas del panel: se destruyen al cerrar.
    initListSortables() {
      ['columns', 'dimensions', 'pivots', 'metrics'].forEach(key => {
        const el = document.querySelector(`[data-column-list="${key}"]`);
        if (el) this.initListSortable(el, key);
      });
    },

    closeDrawer() {
      this.editingId = null;
      this.editingType = null;
      this.drawerAskError = '';
      this.drawerAskOpen = false;
      this.drawerSaveError = '';
      this.drawerTab = 'data';
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
      if (typeof f.sort_by !== 'string') f.sort_by = null;
      if (f.limit != null && f.limit !== '') f.limit = Number(f.limit);
      f.filters = f.filters.map(c => (c && typeof c === 'object' && c._k ? c : conditionFromPayload(c || {})));
    },

    // Un widget recién arrastrado empieza con las primeras columnas ya elegidas.
    _autoPickDimensions() {
      const caps = this.drawerCapabilities;
      const max = Math.min(caps.dimensions ? caps.dimensions[1] : 0, 1);
      const source = this.schema.dimension_fields && this.schema.dimension_fields.length
        ? this.schema.dimension_fields
        : this.schema.all_fields;
      this.drawerDraft.fields.dimensions = (source || []).slice(0, max);
      if ((caps.metrics || [0, 0])[0] > 0) this.addMetric();
    },

    // Una Tabla nueva arranca con las primeras columnas de la hoja.
    _autoPickColumns() {
      const max = Math.min(this.maxColumns || 0, 8);
      this.drawerDraft.fields.columns = (this.schema.all_fields || []).slice(0, max)
        .map(field => ({ field }));
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
      return (this.schema.all_fields || []).filter(c => c === current || !list.some(x => x.field === c));
    },

    addColumn() {
      const list = this.drawerDraft.fields.columns;
      if (list.length >= this.maxColumns) return;
      const field = (this.schema.all_fields || []).find(c => !list.some(x => x.field === c));
      if (field) list.push({ field });
    },

    removeColumn(index) {
      this.drawerDraft.fields.columns.splice(index, 1);
    },

    // Al cambiar la columna elegida, el «Nombre a mostrar» de esa fila deja de servir.
    onColumnFieldChange(index, field) {
      const column = this.drawerDraft.fields.columns[index];
      if (!column) return;
      if (field != null && column.field !== field) column.field = field;
      column.label = '';
    },

    // Nombre que se muestra en la tabla: el «Nombre a mostrar» o la columna tal cual.
    columnLabel(column) {
      return (column && column.label) || humanizeName((column && column.field) || '');
    },

    // ---- métricas
    addMetric() {
      const metrics = this.drawerDraft.fields.metrics;
      if (metrics.length >= (this.drawerCapabilities.metrics || [0, 99])[1]) return;
      const field = (this.schema.numeric_fields || []).find(f => !metrics.some(m => m.field === f))
        || (this.schema.all_fields || [])[0] || '';
      metrics.push({ field, agg: 'sum', alias: this._autoAlias('sum', field, metrics) });
    },

    removeMetric(index) {
      this.drawerDraft.fields.metrics.splice(index, 1);
    },

    onMetricFieldChange(metric) {
      const taken = this.drawerDraft.fields.metrics.filter(m => m !== metric).map(m => m.alias);
      metric.alias = this._autoAlias(metric.agg, metric.field, taken);
    },

    // Nombre visible de la métrica: «Nombre a mostrar» o, si está vacío, el agg en español
    // con el campo («Promedio Ventas»), igual que en las cabeceras del backend.
    metricName(metric) {
      const agg = (this.aggOptions.find(o => o.value === (metric.agg || '')) || {}).label
        || (metric.agg || '');
      return `${agg} ${humanizeName(metric.field)}`.trim();
    },

    _autoAlias(agg, field, taken) {
      const base = (agg && field) ? `${agg}_${field}` : (field || 'metrica');
      const slug = base.toLowerCase().replace(/[^a-z0-9_]+/g, '_').replace(/^_+|_+$/g, '') || 'metrica';
      let alias = /^[a-z]/.test(slug) ? slug : `m_${slug}`;
      let n = 2;
      const used = new Set(taken || []);
      while (used.has(alias)) alias = `${slug.slice(0, 30)}_${n++}`;
      return alias;
    },

    get aggOptions() { return AGG_OPTIONS; },

    // ---- filtros
    addFilter() {
      const filters = this.drawerDraft.fields.filters;
      if (filters.length >= 20) return;
      filters.push(conditionFromPayload({ field: (this.schema.all_fields || [])[0] || '', op: 'eq' }));
    },

    removeFilter(index) {
      this.drawerDraft.fields.filters.splice(index, 1);
    },

    get filterOpOptions() { return FILTER_OPS; },
    get relativeOptions() { return RELATIVE_VALUES; },

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
    async saveDrawer() {
      const w = this.editingWidget;
      if (!w) return;
      this.drawerSaving = true;
      this.drawerSaveError = '';
      try {
        const draft = this.drawerDraft;
        const fields = JSON.parse(JSON.stringify(draft.fields));
        // Una fila a medio elegir («— elegir —») no se envía al servidor.
        ['dimensions', 'pivots'].forEach(k => {
          fields[k] = (fields[k] || []).filter(v => v !== '' && v != null);
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
        fields.filters = fields.filters.map(c => conditionToPayload(c, this.schema.numeric_fields || []));
        if (!fields.filters.length) fields.filters = [];
        if (!fields.sort_by) fields.sort_by = null;
        if (fields.limit === '' || fields.limit == null || Number.isNaN(Number(fields.limit))) fields.limit = null;
        else fields.limit = Number(fields.limit);

        const style = JSON.parse(JSON.stringify(draft.style || {}));
        const title = String(draft.title || '').trim() || w.title;
        style.title = title;

        w.title = title;
        w.fields = fields;
        w.style = style;
        w._dirty = true;
        w.updateChrome?.();

        const result = await this._saveWidget(w);
        if (!result.ok) {
          this.drawerSaveError = result.error || 'No se pudo guardar. Revisa tu conexión e inténtalo de nuevo.';
          return;
        }
        // El panel se queda abierto: «Guardar» no cierra (lo hace «Cancelar» o la ✕).
        if (typeof window.showToast === 'function') window.showToast('Cambios guardados.');
      } catch (e) {
        this.drawerSaveError = e.message;
      } finally {
        this.drawerSaving = false;
      }
    },

    // ---- asistente con IA
    async askAssistant() {
      const prompt = (this.drawerDraft.prompt || '').trim();
      if (!prompt) return;
      this.drawerAsking = true;
      this.drawerAskError = '';
      this.drawerAdvice = null;
      try {
        const { r, data } = await fetchJsonSafe(apiUrl(`/api/dashboard/${this.dashboardId}/table-assistant/`), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ prompt, widget_type: this.editingType }),
        });
        if (!r.ok || !data) throw new Error((data && data.error) || 'El servidor no respondió correctamente.');
        this.drawerAdvice = data;
        this.applyAdvice();
      } catch (e) {
        this.drawerAskError = e.name === 'AbortError'
          ? 'La IA tardó demasiado en responder. Intenta de nuevo.'
          : e.message;
      } finally {
        this.drawerAsking = false;
      }
    },

    applyAdvice() {
      const a = this.drawerAdvice;
      if (!a) return;
      const draft = this.drawerDraft;
      draft.fields = { ...EMPTY_FIELDS(), ...(a.fields || {}) };
      draft.style = { ...(this.drawerManifest.style_defaults || {}), ...(a.style || {}) };
      if (a.title) draft.title = a.title;
      this._normalizeDraft();
      this.drawerAdvice = null;
      this.drawerAskOpen = false;
      if (typeof window.showToast === 'function') {
        window.showToast('Configuración propuesta por la IA. Revisa y guarda.');
      }
    },

    drawerDraft: { title: '', fields: { dimensions: [], pivots: [], metrics: [], filters: [], columns: [], sort_by: null, limit: null }, style: {}, prompt: '' },
    drawerAdvice: null,
    drawerAskOpen: false,
    drawerAsking: false,
    drawerAskError: '',
    drawerSaving: false,
    drawerSaveError: '',
  });
});
