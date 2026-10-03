(function () {
  class BaseWidget {
    static type = null;
    // Entrada en la barra de módulos: `icon` es una clase de Tabler Icons (de trazo) y
    // `category` el grupo donde aparece ('charts' = Gráficos, 'data' = Datos).
    static palette = {
      icon: 'ti-square',
      category: 'data',
      label: 'Widget',
      description: '',
    };
    static defaults = { title: 'Widget', width: 'md:col-span-6', height: 300 };
    static minHeight = 150;
    // Paleta de las series de los gráficos (barras, líneas, dona), en este orden. Con más
    // series de las que tiene, se repite desde el principio.
    static CHART_COLORS = ['#4285F4', '#f59e52', '#ad7fe6', '#b5c665', '#2bb8ca', '#4CC38A', '#E36BB3', '#EF6E6E'];

    // Pestaña del panel de edición: 'data' (Configurar) o, si se omite, 'style' (Personalizar).
    static FIELD_TITLE = { key: 'title', label: 'Título', type: 'text', tab: 'data' };

    // Constructor estructurado (dimensión, pivote, métricas, apiladas): edita data_spec sin IA.
    static FIELD_BUILDER = { key: 'builder', label: 'Datos', type: 'builder', tab: 'data' };

    // Capacidades del tipo: NO se declaran aquí, salen del manifiesto que publica el backend
    // (WidgetType.manifest: DataCapabilities + campos de su options_cls; ver board_editor.html).
    // Sin manifiesto (vista compartida, o el panel sin tipo elegido) valen las de un gráfico.
    static DEFAULT_MANIFEST = {
      data: { dimensions: [0, 1], pivots: [0, 1], columns: [0, 0], metrics: [1, 5],
        metric_types: ['agg', 'calc'], show_as: true, sort: true },
      parts: [],
      view: [],
      max_per_dashboard: null,
    };

    static get manifest() {
      return (window.WIDGET_MANIFEST || {})[this.type] || BaseWidget.DEFAULT_MANIFEST;
    }

    // Opción de vista propia del tipo (un campo de su options_cls), ej. 'stacked'.
    static supportsView(key) {
      return (this.manifest.view || []).includes(key);
    }

    static get maxDimensions() { return this.manifest.data.dimensions[1]; }
    static get maxPivots() { return this.manifest.data.pivots[1]; }
    static get maxMetrics() { return this.manifest.data.metrics[1]; }
    static get maxColumns() { return this.manifest.data.columns[1]; }
    // El KPI no agrupa: su builder no muestra dimensión ni pivote.
    static get supportsDimension() { return this.maxDimensions > 0; }
    static get supportsPivot() { return this.maxPivots > 0; }
    // Métricas: el builder las muestra salvo en widgets que no resumen datos.
    static get supportsMetrics() { return this.maxMetrics > 0; }
    // Columnas que se muestran tal cual, sin agrupar (data_spec.columns).
    static get usesColumns() { return this.maxColumns > 0; }
    // "Mostrar como" porcentaje en las métricas (la dona no: ApexCharts calcula sus %).
    static get supportsShowAs() { return !!this.manifest.data.show_as; }
    static get supportsSort() { return !!this.manifest.data.sort; }
    static get metricTypes() { return this.manifest.data.metric_types || []; }
    // Solo las barras tienen "apiladas" (y solo con pivote); barras y líneas, líneas de referencia.
    static get supportsStacked() { return this.supportsView('stacked'); }
    static get supportsReferenceLines() { return this.supportsView('reference_lines'); }
    // Solo uno por tablero (la caja de filtros).
    static get singleton() { return this.manifest.max_per_dashboard === 1; }

    // --- Lo que sigue es solo de interfaz: no tiene equivalente en el backend. Los controles
    // del panel de datos (textos, listas, opciones) NO van aquí: salen de manifest.parts.
    // Con cuántas columnas (y de dónde) arranca un widget nuevo con columnas sueltas.
    static defaultColumns = 5;
    static defaultColumnsFrom = 'all_fields';
    // Opciones de vista por columna de la lista de columnas. `columnControls`: tipos a elegir
    // por columna (caja de filtros).
    static columnControls = null;
    // "Nombre a mostrar" por columna (view_spec.labels), ej. la etiqueta de cada filtro.
    static supportsColumnLabels = false;
    // Dónde se monta: 'canvas' (grid de 12 columnas) o 'header' (fijo arriba, a todo el ancho).
    static placement = 'canvas';
    // Nombre a mostrar de cada métrica (cabecera de columna, nombre de serie).
    static supportsLabels = false;
    // "Mostrar totales" por nivel de filas/columnas (props rowTotals/columnTotals).
    static supportsTotals = false;

    // El grid del lienzo tiene 12 columnas (position.w), así que el ancho se expresa en
    // columnas y no en porcentaje: "12 columnas" es el ancho completo.
    static FIELD_WIDTH = {
      key: 'width',
      label: 'Ancho',
      type: 'select',
      options: [
        { value: 'md:col-span-2', label: '2 columnas' },
        { value: 'md:col-span-3', label: '3 columnas' },
        { value: 'md:col-span-4', label: '4 columnas' },
        { value: 'md:col-span-5', label: '5 columnas' },
        { value: 'md:col-span-6', label: '6 columnas' },
        { value: 'md:col-span-7', label: '7 columnas' },
        { value: 'md:col-span-8', label: '8 columnas' },
        { value: 'md:col-span-9', label: '9 columnas' },
        { value: 'md:col-span-10', label: '10 columnas' },
        { value: 'md:col-span-11', label: '11 columnas' },
        { value: 'md:col-span-12', label: '12 columnas' },
      ],
    };

    static get FIELD_HEIGHT() {
      return { key: 'height', label: 'Alto (px)', type: 'number', min: this.minHeight, step: 10 };
    }

    // El lienzo es un grid de 12 columnas: un widget que empieza en N y ocupa w columnas
    // termina en N + w - 1, así que N no puede pasar de 13 - w.
    static GRID_COLUMNS = 12;

    static FIELD_START_COL = {
      key: 'startCol',
      label: 'Columna de inicio',
      type: 'select',
      options: [
        { value: '', label: 'Fluido' },
        { value: 'md:col-start-1', label: 'Al inicio' },
        { value: 'md:col-start-2', label: 'Dejar 1 columna' },
        { value: 'md:col-start-3', label: 'Dejar 2 columnas' },
        { value: 'md:col-start-4', label: 'Dejar 3 columnas' },
        { value: 'md:col-start-5', label: 'Dejar 4 columnas' },
        { value: 'md:col-start-6', label: 'Dejar 5 columnas' },
        { value: 'md:col-start-7', label: 'Dejar 6 columnas' },
        { value: 'md:col-start-8', label: 'Dejar 7 columnas' },
        { value: 'md:col-start-9', label: 'Dejar 8 columnas' },
        { value: 'md:col-start-10', label: 'Dejar 9 columnas' },
        { value: 'md:col-start-11', label: 'Dejar 10 columnas' },
      ],
    };

    // Columna inicial (1..12) a la que deja una opción de FIELD_START_COL.
    static _startColNumber(startCol) {
      const m = /md:col-start-(\d+)/.exec(startCol || '');
      return m ? parseInt(m[1], 10) : 0;
    }

    static _startColValue(n) {
      return `md:col-start-${n}`;
    }

    // Última columna inicial posible para un widget de `width` columnas.
    static _maxStartCol(width) {
      return this.GRID_COLUMNS - BaseWidget._parseSpan(width) + 1;
    }

    // Opciones de inicio que no desbordan el grid con el ancho elegido. "Fluido" siempre vale.
    static startColOptionsForWidth(width) {
      const max = BaseWidget._maxStartCol(width);
      return BaseWidget.FIELD_START_COL.options.filter(
        (opt) => !opt.value || BaseWidget._startColNumber(opt.value) <= max
      );
    }

    // Si al elegir un ancho más grande la columna de inicio quedó fuera, se ajusta al último
    // inicio posible: si no, el <select> se quedaría sin opción coincidente con su valor.
    static fitStartCol(startCol, width) {
      const n = BaseWidget._startColNumber(startCol);
      if (!n) return startCol || '';
      const max = BaseWidget._maxStartCol(width);
      return n <= max ? startCol : BaseWidget._startColValue(max);
    }

    static get drawerFields() {
      return [
        this.FIELD_TITLE, this.FIELD_BUILDER,
        this.FIELD_WIDTH, this.FIELD_START_COL,
      ];
    }

    // Reconstruye view_spec completo a partir de data_spec + view_options (solo lo editable).
    // Lo usa applyServerState cuando el backend devuelve view_options en lugar de view_spec.
    static reconstructViewSpec(dataSpec, viewOptions) {
      const spec = dataSpec || {};
      const dims = spec.dimensions || [];
      const metrics = (spec.metrics || []).map(m => m.as).filter(Boolean);
      const percent = (spec.metrics || []).filter(m => m.show_as && m.show_as !== 'value').map(m => m.as);
      return {
        widget: this.type,
        x: dims[0] || '',
        metrics,
        percent,
        title: viewOptions.title || '',
        labels: viewOptions.labels || {},
        display: viewOptions.display || {},
      };
    }

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

    // Formateador de ApexCharts que agrega "%" a las series de métricas pct_* (payload.percent
    // trae sus nombres). `fallback(val)` formatea el resto de las series.
    static percentAwareFormatter(percentNames, fallback = (val) => val, maxDigits = 2) {
      const names = new Set(percentNames || []);
      return (val, opts) => {
        const name = opts && opts.w ? opts.w.globals.seriesNames[opts.seriesIndex] : null;
        if (val == null || !names.has(name)) return fallback(val);
        return `${Number(val).toLocaleString(undefined, { maximumFractionDigits: maxDigits })}%`;
      };
    }

    // true si todas las series del gráfico son porcentajes (el eje Y puede llevar "%").
    static allSeriesPercent(payload, series) {
      const names = new Set(payload.percent || []);
      return series.length > 0 && series.every(s => names.has(s.name));
    }

    // Líneas de referencia de barras y líneas (view_spec.reference_lines; el render las trae en
    // `referenceLines` con la serie ya resuelta a su nombre): un valor fijo o el
    // promedio/máximo/mínimo de una serie (o de todas), calculado con los datos dibujados.
    static REFERENCE_KINDS = [
      { value: 'value', label: 'Valor fijo' },
      { value: 'avg', label: 'Promedio' },
      { value: 'max', label: 'Máximo' },
      { value: 'min', label: 'Mínimo' },
    ];
    static REFERENCE_DEFAULT_LABELS = { avg: 'Promedio', max: 'Máx.', min: 'Mín.' };
    static REFERENCE_COLOR = '#d97706';

    // Valor de una línea de referencia, o null si no se puede calcular (serie que ya no está,
    // valor fijo vacío).
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

    // Anotaciones de ApexCharts para las líneas: en el eje de valores, que en barras
    // horizontales es el X. `format(val)` da el texto del valor en la etiqueta.
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
        // Para que una línea fuera del rango de los datos (ej. una meta) no quede cortada.
        max: items.length ? Math.max(...items.map(a => a.x ?? a.y)) : null,
        min: items.length ? Math.min(...items.map(a => a.x ?? a.y)) : null,
      };
    }

    // Formato del valor en la etiqueta de una línea: "%" si todas las series lo son.
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

    // Los widgets de tipo ApexChart no usan el botón de descarga genérico: usan el menú
    // de exportación propio del toolbar de ApexCharts (PNG/SVG/CSV del gráfico).
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

    // `raw` son las preferencias de UI (view_spec.display) más title/width/height/startCol/order;
    // ver BaseWidget.fromServer para el mapeo desde el widget serializado por el backend.
    constructor(raw = {}) {
      const defaults = this.constructor.defaults;
      this.id = raw.id;
      this.title = raw.title || defaults.title;
      this.chart_type = this.constructor.type;
      this.prompt = '';
      this.data_spec = raw.data_spec || null;
      this.view_spec = raw.view_spec || null;
      this.source_prompt = raw.source_prompt || '';
      this.width = raw.width || defaults.width;
      this.height = raw.height ?? defaults.height;
      this.startCol = raw.startCol || '';
      this.order = raw.order ?? 0;
      this._dirty = raw._dirty ?? false;
      this._loading = false;
      this.el = null;
      this._chart = null;
    }

    // Widget serializado por el backend ({id, type, position, data_spec?, view_spec?, ...}) ->
    // instancia de la clase registrada para `type`.
    // data_spec y view_spec son opcionales: en la carga inicial del tablero no vienen
    // (se piden vía AJAX al editar). El widget renderiza solo con datos compilados.
    static fromServer(w) {
      const pos = w.position || {};
      const view = w.view_spec || {};
      return WidgetRegistry.create(w.type, {
        ...(view.display || {}),
        id: w.id,
        title: view.title,
        data_spec: w.data_spec || null,
        view_spec: w.view_spec || null,
        source_prompt: w.source_prompt || '',
        width: `md:col-span-${pos.w || 6}`,
        startCol: pos.x ? `md:col-start-${pos.x}` : '',
        height: pos.h,
        order: pos.y ?? 0,
      });
    }

    // Actualiza el widget con la versión que devolvió el backend (tras generar/editar el spec).
    // Acepta tanto view_spec (respuesta antigua) como view_options (nueva respuesta de /config/).
    applyServerState(w) {
      if (w.data_spec !== undefined) this.data_spec = w.data_spec;
      if (w.source_prompt !== undefined) this.source_prompt = w.source_prompt || '';

      // Nuevo formato: view_options (solo lo editable)
      if (w.view_options !== undefined) {
        const viewSpec = this.constructor.reconstructViewSpec?.(this.data_spec, w.view_options) || w.view_options;
        this.view_spec = viewSpec;
        if (viewSpec.title) this.title = viewSpec.title;
      }
      // Formato antiguo: view_spec completo
      else if (w.view_spec !== undefined) {
        this.view_spec = w.view_spec;
        if (w.view_spec.title) this.title = w.view_spec.title;
      }
    }

    get hasSpec() {
      return !!this.data_spec;
    }

    buildElement() {
      throw new Error(`${this.constructor.name}: buildElement() no implementado`);
    }

    renderContent(container, data) {
      throw new Error(`${this.constructor.name}: renderContent() no implementado`);
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

    // Vista compartida: sin acciones de edición (arrastres, menús de formato...). Cada widget
    // lo consulta en this._readOnly.
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
      const titleEl = this.el.querySelector('.title-display');
      if (titleEl) titleEl.textContent = this.title;

      Array.from(this.el.classList).filter(c => c.startsWith('col-start-') || c.startsWith('md:col-start-') || c.startsWith('md:col-span-')).forEach(c => this.el.classList.remove(c));
      this.el.classList.add(this.width);
      if (this.startCol) this.el.classList.add(this.startCol);
      this.el.style.height = this.height + 'px';
      this.el.style.setProperty('--ghost-span', this._ghostSpanFromWidth());

      window.dispatchEvent(new Event('resize'));
    }

    // Preferencias puramente visuales del frontend; se guardan en view_spec.display.
    getProperties() {
      return {};
    }

    getPosition() {
      const startMatch = /md:col-start-(\d+)/.exec(this.startCol || '');
      return {
        x: startMatch ? parseInt(startMatch[1], 10) : 0,
        y: this.order,
        w: BaseWidget._parseSpan(this.width),
        h: this.height,
      };
    }

    toPayload() {
      return { title: this.title, position: this.getPosition(), display: this.getProperties() };
    }

    renderApexChart(container, options) {
      if (this._chart) {
        this._chart.destroy();
        this._chart = null;
      }
      // ApexCharts pisa el min-height del elemento donde se monta (lo fuerza a "unset"),
      // lo que anula nuestro min-h-0 y le impide encogerse dentro del flex-col de la
      // tarjeta (por ej. para dejarle lugar al pie de resumen). Le damos un div interno
      // para montar, así esa mutación cae sobre un hijo normal y nunca sobre `container`
      // (el flex item real).
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

    // `entry` es el item de este widget en la respuesta de /render/ (o de generar/editar el
    // spec): {data} ya compilado por el backend, o {error}.
    applyRender(entry) {
      this.setLoading(false);
      if (!entry) return;
      this._lastEntry = entry;
      if (entry.error) {
        this.renderError(entry.error);
        return;
      }
      const container = this.getContentContainer();
      if (container && entry.data) this.renderContent(container, entry.data);
    }

    _attachCommonEvents() {
      const el = this.el;
      el.querySelector('.edit-widget-btn').addEventListener('click', () => {
        Alpine.store('dashboard').openDrawer(this.id);
      });

      el.querySelector('.delete-widget-btn').addEventListener('click', () => {
        Alpine.store('dashboard').removeWidget(this.id);
        this.destroy();
        el.remove();
      });

      const resizeHandle = el.querySelector('.resize-handle');
      if (resizeHandle) resizeHandle.addEventListener('mousedown', (e) => this._onResizeStart(e));
    }

    destroy() {
      if (this._chart) {
        this._chart.destroy();
        this._chart = null;
      }
    }

    _onResizeStart(e) {
      e.preventDefault();
      e.stopPropagation();

      const el = this.el;
      const minHeight = this.constructor.minHeight;
      const stepHeight = 20;
      const startY = e.clientY;
      const startHeight = el.offsetHeight;

      function clamp(value, min, max) {
        return Math.max(min, Math.min(max, value));
      }
      const onMouseMove = (ev) => {
        const newHeight = Math.max(minHeight, startHeight + (ev.clientY - startY));
        el.style.height = newHeight + 'px';
        window.dispatchEvent(new Event('resize'));
      };

      const onMouseUp = () => {
        el.classList.add('is-snapping');
        this.height = clamp(Math.round(el.offsetHeight/stepHeight)*stepHeight, minHeight, 3000);//Max height 3000px
        el.style.height = this.height + 'px';
        this._dirty = true;
        Alpine.store('dashboard').scheduleLayoutSave();
        document.removeEventListener('mousemove', onMouseMove);
        document.removeEventListener('mouseup', onMouseUp);
         setTimeout(() => el.classList.remove('is-snapping'), 160);
      };

      document.addEventListener('mousemove', onMouseMove);
      document.addEventListener('mouseup', onMouseUp);
    }
  }

  window.BaseWidget = BaseWidget;
})();
