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

// Tipos de fila de métrica: una agregación o un cálculo entre las métricas anteriores
// (se serializa como {"type": "formula", "expression"}, que el motor ya evalúa).
const METRIC_TYPE_OPTIONS = [
  { value: 'agg', label: 'Agregación' },
  { value: 'formula', label: 'Cálculo entre métricas' },
];

// Operaciones del cálculo: `symbol` para el nombre visible, `human` para el rótulo.
const CALC_OP_OPTIONS = [
  { value: 'sub', label: '− menos', symbol: '-', human: 'Diferencia' },
  { value: 'add', label: '+ más', symbol: '+', human: 'Suma' },
  { value: 'mul', label: '× por', symbol: '*', human: 'Producto' },
  { value: 'div', label: '÷ entre', symbol: '/', human: 'División' },
  { value: 'ratio_pct', label: 'como % de', symbol: '%', human: 'Porcentaje' },
  { value: 'diff_pct', label: 'variación % vs', symbol: '±%', human: 'Variación' },
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
    get hasTrend() { return !!this.drawerCapabilities.trend; },
    // Columnas para la mini tendencia: las de dimensión (año, mes, categoría…), como en main.
    get trendOptions() {
      const dims = this.schema.dimension_fields || [];
      return dims.length ? dims : (this.schema.all_fields || []);
    },
    get hasFormulaMetrics() {
      const types = this.drawerCapabilities.metric_types;
      return types ? types.indexOf('formula') >= 0 : this.hasMetrics;
    },
    get metricTypeOptions() { return METRIC_TYPE_OPTIONS; },
    get calcOpOptions() { return CALC_OP_OPTIONS; },
    // Controles del bloque «Tarjeta KPI»: el style_schema con `group: 'card'`. Los que van
    // junto a otro control (`inline_with`) se pintan dentro de ese, no en su propia fila.
    get cardControls() {
      return (this.drawerManifest.style_schema || [])
        .filter(c => c.group === 'card' && !c.inline_with);
    },
    inlineControls(control) {
      return (this.drawerManifest.style_schema || []).filter(c => c.inline_with === control.key);
    },
    // `enabled_when` del style_schema: cada clave debe valer lo pedido (true = no vacía).
    // Misma regla que `control_enabled` en el backend.
    controlDisabled(control) {
      const style = this.drawerDraft.style || {};
      return Object.entries((control && control.enabled_when) || {})
        .some(([key, expected]) => (expected === true ? !style[key] : style[key] !== expected));
    },
    // Opciones de un control de rol del KPI: su opción vacía + las métricas del borrador.
    metricRoleOptions(control) {
      const base = (control && control.options) || [];
      const metrics = (this.drawerDraft.fields.metrics || []).filter(m => m && m.alias);
      return [...base, ...metrics.map(m => ({ value: m.alias, label: this.metricName(m) }))];
    },
    // Métricas con las que se puede calcular en la fila `idx`: solo las anteriores.
    formulaOperandOptions(idx) {
      const list = this.drawerDraft.fields.metrics || [];
      return list.slice(0, idx).filter(m => m && m.alias)
        .map(m => ({ value: m.alias, label: this.metricName(m) }));
    },

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
      // No auto-agregar métrica si ya hay métricas en el widget (evita duplicados al editar).
      if ((this.drawerCapabilities.metrics || [0, 0])[0] > 0 && !this.drawerDraft.fields.metrics.length) {
        this.addMetric();
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
      if (typeof f.trend_by !== 'string') f.trend_by = f.trend_by == null ? '' : String(f.trend_by);
      if (typeof f.sort_by !== 'string') f.sort_by = null;
      if (f.limit != null && f.limit !== '') f.limit = Number(f.limit);
      f.filters = f.filters.map(c => (c && typeof c === 'object' && c._k ? c : conditionFromPayload(c || {})));
      this.reconcileFormulas();
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
      // Medidas de verdad primero: numéricas que no son dimensiones (año, mes...
      // no se suman como métrica por defecto).
      const dims = this.schema.dimension_fields || [];
      const numeric = this.schema.numeric_fields || [];
      const used = f => metrics.some(m => m && m.field === f);
      const field = numeric.find(f => !dims.includes(f) && !used(f))
        || numeric.find(f => !used(f))
        || (this.schema.all_fields || [])[0] || '';
      metrics.push({ field, agg: 'sum', alias: this._autoAlias('sum', field, metrics) });
    },

    removeMetric(index) {
      const removed = this.drawerDraft.fields.metrics.splice(index, 1)[0];
      this._rebindAlias((removed || {}).alias, '');
      this.reconcileFormulas();
    },

    onMetricFieldChange(metric) {
      const taken = this.drawerDraft.fields.metrics.filter(m => m !== metric).map(m => m.alias);
      const oldAlias = metric.alias;
      metric.alias = this._autoAlias(metric.agg, metric.field, taken);
      if (oldAlias !== metric.alias) this._rebindAlias(oldAlias, metric.alias);
    },

    // Agregación ↔ cálculo entre métricas: los operandos del cálculo usan el alias.
    onMetricTypeChange(index, type) {
      const list = this.drawerDraft.fields.metrics;
      const metric = list[index];
      if (!metric || (metric.type || 'agg') === type) return;
      const taken = list.filter(m => m !== metric).map(m => m.alias);
      const oldAlias = metric.alias || '';
      if (type === 'formula') {
        metric.type = 'formula';
        delete metric.agg;
        delete metric.field;
        metric.op = 'div';
        const prev = list.slice(0, index).find(m => m && m.alias);
        metric.left = prev ? prev.alias : '';
        metric.rightKind = 'metric';
        metric.right = '';
      } else {
        delete metric.type;
        delete metric.op;
        delete metric.left;
        delete metric.rightKind;
        delete metric.right;
        delete metric.expression;
        metric.agg = metric.agg || 'sum';
        metric.field = metric.field || (this.schema.numeric_fields || [])[0]
          || (this.schema.all_fields || [])[0] || '';
      }
      metric.alias = type === 'formula'
        ? this._autoAlias('', `calc_${index + 1}`, taken)
        : this._autoAlias(metric.agg, metric.field, taken);
      this._rebindAlias(oldAlias, metric.alias);
      this.reconcileFormulas();
    },

    // La expression del cálculo: solo con los operandos completos y evaluables.
    formulaExpression(metric, index) {
      const list = this.drawerDraft.fields.metrics || [];
      const idx = index != null ? index : list.indexOf(metric);
      const op = (this.calcOpOptions || []).find(o => o.value === (metric && metric.op));
      if (!op) return '';
      const before = (alias) => !!alias && list.findIndex((m, i) => m && m.alias === alias && i < idx) >= 0;
      const left = String((metric && metric.left) || '').trim();
      if (!before(left)) return '';
      if ((metric.rightKind || 'number') === 'number') {
        const raw = String(metric.right ?? '').trim();
        if (raw === '' || !Number.isFinite(Number(raw))) return '';
        return this._formulaFor(op.value, left, raw);
      }
      const right = String(metric.right || '').trim();
      if (!before(right)) return '';
      return this._formulaFor(op.value, left, right);
    },

    _formulaFor(op, left, right) {
      switch (op) {
        case 'sub': return `(${left} - ${right})`;
        case 'add': return `(${left} + ${right})`;
        case 'mul': return `(${left} * ${right})`;
        case 'div': return `(${left} / ${right})`;
        case 'ratio_pct': return `(${left} / ${right} * 100)`;
        case 'diff_pct': return `((${left} - ${right}) / ${right} * 100)`;
        default: return '';
      }
    },

    onFormulaChange(index) {
      const metric = (this.drawerDraft.fields.metrics || [])[index];
      if (!metric || metric.type !== 'formula' || !metric.op) return;
      metric.expression = this.formulaExpression(metric, index);
    },

    // Recalcula las expressiones y limpia operandos que ya no existen (una fórmula que
    // vino de la IA, sin `op`, se respeta tal cual).
    reconcileFormulas() {
      const list = this.drawerDraft.fields.metrics || [];
      const aliases = new Set(list.map(m => (m || {}).alias).filter(Boolean));
      list.forEach((metric, index) => {
        if (!metric || metric.type !== 'formula' || !metric.op) return;
        if (metric.left && !aliases.has(metric.left)) metric.left = '';
        if ((metric.rightKind || 'number') !== 'number' && metric.right && !aliases.has(metric.right)) {
          metric.right = '';
        }
        metric.expression = this.formulaExpression(metric, index);
      });
    },

    // Un cálculo incompleto o que depende de una métrica posterior no se puede evaluar:
    // se quita del borrador antes de guardar, en vez de mandar un form que el backend rechaza.
    pruneFormulas() {
      this.reconcileFormulas();
      const list = this.drawerDraft.fields.metrics || [];
      const positions = new Map();
      list.forEach((m, i) => { if (m && m.alias) positions.set(m.alias, i); });
      const kept = list.filter((metric, index) => {
        if (!metric || metric.type !== 'formula') return true;
        if (!metric.op) return !!metric.expression;
        if (!metric.expression) return false;
        const leftPos = positions.get(metric.left);
        if (leftPos == null || leftPos >= index) return false;
        if ((metric.rightKind || 'number') === 'metric') {
          const rightPos = positions.get(metric.right);
          if (rightPos == null || rightPos >= index) return false;
        }
        return true;
      });
      if (kept.length !== list.length) {
        this.drawerDraft.fields.metrics = kept;
        this.reconcileFormulas();
      }
    },

    // Los controles que eligen una métrica (`options_from: 'metrics'`) y los operandos de un
    // cálculo apuntan al alias: si cambia, siguen.
    _rebindAlias(oldAlias, newAlias) {
      if (!oldAlias) return;
      const style = this.drawerDraft.style || {};
      (this.drawerManifest.style_schema || [])
        .filter(c => c.options_from === 'metrics')
        .forEach(c => { if (style[c.key] === oldAlias) style[c.key] = newAlias || ''; });
      (this.drawerDraft.fields.metrics || []).forEach(m => {
        if (!m || m.type !== 'formula') return;
        if (m.left === oldAlias) m.left = newAlias || '';
        if ((m.rightKind || 'number') !== 'number' && m.right === oldAlias) m.right = newAlias || '';
      });
    },

    // Nombre visible de la métrica: «Nombre a mostrar» o, si está vacío, el agg en español
    // con el campo («Promedio Ventas»), igual que en las cabeceras del backend.
    metricName(metric) {
      if (metric && metric.type === 'formula') {
        if (!metric.op || !metric.left || String(metric.right ?? '').trim() === '') {
          return 'Cálculo entre métricas';
        }
        const op = (this.calcOpOptions || []).find(o => o.value === metric.op) || { human: 'Cálculo', symbol: '' };
        const right = (metric.rightKind || 'number') === 'number'
          ? String(metric.right)
          : humanizeName(metric.right);
        return `${op.human} ${humanizeName(metric.left)} ${op.symbol} ${right}`.trim();
      }
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
        // Un cálculo a medias no va al servidor: se descarta del borrador antes de copiarlo.
        this.pruneFormulas();
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
        // Tendencia: sin columna elegida, «sin tendencia» (null, no "").
        fields.trend_by = String(fields.trend_by || '').trim() || null;
        if (!fields.sort_by) fields.sort_by = null;
        if (fields.limit === '' || fields.limit == null || Number.isNaN(Number(fields.limit))) fields.limit = null;
        else fields.limit = Number(fields.limit);

        const style = JSON.parse(JSON.stringify(draft.style || {}));
        const title = String(draft.title || '').trim() || w.title;
        style.title = title;
        // Un control deshabilitado que lo pide (`clear_when_disabled`) no se guarda.
        (this.drawerManifest.style_schema || [])
          .filter(c => c.clear_when_disabled && this.controlDisabled(c))
          .forEach(c => { delete style[c.key]; });

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
