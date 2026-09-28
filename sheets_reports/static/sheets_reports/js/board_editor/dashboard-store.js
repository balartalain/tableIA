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

const AGG_OPTIONS = [
  { value: 'sum', label: 'Suma' },
  { value: 'avg', label: 'Promedio' },
  { value: 'count', label: 'Conteo' },
];

// Nombre de columna resultante ("as") para una métrica del builder: snake_case ASCII, como
// exige el schema (^[a-z][a-z0-9_]{0,62}$). count no depende del campo: siempre "cantidad".
function metricAlias(agg, field) {
  if (agg === 'count') return 'cantidad';
  const prefix = agg === 'avg' ? 'promedio' : 'total';
  const slug = `${prefix}_${field}`
    .normalize('NFD').replace(/[̀-ͯ]/g, '')
    .toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '');
  return (/^[a-z]/.test(slug) ? slug : `m_${slug}`).slice(0, 63);
}

// Estado del builder a partir de data_spec/view_spec: el MISMO spec que escribe la IA, así el
// panel siempre muestra lo que tiene el widget (no hay dos estados separados).
function builderFromSpec(spec, view) {
  if (!spec) return null;
  return {
    dimension: (spec.dimensions || [])[0] || '',
    pivot: spec.pivot || '',
    metrics: (spec.metrics || []).map(m => ({
      field: m.agg === 'count' ? '' : m.field,
      agg: m.agg,
      as: m.as,
      _origAs: m.as, _origField: m.agg === 'count' ? '' : m.field, _origAgg: m.agg,
    })),
    stacked: !!(view && view.stacked),
    sortBy: spec.sort ? spec.sort.by : '',
    sortDir: spec.sort ? spec.sort.dir : 'desc',
  };
}

// Body de PUT /api/widget/<id>/spec/. Las métricas count van sin campo: el backend usa la
// dimensión (cuenta filas del grupo).
function builderToPayload(b) {
  const metrics = [];
  const used = new Set();
  for (const m of b.metrics) {
    if (!m.agg || (m.agg !== 'count' && !m.field)) continue;
    // Conserva el alias existente si la métrica no cambió (así se conservan sus etiquetas).
    const alias = m.as && m.as === m._origAs && m.field === m._origField && m.agg === m._origAgg
      ? m.as
      : metricAlias(m.agg, m.field);
    let candidate = alias;
    for (let i = 2; used.has(candidate); i++) candidate = `${alias}_${i}`.slice(0, 63);
    used.add(candidate);
    metrics.push({ field: m.agg === 'count' ? '' : m.field, agg: m.agg, as: candidate });
  }
  const sortTargets = new Set([b.dimension, ...metrics.map(m => m.as)]);
  return {
    dimensions: b.dimension ? [b.dimension] : [],
    pivot: b.pivot || null,
    metrics,
    stacked: !!(b.stacked && b.pivot),
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
    // Banner de advertencia del builder: mensaje de rechazo del backend (no se aplica nada).
    drawerSpecError: '',
    drawerApplying: false,
    // Copia de data_spec/view_spec del widget en edición, para el visor JSON.
    drawerSpecs: { data_spec: null, view_spec: null },
    schema: { all_fields: [], numeric_fields: [], dimension_fields: [] },
    aggOptions: AGG_OPTIONS,
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

    get dimensionOptions() {
      return withCurrent(this.schema.dimension_fields || [], this.builder && this.builder.dimension);
    },

    // Mismas columnas que "Agrupar por", sin la dimensión elegida (el backend también lo valida).
    get pivotOptions() {
      const b = this.builder;
      if (!b) return [];
      return withCurrent(this.schema.dimension_fields || [], b.pivot).filter(f => f !== b.dimension);
    },

    get pivotLabel() {
      return this.drawerWidgetClass.pivotLabel;
    },

    get maxMetrics() {
      return this.drawerWidgetClass.maxMetrics;
    },

    get showStacked() {
      return this.drawerWidgetClass.supportsStacked && !!(this.builder && this.builder.pivot);
    },

    // Campos para suma/promedio: solo numéricos (count no usa campo).
    numericFieldOptions(current) {
      return withCurrent(this.schema.numeric_fields || [], current);
    },

    get sortOptions() {
      const b = this.builder;
      if (!b) return [];
      const payload = builderToPayload(b);
      const opts = [];
      if (b.dimension) opts.push({ value: b.dimension, label: b.dimension });
      for (const m of payload.metrics) {
        const agg = AGG_OPTIONS.find(a => a.value === m.agg)?.label || m.agg;
        opts.push({ value: m.as, label: m.agg === 'count' ? 'Conteo de filas' : `${agg} de ${m.field}` });
      }
      return opts;
    },

    onDimensionChange() {
      const b = this.builder;
      if (b && b.pivot && b.pivot === b.dimension) b.pivot = '';
    },

    openDrawer(id) {
      const w = this.widgets.find(w => w.id === id);
      if (!w) return;
      this.editingId = id;
      this.editingType = w.chart_type;
      const draft = {};
      for (const field of this.drawerFields) {
        if (field.key === 'builder' || field.key === 'prompt') continue;
        draft[field.key] = w[field.key];
      }
      draft.prompt = '';
      draft.builder = this._builderDraft(w) || this._defaultBuilder();
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
        dimension: this.drawerWidgetClass.supportsDimension ? (dims[0] || '') : '',
        pivot: '',
        metrics: [numeric.length
          ? { field: numeric[0], agg: 'sum', as: '' }
          : { field: '', agg: 'count', as: '' }],
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
    },

    addBuilderMetric() {
      const b = this.builder;
      if (!b || b.metrics.length >= this.maxMetrics) return;
      const numeric = this.schema.numeric_fields || [];
      b.metrics.push(numeric.length
        ? { field: numeric[0], agg: 'sum', as: '' }
        : { field: '', agg: 'count', as: '' });
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
        this.closeDrawer();
      } catch (e) {
        this.drawerSaveError = e.message;
      } finally {
        this.drawerSaving = false;
      }
    },
  });
});
