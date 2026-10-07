(function () {
  class BaseWidget {
    static type = null;
    static palette = {
      icon: 'ti-square',
      category: 'data',
      label: 'Widget',
      description: '',
    };
    static defaults = { title: 'Widget', width: 'md:col-span-6', height: 300 };
    static minHeight = 150;
    static CHART_COLORS = ['#4285F4', '#f59e52', '#ad7fe6', '#b5c665', '#2bb8ca', '#4CC38A', '#E36BB3', '#EF6E6E'];

    static FIELD_TITLE = { key: 'title', label: 'Título', type: 'text', tab: 'data' };

    // Lo que el backend declara del tipo: {label, style_schema, capabilities, max_per_dashboard}.
    static get manifest() {
      return (window.WIDGET_MANIFEST || {})[this.type] || {};
    }

    static get capabilities() {
      return this.manifest.capabilities || {};
    }

    static _capRange(key) {
      const range = this.capabilities[key];
      return Array.isArray(range) ? range : [0, 0];
    }

    static get maxDimensions() { return this._capRange('dimensions')[1]; }
    static get maxPivots() { return this._capRange('pivots')[1]; }
    static get maxMetrics() { return this._capRange('metrics')[1]; }
    static get minDimensions() { return this._capRange('dimensions')[0]; }
    static get minMetrics() { return this._capRange('metrics')[0]; }
    static get supportsDimension() { return this.maxDimensions > 0; }
    static get supportsPivot() { return this.maxPivots > 0; }
    static get supportsMetrics() { return this.maxMetrics > 0; }
    static get supportsSort() { return !!this.capabilities.sort; }
    static get supportsLimit() { return !!this.capabilities.limit; }
    static get supportsFilters() { return !!this.capabilities.filters; }
    static get singleton() { return this.manifest.max_per_dashboard === 1; }

    static placement = 'canvas';

    static _parseSpan(widthClass) {
      const m = /md:col-span-(\d+)/.exec(widthClass);
      return m ? parseInt(m[1], 10) : 6;
    }

    static getGhostSpan() {
      return BaseWidget._parseSpan(this.defaults.width);
    }

    _ghostSpanFromWidth() {
      return BaseWidget._parseSpan(this.width);
    }

    static escapeHTML(str) {
      const div = document.createElement('div');
      div.textContent = str;
      return div.innerHTML;
    }

    static percentAwareFormatter(percentNames, fallback = (val) => val, maxDigits = 2) {
      const names = new Set(percentNames || []);
      return (val, opts) => {
        const name = opts && opts.w ? opts.w.globals.seriesNames[opts.seriesIndex] : null;
        if (val == null || !names.has(name)) return fallback(val);
        return `${Number(val).toLocaleString(undefined, { maximumFractionDigits: maxDigits })}%`;
      };
    }

    static allSeriesPercent(payload, series) {
      const names = new Set(payload.percent || []);
      return series.length > 0 && series.every(s => names.has(s.name));
    }

    static REFERENCE_KINDS = [
      { value: 'value', label: 'Valor fijo' },
      { value: 'avg', label: 'Promedio' },
      { value: 'max', label: 'Máximo' },
      { value: 'min', label: 'Mínimo' },
    ];
    static REFERENCE_DEFAULT_LABELS = { avg: 'Promedio', max: 'Máx.', min: 'Mín.' };
    static REFERENCE_COLOR = '#d97706';

    static referenceValue(line, series) {
      if (line.kind === 'value') {
        return line.value === '' || line.value == null || isNaN(line.value) ? null : Number(line.value);
      }
      const source = line.series ? series.filter(s => s.name === line.series) : series;
      const values = source.flatMap(s => s.data || [])
        .filter(v => v != null && v !== '' && !isNaN(v)).map(Number);
      if (!values.length) return null;
      if (line.kind === 'max') return Math.max(...values);
      if (line.kind === 'min') return Math.min(...values);
      if (line.kind === 'avg') return values.reduce((a, b) => a + b, 0) / values.length;
      return null;
    }

    static referenceAnnotations(lines, series, { horizontal = false, format = (v) => v } = {}) {
      const items = (lines || []).map((line) => {
        const value = BaseWidget.referenceValue(line, series);
        if (value == null) return null;
        const color = line.color || BaseWidget.REFERENCE_COLOR;
        const name = (line.label || '').trim() || BaseWidget.REFERENCE_DEFAULT_LABELS[line.kind] || '';
        const text = name ? `${name}: ${format(value)}` : `${format(value)}`;
        return {
          [horizontal ? 'x' : 'y']: value,
          borderColor: color,
          strokeDashArray: 4,
          label: {
            text,
            borderColor: color,
            orientation: 'horizontal',
            ...(horizontal ? { position: 'top' } : { position: 'right', textAnchor: 'end' }),
            style: { color: '#fff', background: color, fontSize: '10px' },
          },
        };
      }).filter(Boolean);
      return {
        annotations: { [horizontal ? 'xaxis' : 'yaxis']: items },
        max: items.length ? Math.max(...items.map(a => a.x ?? a.y)) : null,
        min: items.length ? Math.min(...items.map(a => a.x ?? a.y)) : null,
      };
    }

    static referenceFormat(percentAxis) {
      return (val) => {
        const text = Number(val).toLocaleString(undefined, { maximumFractionDigits: 2 });
        return percentAxis ? `${text}%` : text;
      };
    }

    static DOWNLOAD_ICON_SVG = `<svg viewBox="0 0 14 14" class="w-3 h-3" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
      <path d="M7 1.5v8M7 9.5 4 6.5M7 9.5l3-3M2 11.5v1a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1v-1"/>
    </svg>`;

    downloadButtonHTML(title = 'Descargar CSV') {
      return `<button class="download-csv-btn text-ink/40 hover:text-moss-600 transition cursor-pointer p-0.5" title="${title}">
        ${BaseWidget.DOWNLOAD_ICON_SVG}
      </button>`;
    }

    _filenameSlug(fallback) {
      return (this.title || fallback).trim().toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '') || fallback;
    }

    chartExportToolbar() {
      const filename = this._filenameSlug('grafico');
      return {
        show: true,
        tools: { download: true, selection: false, zoom: false, zoomin: false, zoomout: false, pan: false, reset: false },
        export: {
          csv: { filename },
          svg: { filename },
          png: { filename },
        },
      };
    }

    downloadRowsAsCSV(headers, rows, filename) {
      const escapeCell = (v) => {
        const s = v === null || v === undefined ? '' : String(v);
        return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
      };
      const csv = [headers, ...rows].map(r => r.map(escapeCell).join(',')).join('\r\n');
      const blob = new Blob(['﻿' + csv], { type: 'text/csv;charset=utf-8;' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    }

    static dragHandleHTML() {
      return `<div class="drag-handle cursor-move text-ink/30 hover:text-ink/60 absolute left-1 top-0 bottom-0 flex items-center opacity-0 group-hover:opacity-100 transition-opacity z-20 text-[1rem] leading-none">⣿</div>`;
    }

    static actionButtonsHTML() {
      return `<div class="absolute -top-2.5 -right-2.5 flex items-center space-x-0.5 opacity-0 group-hover:opacity-100 transition-opacity bg-white border border-line rounded-md shadow-sm px-1 py-0.5 z-30">
        <button class="edit-widget-btn text-ink/40 hover:text-moss-600 transition cursor-pointer p-0.5">
          <svg viewBox="0 0 14 14" class="w-3 h-3" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
            <path d="M10.5 1.5a1.41 1.41 0 0 1 2 2L4.5 11.5l-3 1 1-3Z"/>
          </svg>
        </button>
        <button class="delete-widget-btn text-ink/40 hover:text-red-600 transition cursor-pointer p-0.5">
          <svg viewBox="0 0 14 14" class="w-3 h-3" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
            <path d="M3 3l8 8M11 3l-8 8"/>
          </svg>
        </button>
      </div>`;
    }

    // Campos vacíos nuevos en cada llamada: un objeto compartido con sus arrays haría que lo que
    // un widget agrega (ej. la métrica inicial del panel) aparezca en los siguientes.
    static emptyFields() {
      return { dimensions: [], pivots: [], metrics: [], filters: [], columns: [], sort_by: null, limit: null };
    }

    constructor(raw = {}) {
      const defaults = this.constructor.defaults;
      this.id = raw.id;
      this.type = raw.type || this.constructor.type;
      this.source_id = raw.source_id ?? null;
      this.title = raw.title || defaults.title;
      this.chart_type = this.constructor.type;
      this.position = raw.position || { x: 0, y: 0, w: 6, h: 300 };
      this.fields = raw.fields || BaseWidget.emptyFields();
      this.style = raw.style || {};
      this.data = raw.data || null;
      this._dirty = raw._dirty ?? false;
      this._loading = false;
      this.el = null;
      this._chart = null;
      this._lastEntry = null;
      this._lastData = null;
      this._legendSortable = null;
      this._legendObserver = null;
      this._interactable = null;
      this._readOnly = false;
      this.order = raw.order ?? 0;
      this.syncLayoutFromPosition();
    }

    // El ancho y el alto de la tarjeta salen de `position` (lo único que se guarda).
    syncLayoutFromPosition() {
      const pos = this.position || { x: 0, y: 0, w: 6, h: 300 };
      this.width = `md:col-span-${Math.min(12, Math.max(2, pos.w || 6))}`;
      this.height = pos.h || 300;
      this.startCol = pos.x ? `md:col-start-${Math.min(12, pos.x + 1)}` : '';
    }

    static fromServer(w) {
      // Un tipo que este navegador no conoce (widget de extensión no cargado, o borrado del
      // core) no puede montarse: se omite en vez de tumbar todo el tablero.
      if (!window.WidgetRegistry || !WidgetRegistry.has(w.type)) {
        console.warn(`Widget de tipo "${w.type ?? '?'}" no registrado: se omite del tablero.`);
        return null;
      }
      const pos = w.position || {};
      return WidgetRegistry.create(w.type, {
        id: w.id,
        type: w.type,
        source_id: w.source_id ?? null,
        title: w.title,
        position: { x: pos.x || 0, y: pos.y || 0, w: pos.w || 6, h: pos.h || 300 },
        fields: w.fields || BaseWidget.emptyFields(),
        style: w.style || {},
        data: w.data || null,
      });
    }

    get hasSpec() {
      return !!this.fields && ((this.fields.dimensions || []).length
        || (this.fields.metrics || []).length
        || (this.fields.columns || []).length);
    }

    buildElement() {
      throw new Error(`${this.constructor.name}: buildElement() no implementado`);
    }

    draw(data, style, title) {
      throw new Error(`${this.constructor.name}: draw() no implementado`);
    }

    loaderOverlayHTML() {
      return `<div class="widget-loader absolute inset-0 bg-white/80 flex items-center justify-center rounded-xl z-30 hidden">
        <svg class="animate-spin h-5 w-5 text-moss-500" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
          <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
          <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
        </svg>
      </div>`;
    }

    buildStandardCardElement() {
      const el = document.createElement('div');
      el.className = `col-span-12 ${this.width}${this.startCol ? ' ' + this.startCol : ''} bg-white border border-line rounded-xl shadow-sm p-4 flex flex-col justify-between relative group`;
      el.style.height = this.height + 'px';
      el.style.setProperty('--ghost-span', this._ghostSpanFromWidth());
      el.dataset.widgetId = this.id;
      el.dataset.type = this.chart_type;
      el.innerHTML = `
        ${BaseWidget.dragHandleHTML()}
        <div class="flex justify-between items-center border-line pb-2 mb-2 select-none">
          <span class="title-display text-[10px] font-bold uppercase tracking-wider text-ink/40">${BaseWidget.escapeHTML(this.title)}</span>
        </div>
        <div id="chart-${this.id}" class="flex-1 w-full min-h-0"></div>
        ${this.loaderOverlayHTML()}
        ${BaseWidget.actionButtonsHTML()}
        <div class="resize-handle absolute bottom-1 right-1 w-4 h-4 cursor-se-resize z-10 opacity-60 hover:opacity-100 transition">
          <svg viewBox="0 0 10 10" class="w-full h-full text-ink/30" fill="none">
            <line x1="2" y1="8" x2="8" y2="2" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
            <line x1="4" y1="8" x2="8" y2="4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
          </svg>
        </div>`;
      return el;
    }

    mount() {
      this.el = this.buildElement();
      this._attachCommonEvents();
      this.setLoading(this.hasSpec);
      if (!this.hasSpec) this.renderPlaceholder();
      return this.el;
    }

    // Un widget recién creado cambia de id local (-1) al id real: el DOM se pinta otra vez
    // porque los ids viven en el elemento (data-widget-id y el contenedor del gráfico).
    rebuildElement() {
      const old = this.el;
      const parent = old && old.parentElement;
      this._detachResize();
      this.el = null;
      const el = this.mount();
      if (parent) parent.replaceChild(el, old);
      this.applyRender(this._lastEntry);
      return el;
    }

    buildReadOnlyElement() {
      const el = document.createElement('div');
      el.className = `col-span-12 ${this.width}${this.startCol ? ' ' + this.startCol : ''} bg-white border border-line rounded-xl shadow-sm p-4 flex flex-col justify-between relative`;
      el.style.height = this.height + 'px';
      el.dataset.widgetId = this.id;
      el.dataset.type = this.chart_type;
      const titleHTML = this.title
        ? `<div class="flex items-center border-line pb-2 mb-2"><span class="text-[10px] font-bold uppercase tracking-wider text-ink/40">${BaseWidget.escapeHTML(this.title)}</span></div>`
        : '';
      el.innerHTML = `${titleHTML}<div id="chart-${this.id}" class="flex-1 w-full min-h-0"></div>${this.loaderOverlayHTML()}
        <div class="actions-slot absolute top-2 right-2 z-30 flex items-center gap-1"></div>`;
      return el;
    }

    mountReadOnly() {
      this._readOnly = true;
      this.el = this.buildReadOnlyElement();
      this.setLoading(true);
      return this.el;
    }

    getContentContainer() {
      return this.el ? this.el.querySelector(`#chart-${this.id}`) : null;
    }

    setLoading(isLoading) {
      this._loading = isLoading;
      const loader = this.el && this.el.querySelector('.widget-loader');
      if (loader) loader.classList.toggle('hidden', !isLoading);
    }

    updateChrome() {
      if (!this.el) return;
      this.syncLayoutFromPosition();
      const titleEl = this.el.querySelector('.title-display');
      if (titleEl) titleEl.textContent = this.title;

      Array.from(this.el.classList).filter(c => c.startsWith('col-start-') || c.startsWith('md:col-start-') || c.startsWith('md:col-span-')).forEach(c => this.el.classList.remove(c));
      this.el.classList.add(this.width);
      if (this.startCol) this.el.classList.add(this.startCol);
      this.el.style.height = this.height + 'px';
      this.el.style.setProperty('--ghost-span', this._ghostSpanFromWidth());

      window.dispatchEvent(new Event('resize'));
    }

    getProperties() {
      return { ...this.style };
    }

    getPosition() {
      const pos = this.position || { x: 0, y: 0, w: 6, h: 300 };
      return {
        x: pos.x,
        y: pos.y,
        w: pos.w,
        h: pos.h,
      };
    }

    toPayload() {
      return {
        id: this.id,
        type: this.type,
        title: this.title,
        position: this.getPosition(),
        fields: this.fields,
        style: this.style,
      };
    }

    renderApexChart(container, options) {
      if (this._chart) {
        this._chart.destroy();
        this._chart = null;
      }
      container.innerHTML = '<div style="height:100%;width:100%;"></div>';
      const mountEl = container.firstElementChild;
      this._chart = new ApexCharts(mountEl, options);
      return this._chart.render();
    }

    renderError(message, { retryable = false } = {}) {
      const container = this.getContentContainer();
      if (!container) return;
      if (this._chart) {
        this._chart.destroy();
        this._chart = null;
      }
      container.innerHTML = `
        <div class="h-full w-full flex flex-col items-center justify-center text-center gap-2 px-3">
          <span class="text-xs text-red-600">${BaseWidget.escapeHTML(message || 'Error al cargar los datos')}</span>
          ${retryable ? `<button type="button" class="retry-widget-btn text-xs font-medium text-moss-700 border border-moss-300 hover:bg-moss-tint rounded-lg px-3 py-1 transition cursor-pointer">Reintentar</button>` : ''}
        </div>
      `;
      if (retryable) {
        container.querySelector('.retry-widget-btn').addEventListener('click', () => Alpine.store('dashboard').refreshData());
      }
    }

    renderPlaceholder() {
      const container = this.getContentContainer();
      if (!container) return;
      container.innerHTML = `
        <div class="h-full w-full flex flex-col items-center justify-center text-center gap-1 px-4">
          <i class="ti ti-sparkles text-xl text-moss-500" aria-hidden="true"></i>
          <span class="text-xs text-ink/50">Abre el panel de edición para elegir columnas y métricas, o descríbelo con IA.</span>
        </div>`;
    }

    showEmptyState() {
      const container = this.getContentContainer();
      if (!container) return;
      container.innerHTML = '<div class="flex items-center justify-center h-full text-gray-400 text-sm">Sin datos para mostrar</div>';
    }

    applyRender(entry) {
      this.setLoading(false);
      if (!entry) return;
      this._lastEntry = entry;
      if (entry.error) {
        this.renderError(entry.error);
        return;
      }
      const container = this.getContentContainer();
      if (container && entry.data) {
        this.draw(entry.data, entry.style || {}, entry.title);
      }
    }

    _attachCommonEvents() {
      const el = this.el;
      if (!el) return;
      const editBtn = el.querySelector('.edit-widget-btn');
      const deleteBtn = el.querySelector('.delete-widget-btn');
      if (editBtn) {
        editBtn.addEventListener('click', () => {
          Alpine.store('dashboard').openDrawer(this.id);
        });
      }
      if (deleteBtn) {
        deleteBtn.addEventListener('click', () => {
          Alpine.store('dashboard').removeWidget(this.id);
          this.destroy();
          el.remove();
        });
      }

      this.initResize();
    }

    destroy() {
      if (this._chart) {
        this._chart.destroy();
        this._chart = null;
      }
      this._destroyLegendSortable();
      if (this._legendObserver) { this._legendObserver.disconnect(); this._legendObserver = null; }
      this._detachResize();
    }

    // interact.js arranca el resize desde el asa de la esquina: ancho y alto en el mismo
    // gesto, con el snap hecho en `_onResizeMove` (una columna entera y múltiplos de 20 px).
    initResize() {
      const el = this.el;
      if (!el || this._readOnly || this._interactable || typeof interact === 'undefined') return;
      if (!el.querySelector('.resize-handle')) return;
      let moved = false;
      this._interactable = interact(el).resizable({
        edges: { bottom: '.resize-handle', right: '.resize-handle' },
        listeners: {
          start: () => { document.body.style.userSelect = 'none'; },
          move: (event) => {
            moved = true;
            this._onResizeMove(event);
          },
          end: (event) => {
            document.body.style.userSelect = '';
            if (!moved) return;
            moved = false;
            // El ancho en px solo hace que el borde siga al cursor: al soltar manda la clase
            // md:col-span-N, y en móvil col-span-12 (con px en línea se quedaría fijo).
            const target = this.el || event.target;
            if (target) target.style.width = '';
            this._dirty = true;
            Alpine.store('dashboard').scheduleLayoutSave();
          },
        },
      });
    }

    _detachResize() {
      if (!this._interactable) return;
      this._interactable.unset();
      this._interactable = null;
    }

    // Ancho de una columna del lienzo: 12 columnas con sus gutters, descontando el p-4.
    _singleColumnWidth() {
      const canvas = document.getElementById('dashboard-canvas');
      if (!canvas) return null;
      const css = window.getComputedStyle(canvas);
      const gap = parseFloat(css.columnGap || css.gap) || 16;
      const inner = canvas.clientWidth
        - (parseFloat(css.paddingLeft) || 0)
        - (parseFloat(css.paddingRight) || 0);
      const colW = (inner - gap * 11) / 12;
      return colW > 0 ? { colW, gap } : null;
    }

    _onResizeMove(event) {
      const target = this.el || event.target;
      if (!target) return;
      const pos = this.position || { x: 0, y: 0, w: 6, h: 300 };
      this.position = pos;

      const step = 20;
      this.height = Math.max(
        this.constructor.minHeight,
        Math.min(3000, Math.round(event.rect.height / step) * step),
      );
      pos.h = this.height;

      const grid = this._singleColumnWidth();
      if (grid) {
        const cols = Math.max(2, Math.min(12 - (pos.x || 0),
          Math.round((event.rect.width + grid.gap) / (grid.colW + grid.gap))));
        pos.w = cols;
        target.style.width = cols * grid.colW + (cols - 1) * grid.gap + 'px';
      }

      this.updateChrome();
    }

    _destroyLegendSortable() {
      const sortable = this._legendSortable;
      this._legendSortable = null;
      if (!sortable) return;
      if (Sortable.active || Sortable.dragged) {
        const endEvents = ['dragend', 'mouseup', 'touchend'];
        const done = () => {
          endEvents.forEach(type => document.removeEventListener(type, done));
          setTimeout(() => sortable.destroy());
        };
        endEvents.forEach(type => document.addEventListener(type, done));
      } else {
        sortable.destroy();
      }
    }
  }

  window.BaseWidget = BaseWidget;
})();