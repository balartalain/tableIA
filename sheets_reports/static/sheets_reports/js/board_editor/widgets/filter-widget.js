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
      el.className = 'filter-widget-container contents';
      el.dataset.widgetId = this.id;
      el.dataset.type = this.chart_type;
      el.innerHTML = `
        <div id="chart-${this.id}" class="contents"></div>
        ${this.loaderOverlayHTML()}`;
      if (editable) {
        el.addEventListener('click', (e) => {
          if (e.target.closest('.vscomp-ele') || e.target.closest('.vscomp-wrapper')) return;
          Alpine.store('dashboard').openDrawer(this.id);
        });
      }
      return el;
    }

    buildElement() {
      return this._buildBar(true);
    }

    buildReadOnlyElement() {
      return this._buildBar(false);
    }

    // Miniatura: la caja de un filtro (aunque los filtros van en la cabecera, no en el lienzo).
    thumbnailContent(ctx) {
      const { x, y, w, primary } = ctx;
      const bw = Math.min(Math.max(20, w - 12), 60);
      return `
        <rect x="${x + 6}" y="${y + 14}" width="${bw}" height="12" rx="2" fill="${ctx.theme.card}" stroke="${ctx.theme.border}"/>
        <rect x="${x + 10}" y="${y + 18}" width="${Math.max(6, bw - 20)}" height="3" rx="1.5" fill="${primary}" opacity="0.4"/>`;
    }

    updateChrome() {
    }

    getProperties() {
      return { ...super.getProperties() };
    }

    renderPlaceholder() {
      const container = this.getContentContainer();
      if (!container) return;
      this._destroySelects();
      container.innerHTML = '';
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
        wrap.className = 'w-56 max-w-full shrink-0';
        wrap.title = filter.label;
        wrap.innerHTML = '<div class="filter-control"></div>';
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