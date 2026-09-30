const AI_FETCH_TIMEOUT_MS = 120000;
const RENDER_FETCH_TIMEOUT_MS = 60000;

// Aborta si tarda demasiado y nunca truena por JSON inválido (p. ej. una página HTML de error
// devuelta por un timeout de gateway/proxy) — deja que quien llama decida el mensaje de error.
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

// "Resumir por" (como en las tablas dinámicas de Sheets).
const AGG_OPTIONS = [
  { value: 'sum', label: 'Suma' },
  { value: 'count', label: 'Conteo' },
  { value: 'count_distinct', label: 'Contar únicos' },
  { value: 'avg', label: 'Promedio' },
  { value: 'min', label: 'Mínimo' },
  { value: 'max', label: 'Máximo' },
  { value: 'median', label: 'Mediana' },
];

// "Mostrar como". Qué opciones tienen sentido depende de si hay dimensión y pivote.
const SHOW_AS_LABELS = {
  value: 'Valor',
  pct_row: '% de la fila',
  pct_column: '% de la columna',
  pct_total: '% del total general',
};

// Formato anterior de porcentajes (agg pct_* + of): se traduce al abrir un widget guardado,
// igual que normalize_metric en el backend.
const LEGACY_PERCENT_AGGS = { pct_count: 'count', pct_sum: 'sum' };

// Filas y columnas elegidas en el builder, sin vacíos ni repetidos (una columna usada como
// fila no puede ser además columna de pivote).
function chosen(list) {
  return [...new Set((list || []).filter(Boolean))];
}

function builderDims(b) {
  return chosen(b.dimensions);
}

function builderPivots(b) {
  const dims = new Set(builderDims(b));
  return chosen(b.pivots).filter(p => !dims.has(p));
}

// `pivot` del spec como lista (null, "mes" o ["anio", "mes"]), como pivots_of del backend.
function pivotsOf(spec) {
  if (!spec.pivot) return [];
  return Array.isArray(spec.pivot) ? [...spec.pivot] : [spec.pivot];
}

// count cuenta filas y no usa campo.
function isCountAgg(agg) {
  return agg === 'count';
}

function normalizeMetric(m, spec) {
  if (!(m.agg in LEGACY_PERCENT_AGGS)) return { ...m, show_as: m.show_as || 'value' };
  let showAs = 'pct_total';
  if (m.of !== 'total' && (spec.dimensions || []).length) showAs = spec.pivot ? 'pct_row' : 'pct_column';
  return { ...m, agg: LEGACY_PERCENT_AGGS[m.agg], show_as: showAs };
}

// Nombre de columna resultante ("as") para una métrica del builder: snake_case ASCII, como
// exige el schema (^[a-z][a-z0-9_]{0,62}$). count no depende del campo: siempre "cantidad".
function metricAlias(agg, field, showAs) {
  const pct = showAs && showAs !== 'value' ? 'pct_' : '';
  if (agg === 'count') return `${pct}cantidad`;
  const prefix = {
    avg: 'promedio', min: 'minimo', max: 'maximo', median: 'mediana', count_distinct: 'unicos',
  }[agg] || 'total';
  const slug = `${pct}${prefix}_${field}`
    .normalize('NFD').replace(/[̀-ͯ]/g, '')
    .toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '');
  return (/^[a-z]/.test(slug) ? slug : `m_${slug}`).slice(0, 63);
}

// Cabecera por defecto de una columna: el mismo humanize() del backend (spec_validation), para
// que el placeholder del campo "Nombre a mostrar" sea el título que se ve si no se personaliza.
function defaultColumnName(name) {
  const text = String(name || '').replace(/_/g, ' ').trim();
  return text ? text[0].toUpperCase() + text.slice(1) : '';
}

// Estado del builder a partir de data_spec/view_spec: el MISMO spec que escribe la IA, así el
// panel siempre muestra lo que tiene el widget (no hay dos estados separados).
function builderFromSpec(spec, view) {
  if (!spec) return null;
  const labels = (view && view.labels) || {};
  return {
    // Siempre al menos un select visible por lista ('' = sin elegir).
    dimensions: (spec.dimensions || []).length ? [...spec.dimensions] : [''],
    pivots: pivotsOf(spec).length ? pivotsOf(spec) : [''],
    metrics: (spec.metrics || []).map(raw => {
      const m = normalizeMetric(raw, spec);
      const field = isCountAgg(m.agg) ? '' : m.field;
      return {
        field, agg: m.agg, as: m.as, show_as: m.show_as, label: labels[m.as] || '',
        _origAs: m.as, _origField: field, _origAgg: m.agg, _origShowAs: m.show_as,
      };
    }),
    // Las cabeceras de las filas/columnas no son del builder: se copian tal cual para no perderlas
    // (las puso la IA) al aplicar un cambio.
    labels: { ...labels },
    stacked: !!(view && view.stacked),
    sortBy: spec.sort ? spec.sort.by : '',
    sortDir: spec.sort ? spec.sort.dir : 'desc',
  };
}

// "Mostrar como" disponibles: en un KPI el % es contra la hoja sin los filtros del widget;
// sin pivote, % de la fila siempre sería 100 y % de la columna = % del total.
function showAsOptions(b) {
  const hasDim = builderDims(b).length > 0;
  const hasPivot = builderPivots(b).length > 0;
  let values = ['value', 'pct_row', 'pct_column', 'pct_total'];
  if (!hasDim) values = ['value', 'pct_total'];
  else if (!hasPivot) values = ['value', 'pct_column'];
  return values.map(value => ({
    value,
    label: !hasDim && value === 'pct_total' ? '% del total (sin filtros del widget)'
      : !hasPivot && value === 'pct_column' ? '% del total' : SHOW_AS_LABELS[value],
  }));
}

// Lleva `show_as` a una opción válida para la forma actual (ej. se quitó el pivote).
function effectiveShowAs(b, showAs) {
  if (!showAs || showAs === 'value') return 'value';
  if (!builderDims(b).length) return 'pct_total';
  if (!builderPivots(b).length) return 'pct_column';
  return showAs;
}

// Alias ("as") final de cada métrica del builder, en el mismo orden (null si aún no es válida:
// sin función o sin columna). Separado de builderToPayload para que la UI pueda mostrar el nombre
// por defecto de una métrica concreta, que depende también del orden (desempate de alias).
function metricAliases(b) {
  const used = new Set();
  return b.metrics.map(m => {
    if (!m.agg || (!isCountAgg(m.agg) && !m.field)) return null;
    const showAs = effectiveShowAs(b, m.show_as);
    // Conserva el alias existente si la métrica no cambió (así se conservan sus etiquetas).
    const unchanged = m.as && m.as === m._origAs && m.field === m._origField
      && m.agg === m._origAgg && showAs === m._origShowAs;
    const alias = unchanged ? m.as : metricAlias(m.agg, m.field, showAs);
    let candidate = alias;
    for (let i = 2; used.has(candidate); i++) candidate = `${alias}_${i}`.slice(0, 63);
    used.add(candidate);
    return { as: candidate, show_as: showAs, isCount: isCountAgg(m.agg) };
  });
}

// Body de PUT /api/widget/<id>/spec/. Las métricas count van sin campo: el backend usa la
// dimensión (cuenta filas del grupo). `labels` son las cabeceras de columna: viven en view_spec
// (no en la métrica, que el schema valida con additionalProperties: false).
function builderToPayload(b) {
  const dimensions = builderDims(b);
  const pivots = builderPivots(b);
  const aliases = metricAliases(b);
  // Cabeceras de las filas/columnas: se conservan las que ya tenía el widget y siguen en uso.
  const kept = new Set([...dimensions, ...pivots]);
  const labels = {};
  for (const [name, label] of Object.entries(b.labels || {})) {
    if (kept.has(name) && (label || '').trim()) labels[name] = label.trim();
  }
  const metrics = [];
  b.metrics.forEach((m, i) => {
    const alias = aliases[i];
    if (!alias) return;
    const metric = { field: alias.isCount ? '' : m.field, agg: m.agg, as: alias.as };
    if (alias.show_as !== 'value') metric.show_as = alias.show_as;
    metrics.push(metric);
    // "Nombre a mostrar" vacío = la cabecera por defecto (la del alias).
    const label = (m.label || '').trim();
    if (label) labels[alias.as] = label;
  });
  const sortTargets = new Set([...dimensions, ...metrics.map(m => m.as)]);
  return {
    dimensions,
    // Una columna como texto (compatible con los gráficos); varias, como lista.
    pivot: pivots.length > 1 ? pivots : (pivots[0] || null),
    metrics,
    labels,
    stacked: !!(b.stacked && pivots.length),
    sort: b.sortBy && sortTargets.has(b.sortBy) ? { by: b.sortBy, dir: b.sortDir || 'desc' } : null,
  };
}

// Incluye `current` aunque no esté en la lista (ej. la IA agrupó por una columna numérica de
// muchos valores), para que el select no pierda el valor que tiene el widget.
function withCurrent(options, current) {
  return current && !options.includes(current) ? [current, ...options] : options;
}

document.addEventListener('alpine:init', () => {
  Alpine.store('dashboard', {
    widgets: [],
    editingId: null,
    editingType: null,
    dashboardId: window.DASHBOARD_ID,
    drawerDraft: {},
    drawerGenerating: false,
    drawerGenerateError: '',
    drawerSaving: false,
    drawerSaveError: '',
    drawerHelpOpen: false,
    // Pestaña activa del panel: 'data' (filas, columnas, métricas) o 'style' (personalizar).
    drawerTab: 'data',
    // Banner de advertencia del builder: mensaje de rechazo del backend (no se aplica nada).
    drawerSpecError: '',
    drawerApplying: false,
    // Copia de data_spec/view_spec del widget en edición, para el visor JSON.
    drawerSpecs: { data_spec: null, view_spec: null },
    schema: { all_fields: [], numeric_fields: [], dimension_fields: [] },
    aggOptions: AGG_OPTIONS,
    isCountAgg,
    _nextId: -1,

    schemaError: '',

    async loadSchema() {
      try {
        const r = await fetch(apiUrl(`/api/dashboard/${this.dashboardId}/schema/`));
        const data = await r.json().catch(() => null);
        if (r.ok && data) {
          this.schema = data;
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

    // Carga widgets + datos ya calculados en un solo request. Retorna {id: entry}.
    async loadBoard() {
      const { r, data } = await fetchJsonSafe(this._renderUrl(), {}, RENDER_FETCH_TIMEOUT_MS);
      if (!r.ok || !data) throw new Error((data && data.error) || 'No se pudo cargar el tablero');
      this.widgets = data.widgets.map(w => BaseWidget.fromServer(w));
      this.widgets.sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
      return Object.fromEntries(data.widgets.map(w => [w.id, w]));
    },

    // Recalcula todos los widgets (sin IA): refresco periódico, filtros, reintentos.
    async refreshData() {
      const saved = this.widgets.filter(w => w.id > 0 && w.hasSpec);
      saved.forEach(w => w.setLoading(true));
      try {
        const { r, data } = await fetchJsonSafe(this._renderUrl(), {}, RENDER_FETCH_TIMEOUT_MS);
        if (!r.ok || !data) {
          const message = (data && data.error) || `Error ${r.status} al cargar los datos`;
          saved.forEach(w => { w.setLoading(false); w.renderError(message, { retryable: true }); });
          return;
        }
        const byId = Object.fromEntries(data.widgets.map(w => [w.id, w]));
        saved.forEach(w => w.applyRender(byId[w.id]));
      } catch (e) {
        saved.forEach(w => { w.setLoading(false); w.renderError('No se pudo conectar con el servidor', { retryable: true }); });
      }
    },

    addWidget(type) {
      const widget = WidgetRegistry.create(type, {
        id: this._nextId--,
        order: this.widgets.length,
        _dirty: true,
      });
      this.widgets.push(widget);
      return widget;
    },

    // Guarda solo la presentación (título, posición, preferencias visuales). Los widgets
    // nuevos no existen en el backend hasta que se generan con IA.
    async _saveWidget(w) {
      if (w.id < 0) return;
      await fetch(apiUrl(`/api/widget/${w.id}/`), {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(w.toPayload()),
      });
      w._dirty = false;
    },

    async removeWidget(id) {
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
        if (w && w.order !== i) {
          w.order = i;
          w._dirty = true;
        }
      });
      this.widgets.sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
    },

    get editingWidget() {
      return this.widgets.find(w => w.id === this.editingId) || null;
    },

    get drawerFields() {
      // No debe colapsar a []: eso destruiría/recrearía los <select> del drawer
      // (y sus <option>) en cada apertura.
      const WidgetClass = this.editingType ? WidgetRegistry.get(this.editingType) : BaseWidget;
      return WidgetClass.drawerFields;
    },

    get drawerWidgetClass() {
      return this.editingType ? WidgetRegistry.get(this.editingType) : BaseWidget;
    },

    get drawerHelp() {
      return this.drawerWidgetClass.help;
    },

    get builder() {
      return this.drawerDraft.builder || null;
    },

    // Columnas agrupables para la fila `i`, sin las ya usadas en otras filas o en columnas
    // (el backend también lo valida).
    dimensionOptionsAt(i) {
      const b = this.builder;
      if (!b) return [];
      const current = b.dimensions[i];
      const used = new Set([...b.dimensions, ...b.pivots]);
      return withCurrent(this.schema.dimension_fields || [], current).filter(f => f === current || !used.has(f));
    },

    pivotOptionsAt(i) {
      const b = this.builder;
      if (!b) return [];
      const current = b.pivots[i];
      const used = new Set([...b.dimensions, ...b.pivots]);
      return withCurrent(this.schema.dimension_fields || [], current).filter(f => f === current || !used.has(f));
    },

    get dimensionLabel() {
      return this.drawerWidgetClass.dimensionLabel;
    },

    get maxDimensions() {
      return this.drawerWidgetClass.maxDimensions;
    },

    get maxPivots() {
      return this.drawerWidgetClass.maxPivots;
    },

    // Se agrega un nivel solo cuando el anterior ya tiene columna elegida.
    get canAddDimension() {
      const b = this.builder;
      return !!b && b.dimensions.length < this.maxDimensions && b.dimensions.every(Boolean);
    },

    get canAddPivot() {
      const b = this.builder;
      return !!b && b.pivots.length < this.maxPivots && b.pivots.every(Boolean);
    },

    // Los totales de cada nivel (drawerDraft.rowTotals/columnTotals) siguen a su fila o
    // columna al agregar o quitar niveles, como en Sheets.
    addDimension() {
      if (!this.canAddDimension) return;
      this.builder.dimensions.push('');
      this.drawerDraft.rowTotals?.push(true);
    },

    removeDimension(i) {
      const b = this.builder;
      if (!b || b.dimensions.length <= 1) return;
      b.dimensions.splice(i, 1);
      this.drawerDraft.rowTotals?.splice(i, 1);
    },

    addPivot() {
      if (!this.canAddPivot) return;
      this.builder.pivots.push('');
      this.drawerDraft.columnTotals?.push(true);
    },

    removePivot(i) {
      const b = this.builder;
      if (!b) return;
      if (b.pivots.length > 1) {
        b.pivots.splice(i, 1);
        this.drawerDraft.columnTotals?.splice(i, 1);
      } else {
        b.pivots[0] = '';
        if (this.drawerDraft.columnTotals) this.drawerDraft.columnTotals[0] = true;
      }
    },

    get pivotLabel() {
      return this.drawerWidgetClass.pivotLabel;
    },

    get maxMetrics() {
      return this.drawerWidgetClass.maxMetrics;
    },

    // El campo "Nombre a mostrar" solo donde la métrica se ve con nombre: hoy, la tabla.
    get drawerSupportsLabels() {
      return !!this.drawerWidgetClass.supportsLabels;
    },

    // Cabecera por defecto de la métrica `i`: lo que se verá si no se escribe un nombre.
    metricLabelPlaceholder(i) {
      const b = this.builder;
      if (!b) return '';
      const alias = metricAliases(b)[i];
      return alias ? defaultColumnName(alias.as) : '';
    },

    get showStacked() {
      return this.drawerWidgetClass.supportsStacked && !!(this.builder && builderPivots(this.builder).length);
    },

    // Campos según la función: contar únicos acepta cualquier columna; el resto de las
    // funciones que resumen una columna, solo numéricas (count no usa campo).
    fieldOptionsFor(agg, current) {
      const fields = agg === 'count_distinct' ? this.schema.all_fields : this.schema.numeric_fields;
      return withCurrent(fields || [], current);
    },

    get showAsOptions() {
      return this.builder ? showAsOptions(this.builder) : [];
    },

    showAsValue(m) {
      return this.builder ? effectiveShowAs(this.builder, m.show_as) : 'value';
    },

    get sortOptions() {
      const b = this.builder;
      if (!b) return [];
      const payload = builderToPayload(b);
      const opts = [];
      for (const d of payload.dimensions) opts.push({ value: d, label: d });
      for (const m of payload.metrics) {
        const agg = AGG_OPTIONS.find(a => a.value === m.agg)?.label || m.agg;
        const base = m.agg === 'count' ? 'Conteo de filas' : `${agg} de ${m.field}`;
        const showAs = m.show_as ? ` (${SHOW_AS_LABELS[m.show_as]})` : '';
        opts.push({ value: m.as, label: base + showAs });
      }
      return opts;
    },

    onDimensionChange() {
      // Una columna elegida como fila deja de ser columna de pivote.
      const b = this.builder;
      if (!b) return;
      b.pivots = b.pivots.map(p => (b.dimensions.includes(p) ? '' : p));
    },

    openDrawer(id) {
      const w = this.widgets.find(w => w.id === id);
      if (!w) return;
      this.editingId = id;
      this.editingType = w.chart_type;
      // Un widget nuevo empieza por sus datos.
      if (w.id < 0) this.drawerTab = 'data';
      const draft = {};
      for (const field of this.drawerFields) {
        if (field.key === 'builder' || field.key === 'prompt') continue;
        draft[field.key] = w[field.key];
      }
      draft.prompt = '';
      draft.builder = this._builderDraft(w) || this._defaultBuilder();
      // Totales por nivel: van junto a cada fila/columna del builder, pero son presentación
      // (se guardan con "Guardar" y no vuelven a pedir los datos).
      if (this.drawerWidgetClass.supportsTotals) {
        const levels = (list, n) => Array.from({ length: n }, (_, i) => (list || [])[i] !== false);
        draft.rowTotals = levels(w.rowTotals, draft.builder.dimensions.length);
        draft.columnTotals = levels(w.columnTotals, draft.builder.pivots.length);
        // "Repetir etiquetas de fila" va, como en Sheets, bajo la primera fila.
        draft.repeatRowLabels = !!w.repeatRowLabels;
      }
      this.drawerDraft = draft;
      // Widget nuevo abierto antes de que llegaran las columnas: completar los valores por
      // defecto cuando lleguen.
      if (w.id < 0 && !(this.schema.all_fields || []).length) {
        this.loadSchema().then(() => {
          if (this.editingId === id) this.drawerDraft.builder = this._defaultBuilder();
        });
      }
      this.drawerGenerateError = '';
      this.drawerSaveError = '';
      this.drawerSpecError = '';
      this._syncDrawerSpecs(w);
    },

    _builderDraft(w) {
      return builderFromSpec(w.data_spec, w.view_spec);
    },

    // Punto de partida del builder para un widget nuevo: primera columna agrupable y una
    // métrica (suma de la primera columna numérica, o conteo si la hoja no tiene números).
    _defaultBuilder() {
      const dims = this.schema.dimension_fields || [];
      const numeric = this.schema.numeric_fields || [];
      return {
        dimensions: [this.drawerWidgetClass.supportsDimension ? (dims[0] || '') : ''],
        pivots: [''],
        metrics: [numeric.length
          ? { field: numeric[0], agg: 'sum', as: '', show_as: 'value', label: '' }
          : { field: '', agg: 'count', as: '', show_as: 'value', label: '' }],
        labels: {},
        stacked: false,
        sortBy: '',
        sortDir: 'desc',
      };
    },

    _syncDrawerSpecs(w) {
      this.drawerSpecs = {
        data_spec: w && w.data_spec ? JSON.parse(JSON.stringify(w.data_spec)) : null,
        view_spec: w && w.view_spec ? JSON.parse(JSON.stringify(w.view_spec)) : null,
      };
    },

    closeDrawer() {
      this.editingId = null;
      this.editingType = null;
      this.drawerDraft = {};
      this.drawerGenerateError = '';
      this.drawerSaveError = '';
      this.drawerSpecError = '';
      this.drawerHelpOpen = false;
      this.drawerTab = 'data';
    },

    addBuilderMetric() {
      const b = this.builder;
      if (!b || b.metrics.length >= this.maxMetrics) return;
      const numeric = this.schema.numeric_fields || [];
      b.metrics.push(numeric.length
        ? { field: numeric[0], agg: 'sum', as: '', show_as: 'value', label: '' }
        : { field: '', agg: 'count', as: '', show_as: 'value', label: '' });
    },

    removeBuilderMetric(index) {
      const b = this.builder;
      if (!b || b.metrics.length <= 1) return;
      b.metrics.splice(index, 1);
    },

    // "Aplicar al widget seleccionado": escribe data_spec vía update_widget_spec. NUNCA llama a
    // la IA. Si el backend rechaza la combinación, muestra su mensaje en el banner y no toca el
    // widget. Retorna true si se aplicó.
    async applyBuilder() {
      const w = this.editingWidget;
      const b = this.builder;
      if (!w || !b) return false;
      const payload = builderToPayload(b);
      if (!payload.metrics.length) {
        this.drawerSpecError = 'Agrega al menos una métrica con su columna.';
        return false;
      }
      this.drawerApplying = true;
      this.drawerSpecError = '';
      try {
        const isNew = w.id < 0;
        // Widget nuevo: se crea desde el builder. Existente: se edita su spec. Ninguno usa IA.
        const { r, data } = await fetchJsonSafe(
          isNew
            ? apiUrl(`/api/dashboard/${this.dashboardId}/widgets/`)
            : apiUrl(`/api/widget/${w.id}/spec/`),
          {
            method: isNew ? 'POST' : 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              ...payload,
              title: this.drawerDraft.title,
              ...(isNew ? { type: this.editingType, position: w.getPosition(), display: w.getProperties() } : {}),
            }),
          },
          RENDER_FETCH_TIMEOUT_MS,
        );
        if (!r.ok || !data) {
          this.drawerSpecError = (data && data.error) || 'No se pudo aplicar el cambio.';
          return false;
        }
        if (isNew) this._swapWidgetId(w, data.id);
        w.applyServerState(data);
        w.updateChrome();
        w.applyRender(data);
        this.drawerDraft.builder = this._builderDraft(w);
        this._syncDrawerSpecs(w);
        return true;
      } catch (e) {
        this.drawerSpecError = 'No se pudo conectar con el servidor.';
        return false;
      } finally {
        this.drawerApplying = false;
      }
    },

    get builderDirty() {
      const w = this.editingWidget;
      if (!w || !this.builder) return false;
      if (w.id < 0) return true;
      return JSON.stringify(builderToPayload(this.builder))
        !== JSON.stringify(builderToPayload(this._builderDraft(w)));
    },

    _swapWidgetId(w, newId) {
      const oldId = w.id;
      w.id = newId;
      if (w.el) {
        w.el.dataset.widgetId = newId;
        const chartContainer = w.el.querySelector(`#chart-${oldId}`);
        if (chartContainer) chartContainer.id = `chart-${newId}`;
      }
      if (this.editingId === oldId) this.editingId = newId;
    },

    // Genera (o regenera) el spec del widget con IA y lo guarda en el backend.
    async generateWidgetSpec() {
      const w = this.editingWidget;
      if (!w || !this.drawerDraft.prompt) return;
      this.drawerGenerating = true;
      this.drawerGenerateError = '';
      try {
        const { r, data } = await fetchJsonSafe(apiUrl(`/api/dashboard/${this.dashboardId}/widgets/generate/`), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            prompt: this.drawerDraft.prompt,
            widget_type: this.editingType,
            widget_id: w.id > 0 ? w.id : null,
            position: w.getPosition(),
          }),
        });
        if (!r.ok || !data) {
          throw new Error((data && data.error) || 'El servidor no respondió correctamente (puede que la IA haya tardado demasiado). Intenta de nuevo.');
        }
        if (w.id < 0) this._swapWidgetId(w, data.id);
        w.applyServerState(data);
        w.updateChrome();
        w.applyRender(data);
        // Mostrar en el drawer lo que generó la IA, para revisarlo o ajustarlo en el builder.
        this.drawerDraft.title = w.title;
        this.drawerDraft.builder = this._builderDraft(w);
        this.drawerDraft.prompt = '';
        this.drawerSpecError = '';
        this._syncDrawerSpecs(w);
      } catch (e) {
        this.drawerGenerateError = e.name === 'AbortError'
          ? 'La IA tardó demasiado en responder. Intenta de nuevo.'
          : e.message;
      } finally {
        this.drawerGenerating = false;
      }
    },

    async saveDrawer() {
      const w = this.editingWidget;
      if (!w) return;
      this.drawerSaving = true;
      this.drawerSaveError = '';
      try {
        const { prompt, builder, ...presentation } = this.drawerDraft;
        // Cambios del builder sin aplicar: se aplican con el mismo camino que el botón.
        if (builder && this.builderDirty && !(await this.applyBuilder())) return;

        Object.assign(w, presentation);
        await this._saveWidget(w);
        w.updateChrome();
        // Opciones de presentación que cambian el contenido (ej. totales de la tabla): se
        // redibuja con los datos ya calculados, sin volver a pedirlos.
        if (w._lastEntry) w.applyRender(w._lastEntry);
        this.closeDrawer();
      } catch (e) {
        this.drawerSaveError = e.message;
      } finally {
        this.drawerSaving = false;
      }
    },
  });
});
