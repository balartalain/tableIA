// «Generar con IA»: el usuario describe el tablero, la IA propone la lista de widgets (tipo,
// título, descripción y el pedido que configura cada uno: POST board-plan/) y, al aceptar, cada
// widget marcado se configura con el asistente del panel (table-assistant, con su pedido y su
// tipo fijo), se crean los campos calculados que proponga y se crea en el tablero. Varios a la
// vez (BUILD_CONCURRENCY): cada widget aparece en su lugar del lienzo apenas está listo. Lo
// abre el evento `ai-board:open`.

// Widgets que se piden a la IA al mismo tiempo al crear el tablero.
const BUILD_CONCURRENCY = 3;

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
    // Fila del lienzo de cada fila de la tanda que se está creando: {índice: row_id}.
    _rowIds: {},
    // Campos calculados de la tanda: la cola que los crea de a uno y los nombres ya creados.
    _calculated: null,
    _calculatedNames: new Set(),

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

    _isHeader(item) {
      const WidgetClass = WidgetRegistry.has(item.widget_type) ? WidgetRegistry.get(item.widget_type) : null;
      return (WidgetClass && WidgetClass.placement === 'header') || item.widget_type === 'filter';
    },

    // Orden del tablero: filtros, luego KPI, dona y ranking (los números clave arriba) y después
    // el resto, cada grupo en el orden que propuso la IA.
    _organizePlanItems(items) {
      const TOP_ORDER = ['kpi', 'donut', 'ranking'];
      const rank = i => {
        if (this._isHeader(i)) return 0;
        const top = TOP_ORDER.indexOf(i.widget_type);
        return top >= 0 ? 1 + top : 1 + TOP_ORDER.length;
      };
      return (items || [])
        .map((i, index) => ({ item: { ...i, include: true, status: '', error: '' }, rank: rank(i), index }))
        .sort((a, b) => a.rank - b.rank || a.index - b.index)
        .map(({ item }) => item);
    },

    // Ancho mínimo con el que un widget entra a una fila; lo que sobre de la fila se reparte.
    _baseWidth(type) {
      if (type === 'kpi') return 3;
      const WidgetClass = WidgetRegistry.has(type) ? WidgetRegistry.get(type) : null;
      const width = parseInt(((WidgetClass && WidgetClass.defaults.width) || '').match(/col-span-(\d+)/)?.[1] || '6', 10);
      return Math.min(12, Math.max(2, width));
    },

    // Reparte los widgets en filas de 12 columnas, en orden: cada fila se llena hasta que el
    // siguiente no cabe y el espacio que sobra se reparte parejo entre los de la fila. Deja en
    // cada item su `width` y su `_row` (índice de fila dentro de esta tanda).
    _layoutQueue(queue) {
      const rows = [];
      let current = null;
      queue.filter(i => !this._isHeader(i)).forEach(item => {
        const width = this._baseWidth(item.widget_type);
        if (!current || current.used + width > 12) {
          current = { used: 0, items: [] };
          rows.push(current);
        }
        current.items.push(item);
        current.used += width;
        item.width = width;
        item._row = rows.length - 1;
      });
      rows.forEach(row => {
        const spare = 12 - row.used;
        const each = Math.floor(spare / row.items.length);
        const extra = spare % row.items.length;
        row.items.forEach((item, index) => { item.width += each + (index < extra ? 1 : 0); });
      });
    },

    // La fila del lienzo de un item: las filas de la tanda van al final del tablero, en orden
    // (la primera aprovecha una fila vacía, como la del tablero recién creado).
    async _rowFor(item) {
      if (this._isHeader(item)) return null;
      if (this._rowIds[item._row]) return this._rowIds[item._row];
      const store = this.store;
      const taken = new Set(Object.values(this._rowIds));
      const last = store.rows.length ? store.rows[store.rows.length - 1].id : null;
      let rowId = last && !taken.has(last) && !store.getRowWidgets(last).length ? last : null;
      if (!rowId) {
        rowId = store.addRowBelow(last);
        await Alpine.nextTick();
      }
      this._rowIds[item._row] = rowId;
      return rowId;
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
      this._layoutQueue(queue);
      this._rowIds = {};
      this._calculated = Promise.resolve();
      this._calculatedNames = new Set();
      // Las filas y el orden quedan fijados antes de empezar: los widgets terminan en cualquier
      // orden y cada uno cae en su sitio.
      const firstOrder = Math.max(-1, ...this.store.widgets.map(w => w.position?.order ?? -1)) + 1;
      for (const [index, item] of queue.entries()) {
        item._order = firstOrder + index;
        item._rowId = await this._rowFor(item);
      }
      let next = 0;
      const worker = async () => {
        while (next < queue.length) {
          const item = queue[next++];
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
      };
      await Promise.all(Array.from({ length: Math.min(BUILD_CONCURRENCY, queue.length) }, worker));
      // Si alguno falló, los de su fila ocupan el hueco; una fila sin widgets no queda.
      const rowIds = Object.values(this._rowIds);
      await this.store.fillRows(rowIds);
      rowIds.forEach(id => this.store.removeRow(id));
      this.building = false;
      const done = queue.filter(i => i.status === 'done').length;
      if (!this.open && typeof window.showToast === 'function') {
        window.showToast(done === queue.length ? `Tablero listo: ${done} widgets creados.`
          : `Se crearon ${done} de ${queue.length} widgets.`);
      }
    },

    // Los campos calculados se crean de a uno: si dos widgets que se configuran a la vez
    // proponen el mismo campo, el segundo usa el que ya creó el primero.
    _addCalculated(fields) {
      const run = async () => {
        const fresh = fields.filter(f => !this._calculatedNames.has(f.name));
        if (!fresh.length) return;
        await this.store.addCalculatedFields(this.source, fresh);
        fresh.forEach(f => this._calculatedNames.add(f.name));
      };
      const result = this._calculated.then(run);
      this._calculated = result.catch(() => {});
      return result;
    },

    async _buildOne(item) {
      let fields = item.fields;
      let style = item.style || {};
      let calculatedFields = item.calculated_fields || [];
      let columnScales = item.column_scales || [];

      // Si el widget no trae fields ya resueltos (ej. filter), consulta al asistente con IA
      if (!fields) {
        const { r, data } = await fetchJsonSafe(apiUrl(`/api/dashboard/${this.store.dashboardId}/table-assistant/`), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            prompt: item.request, widget_type: item.widget_type, source: this.source, columns: item.columns || [],
          }),
        });
        if (!r.ok || !data) throw new Error((data && data.error) || `Error ${r.status}`);
        fields = data.fields;
        style = data.style || style;
        calculatedFields = data.calculated_fields || [];
        columnScales = data.column_scales || [];
      }

      if (calculatedFields.length) await this._addCalculated(calculatedFields);
      // Las escalas que corrige la IA (matriz de correlación) van a la fuente antes del widget.
      if (columnScales.length) await this.store.saveScales(this.source, this.store._scalesMap(columnScales));
      const result = await this.store.createWidgetFromProposal({
        type: item.widget_type,
        source: this.source,
        title: item.title,
        width: item.width,
        rowId: item._rowId,
        order: item._order,
        fields,
        style,
      });
      if (!result.ok) throw new Error(result.error);
    },
  };
}
