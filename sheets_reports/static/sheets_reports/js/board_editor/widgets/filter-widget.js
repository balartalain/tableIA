(function () {
  const MULTI_SELECT_TEXTS = {
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
    static getGhostSpan() {
      return 12;
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

    updateChrome() {
      if (!this.el) return;
      const titleEl = this.el.querySelector('.title-display');
      if (titleEl) titleEl.textContent = this.title;
    }

    getProperties() {
      return { ...super.getProperties() };
    }

    renderPlaceholder() {
      const container = this.getContentContainer();
      if (!container) return;
      this._destroySelects();
      container.innerHTML = `<span class="text-xs text-ink/50">Agrega filtros desde el panel de edición.</span>`;
    }

    draw(data, style, title) {
      this.style = { ...this.style, ...style };
      if (title) this.title = title;

      const filters = (data && data.filters) || [];
      const signature = JSON.stringify(filters);
      if (signature === this._signature && this._selects.length) return;
      this._destroySelects();
      this._signature = signature;

      const container = this.getContentContainer();
      container.innerHTML = '';
      if (!filters.length) {
        this.renderPlaceholder();
        return;
      }
      filters.forEach(filter => {
        const wrap = document.createElement('div');
        wrap.className = 'w-56 max-w-full';
        wrap.title = filter.label;
        wrap.innerHTML = `
          <div class="filter-control"></div>
          ${filter.truncated ? `<p class="mt-0.5 text-[10px] text-ink/40">Solo las primeras ${filter.options.length.toLocaleString()} opciones</p>` : ''}`;
        container.appendChild(wrap);
        const ele = wrap.querySelector('.filter-control');
        if (filter.type === 'multi_select') this._initMultiSelect(ele, filter);
      });
    }

    _initMultiSelect(ele, filter) {
      const store = Alpine.store('dashboard');
      const byKey = new Map(filter.options.map(v => [String(v), v]));
      VirtualSelect.init({
        ele,
        options: filter.options.map(v => ({ label: String(v), value: String(v) })),
        multiple: true,
        search: true,
        selectedValue: ((store.boardFilters || {})[filter.field] || []).map(String),
        silentInitialValueSet: true,
        maxWidth: '100%',
        dropboxWrapper: 'body',
        zIndex: 60,
        ...MULTI_SELECT_TEXTS,
        placeholder: filter.label,
        ariaLabelText: filter.label,
        allOptionsSelectedText: `${filter.label}: todos`,
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