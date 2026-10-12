// «Generar con IA»: el usuario describe el tablero, la IA propone la lista de widgets (tipo,
// título, descripción y el pedido que configura cada uno: POST board-plan/) y, al aceptar, cada
// widget marcado se configura con el asistente del panel (table-assistant, con su pedido y su
// tipo fijo), se crean los campos calculados que proponga y se crea en el tablero. De a uno y
// en orden: un campo calculado creado para un widget ya lo ve el siguiente, y cada widget
// aparece en el lienzo apenas está listo. Lo abre el evento `ai-board:open`.

function aiBoard() {
  return {
    open: false,
    // prompt → plan (la lista propuesta) → build (creando los widgets).
    step: 'prompt',
    prompt: '',
    source: null,
    // [{widget_type, title, description, request, width, include, status, error}]
    // status: '' | 'pending' | 'working' | 'done' | 'failed'.
    items: [],
    note: '',
    error: '',
    loading: false,
    building: false,

    get store() { return Alpine.store('dashboard'); },
    get sources() { return this.store.sources || []; },
    get selected() { return this.items.filter(i => i.include); },
    get failed() { return this.items.filter(i => i.status === 'failed'); },
    get finished() { return this.step === 'build' && !this.building; },
    get doneCount() { return this.items.filter(i => i.status === 'done').length; },

    show() {
      if (this.building) { this.open = true; return; }
      Object.assign(this, { open: true, step: 'prompt', error: '', note: '', items: [] });
      if (!this.sources.some(s => s.id === this.source)) this.source = this.sources.length ? this.sources[0].id : null;
      this.$nextTick(() => this.$refs.prompt && this.$refs.prompt.focus());
    },

    // Mientras crea, la ventana se puede cerrar: sigue creando y se reabre con el progreso.
    close() {
      this.open = false;
    },

    icon(type) {
      return (WidgetRegistry.has(type) && WidgetRegistry.get(type).palette.icon) || 'ti-layout-grid';
    },

    typeLabel(type) {
      return (this.store.widgetManifest[type] || {}).label || type;
    },

    _organizePlanItems(items) {
      const TOP_TYPES = new Set(['kpi', 'donut', 'ranking']);
      const mapped = (items || []).map(i => {
        const WidgetClass = WidgetRegistry.get(i.widget_type);
        const isHeader = (WidgetClass && WidgetClass.placement === 'header') || i.widget_type === 'filter';
        const isTop = TOP_TYPES.has(i.widget_type);

        let category = 3;
        let width = 6;

        if (isHeader) {
          category = 1;
          width = 12;
        } else if (isTop) {
          category = 2;
          width = 4;
        } else {
          category = 3;
          width = 6;
        }

        return {
          ...i,
          width,
          include: true,
          status: '',
          error: '',
          _category: category,
        };
      });

      mapped.sort((a, b) => a._category - b._category);
      return mapped.map(({ _category, ...item }) => item);
    },

    async propose() {
      if (!this.prompt.trim() || this.loading) return;
      this.loading = true;
      this.error = '';
      try {
        const { r, data } = await fetchJsonSafe(apiUrl(`/api/dashboard/${this.store.dashboardId}/board-plan/`), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ prompt: this.prompt, source: this.source }),
        });
        if (!r.ok || !data) throw new Error((data && data.error) || `Error ${r.status}`);
        this.items = this._organizePlanItems(data.items || []);
        this.note = data.note || '';
        this.step = 'plan';
      } catch (e) {
        this.error = e.name === 'AbortError' ? 'La IA tardó demasiado. Intenta de nuevo.'
          : e.message === 'Failed to fetch' ? 'No se pudo conectar con el servidor.' : e.message;
      } finally {
        this.loading = false;
      }
    },

    back() {
      this.step = 'prompt';
      this.error = '';
    },

    async build({ onlyFailed = false } = {}) {
      const queue = onlyFailed ? this.failed : this.selected;
      if (!queue.length || this.building) return;
      this.items = this.items.filter(i => i.include);
      queue.forEach(i => { i.status = 'pending'; i.error = ''; });
      this.step = 'build';
      this.building = true;
      for (const item of queue) {
        item.status = 'working';
        try {
          await this._buildOne(item);
          item.status = 'done';
        } catch (e) {
          item.status = 'failed';
          item.error = e.name === 'AbortError' ? 'La IA tardó demasiado.'
            : e.message === 'Failed to fetch' ? 'No se pudo conectar con el servidor.' : e.message;
        }
      }
      this.building = false;
      const done = queue.filter(i => i.status === 'done').length;
      if (!this.open && typeof window.showToast === 'function') {
        window.showToast(done === queue.length ? `Tablero listo: ${done} widgets creados.`
          : `Se crearon ${done} de ${queue.length} widgets.`);
      }
    },

    async _buildOne(item) {
      let fields = item.fields;
      let style = item.style || {};
      let calculatedFields = item.calculated_fields || [];

      // Si el widget no trae fields ya resueltos (ej. filter), consulta al asistente con IA
      if (!fields) {
        const { r, data } = await fetchJsonSafe(apiUrl(`/api/dashboard/${this.store.dashboardId}/table-assistant/`), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ prompt: item.request, widget_type: item.widget_type, source: this.source }),
        });
        if (!r.ok || !data) throw new Error((data && data.error) || `Error ${r.status}`);
        fields = data.fields;
        style = data.style || style;
        calculatedFields = data.calculated_fields || [];
      }

      if (calculatedFields.length) {
        await this.store.addCalculatedFields(this.source, calculatedFields);
      }
      const result = await this.store.createWidgetFromProposal({
        type: item.widget_type,
        source: this.source,
        title: item.title,
        fields,
        style,
      });
      if (!result.ok) throw new Error(result.error);
    },
  };
}
