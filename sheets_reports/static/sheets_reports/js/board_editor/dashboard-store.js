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

// Tipos de fila de métrica: una agregación o un cálculo entre las métricas anteriores
// (se serializa como {"type": "formula", "expression"}, que el motor ya evalúa).
// «Mostrar como» de una métrica (`metric.window`): cómo se muestra cada valor respecto a los demás.
const WINDOW_OPTIONS = [
  { value: 'percent_of_total', label: '% del total' },
  { value: 'percent_of_row', label: '% de la fila' },
  { value: 'running_total', label: 'Acumulado' },
  { value: 'pct_change', label: 'Variación vs anterior' },
];

// Ventanas que valen con pivotes (PIVOT_WINDOWS en services/ai_spec.py).
const PIVOT_WINDOWS = ['percent_of_total', 'percent_of_row'];

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
    // Fuentes de datos del tablero ([{id, label}]) y el schema de cada una, pedido al abrir el
    // panel de un widget. `schema` es siempre el de la fuente del widget que se edita.
    sources: [],
    schemas: {},
    schema: { all_fields: [], numeric_fields: [], dimension_fields: [], time_fields: [], sample_values: {} },
    widgetManifest: window.WIDGET_MANIFEST || {},
    schemaError: '',
    _nextId: -1,
    listSortables: {},

    // Schema de una fuente (cacheado por id). Lo deja en `schema` para el panel.
    async loadSchema(sourceId) {
      const empty = { all_fields: [], numeric_fields: [], dimension_fields: [], time_fields: [], sample_values: {} };
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
          const { widget_manifest: manifest, ...schema } = data;
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
      return {
        id: w.id,
        type: w.type,
        source_id: w.source_id ?? null,
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
      this.sources = (data.dashboard && data.dashboard.sources) || [];
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
      }
    },

    addWidget(type) {
      const manifest = this.widgetManifest[type] || {};
      const widget = WidgetRegistry.create(type, {
        id: this._nextId--,
        // Un widget nuevo usa la primera fuente del tablero; se cambia en su panel.
        source_id: this.sources.length ? this.sources[0].id : null,
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
      delete this.assistantThreads[id];
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

    // Límites de las listas del panel (capabilities del backend).
    get hasColumns() { return (this.drawerCapabilities.columns || [0, 0])[1] > 0; },
    get maxColumns() { return (this.drawerCapabilities.columns || [0, 0])[1]; },
    get columnsLabel() { return this.drawerCapabilities.columns_label || 'Columnas a mostrar'; },
    get hasMetrics() { return (this.drawerCapabilities.metrics || [0, 0])[1] > 0; },
    get maxMetrics() { return (this.drawerCapabilities.metrics || [0, 0])[1]; },
    // Condiciones propias de una métrica: solo sirven junto a otras métricas (Electrónica vs
    // total); con una sola equivalen a los filtros del widget.
    get hasMetricFilters() { return this.maxMetrics > 1; },
    get maxDimensions() { return (this.drawerCapabilities.dimensions || [0, 0])[1]; },
    get maxPivots() { return (this.drawerCapabilities.pivots || [0, 0])[1]; },
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
    // Opciones de «Mostrar como» para el borrador: las que admite el widget
    // (capabilities.windows) y, de ellas, las que valen con sus pivotes; [] = sin select.
    // Mismas reglas que form_errors en el backend.
    get metricWindowOptions() {
      const allowed = this.drawerCapabilities.windows || [];
      const hasPivots = (this.drawerDraft.fields.pivots || []).some(Boolean);
      // Con pivote solo los porcentajes se calculan por celda; «% de la fila» necesita pivote.
      const options = WINDOW_OPTIONS.filter(o => allowed.includes(o.value)
        && (hasPivots ? PIVOT_WINDOWS.includes(o.value) : o.value !== 'percent_of_row'));
      return options.length ? [{ value: '', label: 'Valor' }, ...options] : [];
    },
    // ¿La ventana guardada en la métrica ya no vale (ej. se agregó un pivote)? Se quita al guardar.
    metricWindowInvalid(metric) {
      const type = metric && metric.window && metric.window.type;
      return !!type && !this.metricWindowOptions.some(o => o.value === type);
    },
    setMetricWindow(metric, type) {
      if (type) metric.window = { type };
      else delete metric.window;
    },
    get calcOpOptions() { return CALC_OP_OPTIONS; },
    // Opciones de un select que elige una métrica del widget (roles del KPI): las fijas que
    // pasa el partial (`base`, ej. «Primera métrica») + las métricas del borrador.
    metricRoleOptions(base = []) {
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
      this.drawerAskOpen = false;
      this.drawerSaveError = '';
      this.drawerSaving = false;
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
        if (this.editingId === id) this._prefillDraft();
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
      this.editingId = null;
      this.editingType = null;
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
      const toDraft = c => (c && typeof c === 'object' && c._k ? c : conditionFromPayload(c || {}));
      f.filters = f.filters.map(toDraft);
      f.metrics.forEach(m => {
        if (!m || m.type === 'formula') return;
        m.filters = (Array.isArray(m.filters) ? m.filters : []).map(toDraft);
      });
      // Las fórmulas que llegan solo con `expression` (las de la IA) se leen a los selects si
      // encajan en una operación; si no, quedan como fórmula personalizada (solo lectura).
      f.metrics.forEach((m, index) => {
        if (!m || m.type !== 'formula' || m.op || !m.expression) return;
        const before = f.metrics.slice(0, index).map(x => (x || {}).alias).filter(Boolean);
        const parsed = this._parseFormula(m.expression, before);
        if (parsed) Object.assign(m, parsed);
      });
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
      metrics.push({ field, agg: 'sum', alias: this._autoAlias('sum', field, metrics), filters: [] });
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
        delete metric.filters;
        delete metric.window;
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
        metric.filters = [];
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

    // Inversa de `_formulaFor`: lee una expresión (ej. la que genera la IA, «a - b» o
    // «a / b * 100») de vuelta a los selects {left, op, rightKind, right}. `aliases` son las
    // métricas que puede usar (las anteriores). null si no encaja en una operación del panel.
    _parseFormula(expression, aliases) {
      const ID = '[A-Za-z_][A-Za-z0-9_]*';
      const NUM = '-?\\d+(?:\\.\\d+)?';
      const OPERAND = `(${ID}|${NUM})`;
      const wrapsAll = (t) => {
        let depth = 0;
        for (let i = 0; i < t.length; i++) {
          if (t[i] === '(') depth++;
          else if (t[i] === ')' && --depth === 0 && i < t.length - 1) return false;
        }
        return depth === 0;
      };
      let text = String(expression || '').trim();
      while (text.startsWith('(') && text.endsWith(')') && wrapsAll(text)) text = text.slice(1, -1).trim();
      const isAlias = (a) => aliases.includes(a);
      const rightOf = (r) => (isAlias(r) ? { rightKind: 'metric', right: r }
        : new RegExp(`^${NUM}$`).test(r) ? { rightKind: 'number', right: r } : null);
      const match = (pattern) => text.match(new RegExp(`^${pattern}$`));
      let m = match(`\\(\\s*(${ID})\\s*-\\s*${OPERAND}\\s*\\)\\s*/\\s*${OPERAND}\\s*\\*\\s*100`);
      if (m && m[2] === m[3] && isAlias(m[1]) && rightOf(m[2])) return { left: m[1], op: 'diff_pct', ...rightOf(m[2]) };
      m = match(`(${ID})\\s*/\\s*${OPERAND}\\s*\\*\\s*100`);
      if (m && isAlias(m[1]) && rightOf(m[2])) return { left: m[1], op: 'ratio_pct', ...rightOf(m[2]) };
      m = match(`(${ID})\\s*([-+*/])\\s*${OPERAND}`);
      if (m && isAlias(m[1]) && rightOf(m[3])) {
        const op = { '-': 'sub', '+': 'add', '*': 'mul', '/': 'div' }[m[2]];
        return { left: m[1], op, ...rightOf(m[3]) };
      }
      return null;
    },

    // Una fórmula personalizada (sin operación del panel) pasa a editarse con los selects.
    editFormulaWithSelects(index) {
      const metric = (this.drawerDraft.fields.metrics || [])[index];
      if (!metric || metric.type !== 'formula') return;
      Object.assign(metric, { op: 'sub', left: '', rightKind: 'metric', right: '' });
      this.reconcileFormulas();
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
        if (!metric.op && metric.expression) return `Cálculo: ${metric.expression}`;
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
      let alias = /^[a-z]/.test(slug) ? slug : `m_${slug}`;
      let n = 2;
      const used = new Set(taken || []);
      while (used.has(alias)) alias = `${slug.slice(0, 30)}_${n++}`;
      return alias;
    },

    get aggOptions() { return AGG_OPTIONS; },

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
      // Un cálculo a medias tampoco (`pruneFormulas` lo quita además del borrador al guardar).
      fields.metrics = (fields.metrics || []).filter(m => !(m && m.type === 'formula' && !m.expression));
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
      if (!fields.sort_by) fields.sort_by = null;
      if (fields.limit === '' || fields.limit == null || Number.isNaN(Number(fields.limit))) fields.limit = null;
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
        // Un cálculo a medias no va al servidor: se descarta del borrador antes de copiarlo.
        this.pruneFormulas();
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
      const columns = (f.columns || []).map(c => (typeof c === 'string' ? c : c.label ? `${c.field} («${c.label}»)` : c.field));
      if (columns.length) steps.push({ title: this.columnsLabel, detail: `Muestra, en este orden: ${list(columns)}` });
      if ((f.dimensions || []).length) steps.push({ title: this.drawerCapabilities.dimensions_label || 'Dimensiones', detail: `Agrupa, en este orden: ${list(f.dimensions)}` });
      if ((f.pivots || []).length) steps.push({ title: 'Pivotes', detail: `Desagrega por: ${list(f.pivots)}` });
      if ((f.filters || []).length) {
        steps.push({ title: 'Filtros', details: f.filters.map(c => this._describeCondition(c)) });
      }
      const metrics = f.metrics || [];
      if (metrics.length) {
        steps.push({ title: 'Métricas', details: metrics.map(m => {
          // `metricName` ya incluye las condiciones propias de la métrica.
          const what = m.type === 'formula' ? `Cálculo: ${m.expression || ''}` : this.metricName(m);
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
