(function () {
  // Caja de filtros (FilterWidget del backend): una barra fija arriba del tablero, a todo el
  // ancho, con un control por columna en el orden del panel. Lo que se elige no se guarda en
  // el widget: va a la selección del tablero (filters.js) y recalcula todos los widgets.

  // Textos de Virtual Select en español.
  const MULTI_SELECT_TEXTS = {
    placeholder: 'Todos',
    searchPlaceholderText: 'Buscar…',
    noOptionsText: 'Sin opciones',
    noSearchResultsText: 'Sin resultados',
    selectAllText: 'Seleccionar todo',
    optionSelectedText: 'seleccionado',
    optionsSelectedText: 'seleccionados',
    allOptionsSelectedText: 'Todos',
    clearButtonText: 'Limpiar',
  };

  class FilterWidget extends BaseWidget {
    static type = 'filter';
    static palette = {
      icon: 'ti-filter',
      category: 'controls',
      label: 'Filtros',
      description: 'Caja de filtros fija arriba del tablero',
    };
    static defaults = { title: 'Filtros', width: 'md:col-span-12', height: 300 };
    static placement = 'header';
    static singleton = true;
    // Builder: solo la lista de filtros (columna + tipo), reordenable.
    static supportsDimension = false;
    static supportsPivot = false;
    static supportsMetrics = false;
    static supportsConditions = false;
    static supportsSort = false;
    static usesColumns = true;
    static maxColumns = 10;
    static defaultColumns = 1;
    static defaultColumnsFrom = 'dimension_fields';
    static columnsLabel = 'Filtros';
    static columnsHint = 'un control por columna; arrastra para cambiar el orden';
    static addColumnLabel = 'Agregar filtro';
    static allowAllColumns = false;
    // Cada filtro lleva su etiqueta (por defecto, el nombre de la columna).
    static supportsColumnLabels = true;
    // Tipos de filtro (FILTER_CONTROLS del backend). El rango de fecha aún no está disponible.
    static columnControls = [
      { value: 'multi_select', label: 'Selector múltiple' },
      { value: 'date_range', label: 'Rango de fecha (próximamente)', disabled: true },
    ];

    static getGhostSpan() {
      return 12;
    }

    // Siempre a todo el ancho y arriba: sin ancho ni columna de inicio.
    static get drawerFields() {
      return [this.FIELD_TITLE, this.FIELD_BUILDER];
    }

    constructor(raw) {
      super(raw);
      this._selects = [];
      this._signature = null;
    }

    _buildBar(editable) {
      const el = document.createElement('div');
      el.className = `relative ${editable ? 'group' : ''} bg-white border border-line rounded-xl shadow-sm px-4 py-3`;
      el.dataset.widgetId = this.id;
      el.dataset.type = this.chart_type;
      // El título solo en el editor (identifica la caja); en la vista compartida, solo los filtros.
      el.innerHTML = `
        ${editable ? `<div class="flex items-center gap-1.5 mb-2 select-none">
          <i class="ti ti-filter text-sm text-ink/40" aria-hidden="true"></i>
          <span class="title-display text-[10px] font-bold uppercase tracking-wider text-ink/40">${BaseWidget.escapeHTML(this.title)}</span>
        </div>` : ''}
        <div id="chart-${this.id}" class="flex flex-wrap items-end gap-3 min-h-[2.25rem]"></div>
        ${this.loaderOverlayHTML()}
        ${editable ? BaseWidget.actionButtonsHTML() : ''}`;
      return el;
    }

    buildElement() {
      return this._buildBar(true);
    }

    buildReadOnlyElement() {
      return this._buildBar(false);
    }

    // Solo el título: el ancho y el alto no aplican.
    updateChrome() {
      if (!this.el) return;
      const titleEl = this.el.querySelector('.title-display');
      if (titleEl) titleEl.textContent = this.title;
    }

    renderPlaceholder() {
      const container = this.getContentContainer();
      if (!container) return;
      this._destroySelects();
      container.innerHTML = `<span class="text-xs text-ink/50">Agrega filtros desde el panel de edición.</span>`;
    }

    renderContent(container, data) {
      const filters = (data && data.filters) || [];
      // El refresco periódico devuelve las mismas opciones: no se recrean los controles (se
      // cerraría un desplegable abierto).
      const signature = JSON.stringify(filters);
      if (signature === this._signature && this._selects.length) return;
      this._destroySelects();
      this._signature = signature;
      container.innerHTML = '';
      if (!filters.length) {
        this.renderPlaceholder();
        return;
      }
      filters.forEach(filter => {
        const wrap = document.createElement('div');
        wrap.className = 'w-56 max-w-full';
        wrap.innerHTML = `
          <label class="block text-[11px] font-semibold text-ink/60 mb-1">${BaseWidget.escapeHTML(filter.label)}</label>
          <div class="filter-control"></div>
          ${filter.truncated ? `<p class="mt-0.5 text-[10px] text-ink/40">Solo las primeras ${filter.options.length.toLocaleString()} opciones</p>` : ''}`;
        container.appendChild(wrap);
        const ele = wrap.querySelector('.filter-control');
        if (filter.type === 'multi_select') this._initMultiSelect(ele, filter);
      });
    }

    _initMultiSelect(ele, filter) {
      const store = Alpine.store('dashboard');
      // Virtual Select trabaja con textos: se guarda el valor original (ej. 2026 numérico)
      // para mandarlo tal cual en el filtro.
      const byKey = new Map(filter.options.map(v => [String(v), v]));
      VirtualSelect.init({
        ele,
        options: filter.options.map(v => ({ label: String(v), value: String(v) })),
        multiple: true,
        search: true,
        selectedValue: ((store.boardFilters || {})[filter.field] || []).map(String),
        silentInitialValueSet: true,
        maxWidth: '100%',
        // El desplegable se dibuja en <body>: el lienzo tiene scroll y lo recortaría.
        dropboxWrapper: 'body',
        zIndex: 60,
        ...MULTI_SELECT_TEXTS,
      });
      ele.addEventListener('change', () => {
        const values = (ele.value || []).map(k => (byKey.has(k) ? byKey.get(k) : k));
        store.setBoardFilter(filter.field, values);
      });
      this._selects.push(ele);
    }

    _destroySelects() {
      this._selects.forEach(ele => { if (typeof ele.destroy === 'function') ele.destroy(); });
      this._selects = [];
      this._signature = null;
    }

    destroy() {
      this._destroySelects();
      super.destroy();
    }
  }

  WidgetRegistry.register(FilterWidget);
})();
