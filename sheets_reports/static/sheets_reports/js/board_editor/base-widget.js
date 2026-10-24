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

    static FIELD_TITLE = { key: 'title', label: 'Título', type: 'text', tab: 'data' };

    // Lo que el backend declara del tipo: {label, style_schema, capabilities, max_per_page}.
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
    static get singleton() { return this.manifest.max_per_page === 1; }

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

    // `decimals`: null = hasta 2 (como formatNumber).
    static percentAwareFormatter(percentNames, fallback = (val) => val, decimals = null) {
      const names = new Set(percentNames || []);
      return (val, opts) => {
        const name = opts && opts.w ? opts.w.globals.seriesNames[opts.seriesIndex] : null;
        if (val == null || !names.has(name)) return fallback(val);
        return formatNumber(val, { percent: true, decimals });
      };
    }

    // Etiquetas del eje de valores: sin formato, ApexCharts toma los decimales de la serie más
    // fina (un 0,97 junto a miles da «17920.0000000000»).
    static axisNumber(val) {
      return val == null || isNaN(val) ? val : formatNumber(val);
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

    // `theme`: ChartTheme.read() del lienzo del widget (color de la línea y de su rótulo).
    static referenceAnnotations(lines, series, { horizontal = false, format = (v) => v, theme } = {}) {
      const t = theme || ChartTheme.read();
      const items = (lines || []).map((line) => {
        const value = BaseWidget.referenceValue(line, series);
        if (value == null) return null;
        const color = t.ref;
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
            style: { color: t.refText, background: color, fontSize: '10px' },
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
        return formatNumber(val, { percent: percentAxis });
      };
    }

    static DOWNLOAD_ICON_SVG = `<svg viewBox="0 0 14 14" class="w-3 h-3" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
      <path d="M7 1.5v8M7 9.5 4 6.5M7 9.5l3-3M2 11.5v1a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1v-1"/>
    </svg>`;

    downloadButtonHTML(title = 'Descargar CSV') {
      return `<button class="download-csv-btn text-fg-muted hover:text-accent-text transition cursor-pointer p-0.5" title="${title}">
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

    // --- Miniaturas del tablero (modo plantilla) -----------------------------
    // Cada widget sabe dibujarse en la miniatura: el generador mide la geometría real
    // (generateRealBoardThumbnail) y le pasa un `ctx`; el widget devuelve el SVG interior de
    // su tarjeta (el cromo y el título los pinta el generador). Datos de reserva permitidos.

    // Colores del lienzo resueltos (ChartTheme lee las variables --tb-* de su `.tablero`).
    themeColors() {
      return ChartTheme.read(this.el);
    }

    // Opciones base de ApexCharts del tema del tablero; cada widget las mezcla con las suyas.
    themeOptions() {
      return ChartTheme.baseOptions(this.el);
    }

    // Paleta categórica del tablero, repetida hasta `count` colores.
    seriesColors(count = 1) {
      const palette = this.themeColors().categorical;
      return Array.from({ length: Math.max(1, count) }, (_, i) => palette[i % palette.length]);
    }

    // Color de texto legible sobre cada color de seriesColors(count).
    seriesTextColors(count = 1) {
      const on = this.themeColors().onCategorical;
      return Array.from({ length: Math.max(1, count) }, (_, i) => on[i % on.length]);
    }

    // Paleta de la miniatura: la categórica del tablero.
    thumbnailPalette() {
      return this.themeColors().categorical;
    }

    // Contenido interior de la miniatura. Por defecto, nada (el generador dibuja barras).
    // ctx = { x, y, w, h, scale, el, content, colors, primary } (coordenadas absolutas).
    thumbnailContent(ctx) {
      return '';
    }

    // Barras verticales genéricas (reserva del generador y base de algunos esquemáticos).
    static thumbBars({ x, y, w, h, colors }) {
      const palette = colors && colors.length ? colors : ChartTheme.read().categorical;
      const count = Math.min(6, Math.max(2, Math.floor(w / 12)));
      const bw = Math.max(2.5, (w - 16 - (count - 1) * 3) / count);
      const heights = [0.4, 0.85, 0.3, 0.7, 0.55, 0.65];
      const maxBarH = Math.max(6, h - 24);
      let barX = x + 8;
      let bars = '';
      for (let b = 0; b < count; b++) {
        const bh = Math.max(3, maxBarH * heights[b % heights.length]);
        const by = y + h - 8 - bh;
        bars += `<rect x="${barX}" y="${by}" width="${bw}" height="${bh}" rx="1" fill="${palette[b % palette.length]}"/>`;
        barX += bw + 3;
      }
      return bars;
    }

    // Rejilla tipo heatmap (matriz de correlación / V de Cramér) con la diagonal marcada.
    static thumbGrid({ x, y, w, h }, shades, { rows = 4, cols = 4 } = {}) {
      const gx = x + 6;
      const gy = y + 15;
      const gw = Math.max(8, w - 12);
      const gh = Math.max(10, h - 20);
      const cw = gw / cols;
      const ch = gh / rows;
      let cells = '';
      for (let r = 0; r < rows; r++) {
        for (let c = 0; c < cols; c++) {
          const fill = r === c ? shades[0] : shades[(r + c) % shades.length];
          cells += `<rect x="${(gx + c * cw).toFixed(1)}" y="${(gy + r * ch).toFixed(1)}" width="${(cw - 0.8).toFixed(1)}" height="${(ch - 0.8).toFixed(1)}" rx="1" fill="${fill}"/>`;
        }
      }
      return cells;
    }

    // Reescribe ids y url(#id) para que varios SVG ApexCharts anidados no colisionen.
    static thumbNamespaceSvgIds(root, suffix) {
      const map = new Map();
      root.querySelectorAll('[id]').forEach(node => {
        const old = node.getAttribute('id');
        const nid = `${old}__${suffix}`;
        map.set(old, nid);
        node.setAttribute('id', nid);
      });
      if (!map.size) return;
      const rewrite = v => v.replace(/#([A-Za-z0-9_\-:.]+)/g, (m, id) => (map.has(id) ? `#${map.get(id)}` : m));
      const attrNames = ['fill', 'stroke', 'clip-path', 'mask', 'filter', 'marker-start', 'marker-mid', 'marker-end', 'href', 'xlink:href'];
      root.querySelectorAll('*').forEach(node => {
        attrNames.forEach(a => {
          const v = node.getAttribute(a);
          if (v && v.indexOf('#') !== -1) node.setAttribute(a, rewrite(v));
        });
        const style = node.getAttribute('style');
        if (style && style.indexOf('url(#') !== -1) {
          node.setAttribute('style', style.replace(/url\(#([^)]+)\)/g, (m, id) => (map.has(id) ? `url(#${map.get(id)})` : m)));
        }
      });
    }

    // Devuelve el markup SVG autocontenido del gráfico ApexCharts (sin width/height, con
    // viewBox), o '' si no se puede obtener.
    static async thumbApexSvgMarkup(widget, suffix) {
      try {
        if (!widget || !widget._chart || typeof widget._chart.dataURI !== 'function') return '';
        const res = await widget._chart.dataURI({ type: 'svg' });
        const uri = res && typeof res === 'string' ? res : (res && res.imgURI);
        if (!uri) return '';
        let svgStr;
        if (/^\s*</.test(uri)) {
          // Ya es markup SVG en crudo.
          svgStr = uri;
        } else {
          const comma = uri.indexOf(',');
          if (comma < 0) return '';
          const meta = uri.slice(0, comma);
          const payload = uri.slice(comma + 1);
          if (/base64/i.test(meta)) {
            const bin = atob(payload);
            // base64 -> UTF-8 (preserva acentos en las etiquetas)
            svgStr = decodeURIComponent(Array.prototype.map.call(bin, ch => '%' + ('00' + ch.charCodeAt(0).toString(16)).slice(-2)).join(''));
          } else {
            svgStr = decodeURIComponent(payload);
          }
        }
        const doc = new DOMParser().parseFromString(svgStr, 'image/svg+xml');
        const svgEl = doc.documentElement;
        if (!svgEl || svgEl.nodeName.toLowerCase() !== 'svg') return '';
        BaseWidget.thumbNamespaceSvgIds(svgEl, suffix);
        svgEl.removeAttribute('class');
        // Guardar las dimensiones naturales ANTES de quitarlas (por si no hay viewBox).
        const ow = parseFloat(svgEl.getAttribute('width')) || 0;
        const oh = parseFloat(svgEl.getAttribute('height')) || 0;
        svgEl.removeAttribute('width');
        svgEl.removeAttribute('height');
        if (!svgEl.getAttribute('viewBox') && ow && oh) {
          svgEl.setAttribute('viewBox', `0 0 ${ow} ${oh}`);
        }
        return new XMLSerializer().serializeToString(svgEl);
      } catch (e) {
        return '';
      }
    }

    // Reserva: clona el <svg> real montado en el DOM (puede perder el CSS que ApexCharts
    // inyecta en <head>).
    static thumbCloneLiveApexSvg(apexEl, suffix) {
      try {
        if (!apexEl) return '';
        const rect = apexEl.getBoundingClientRect();
        const clone = apexEl.cloneNode(true);
        BaseWidget.thumbNamespaceSvgIds(clone, suffix);
        clone.removeAttribute('class');
        clone.removeAttribute('width');
        clone.removeAttribute('height');
        if (!clone.getAttribute('viewBox')) {
          clone.setAttribute('viewBox', `0 0 ${Math.round(rect.width) || 100} ${Math.round(rect.height) || 100}`);
        }
        return new XMLSerializer().serializeToString(clone);
      } catch (e) {
        return '';
      }
    }

    // Miniatura de un gráfico: incrusta su SVG real de ApexCharts escalado a su caja
    // (ctx.content). Devuelve '' si no hay gráfico o no se puede exportar.
    async apexThumbnail(ctx) {
      const box = ctx && ctx.content;
      if (!box || !this.el) return '';
      const apexEl = this.el.querySelector('svg.apexcharts-svg');
      const suffix = `w${this.id != null ? this.id : 'x'}`;
      let markup = await BaseWidget.thumbApexSvgMarkup(this, suffix);
      if (!markup && apexEl) markup = BaseWidget.thumbCloneLiveApexSvg(apexEl, suffix);
      if (!markup) return '';
      return markup.replace(/^<svg/, `<svg x="${box.x}" y="${box.y}" width="${box.w}" height="${box.h}" preserveAspectRatio="xMidYMid meet"`);
    }

    static dragHandleHTML() {
      return `<div class="drag-handle cursor-move text-fg-subtle hover:text-fg-muted absolute left-1 top-0 bottom-0 flex items-center opacity-0 group-hover:opacity-100 transition-opacity z-20 text-base leading-none">⣿</div>`;
    }

    static actionButtonsHTML() {
      return `<div class="absolute -top-2.5 -right-2.5 flex items-center space-x-0.5 opacity-0 group-hover:opacity-100 transition-opacity bg-surface border border-line rounded-md shadow-sm px-1 py-0.5 z-30">
        <button class="edit-widget-btn text-fg-muted hover:text-accent-text transition cursor-pointer p-0.5">
          <svg viewBox="0 0 14 14" class="w-3 h-3" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
            <path d="M10.5 1.5a1.41 1.41 0 0 1 2 2L4.5 11.5l-3 1 1-3Z"/>
          </svg>
        </button>
        <button class="delete-widget-btn text-fg-muted hover:text-danger transition cursor-pointer p-0.5">
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
      this.position = raw.position || { order: 0, w: 6, h: 300, row_id: 'row_1' };
      this.fields = raw.fields || BaseWidget.emptyFields();
      this.style = raw.style || {};
      this.data = raw.data || null;
      this._dirty = raw._dirty ?? false;
      this.el = null;
      this._chart = null;
      this._lastEntry = null;
      this._lastData = null;
      this._legendSortable = null;
      this._legendObserver = null;
      this._interactable = null;
      this._readOnly = false;
      this.order = (this.position && typeof this.position.order === 'number') ? this.position.order : 0;
      this.syncLayoutFromPosition();
    }

    // El ancho y el alto de la tarjeta salen de `position` (lo único que se guarda).
    syncLayoutFromPosition() {
      const pos = this.position || { order: 0, w: 6, h: 300 };
      this.width = `md:col-span-${Math.min(12, Math.max(2, pos.w || 6))}`;
      this.height = pos.h || 300;
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
        position: { order: pos.order ?? 0, w: pos.w || 6, h: pos.h || 300, ...(pos.row_id ? { row_id: pos.row_id } : {}) },
        fields: w.fields || BaseWidget.emptyFields(),
        style: w.style || {},
        data: w.data || null,
      });
    }

    get hasSpec() {
      return !!this.fields && ((this.fields.dimensions || []).length
        || (this.fields.metrics || []).length
        || (this.fields.columns || []).length) > 0;
    }

    buildElement() {
      throw new Error(`${this.constructor.name}: buildElement() no implementado`);
    }

    draw(data, style, title) {
      throw new Error(`${this.constructor.name}: draw() no implementado`);
    }

    buildStandardCardElement() {
      const el = document.createElement('div');
      el.className = `tb-widget col-span-12 ${this.width} p-4 flex flex-col justify-between relative group`;
      el.style.height = this.height + 'px';
      el.style.setProperty('--ghost-span', this._ghostSpanFromWidth());
      el.dataset.widgetId = this.id;
      el.dataset.type = this.chart_type;
      el.innerHTML = `
        ${BaseWidget.dragHandleHTML()}
        <div class="flex items-center pb-2 mb-2 select-none gap-1 min-w-0">
          <span class="title-display tb-widget-title truncate">${BaseWidget.escapeHTML(this.title)}</span>
          ${this.aiInsightButtonHTML()}
        </div>
        <div id="chart-${this.id}" class="flex-1 w-full min-h-0"></div>
        ${BaseWidget.actionButtonsHTML()}
        <div class="resize-handle absolute bottom-1 right-1 w-4 h-4 cursor-se-resize z-10 opacity-60 hover:opacity-100 transition">
          <svg viewBox="0 0 10 10" class="w-full h-full text-fg-subtle" fill="none">
            <line x1="2" y1="8" x2="8" y2="2" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
            <line x1="4" y1="8" x2="8" y2="4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
          </svg>
        </div>`;
      return el;
    }

    mount() {
      this.el = this.buildElement();
      this._attachCommonEvents();
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
      el.className = `tb-widget col-span-12 ${this.width} p-4 flex flex-col justify-between relative`;
      el.style.height = this.height + 'px';
      el.dataset.widgetId = this.id;
      el.dataset.type = this.chart_type;
      const titleHTML = this.title
        ? `<div class="flex items-center pb-2 mb-2 select-none gap-1 min-w-0"><span class="title-display tb-widget-title truncate">${BaseWidget.escapeHTML(this.title)}</span>${this.aiInsightButtonHTML()}</div>`
        : (this.chart_type !== 'filter' ? `<div class="flex pb-2 mb-2">${this.aiInsightButtonHTML()}</div>` : '');
      el.innerHTML = `${titleHTML}<div id="chart-${this.id}" class="flex-1 w-full min-h-0"></div>
        <div class="actions-slot absolute top-2 right-2 z-30 flex items-center gap-1"></div>`;
      return el;
    }

    mountReadOnly() {
      this._readOnly = true;
      this.el = this.buildReadOnlyElement();
      this._attachInsightEvent();
      return this.el;
    }

    getContentContainer() {
      return this.el ? this.el.querySelector(`#chart-${this.id}`) : null;
    }

    updateChrome() {
      if (!this.el) return;
      this.syncLayoutFromPosition();
      const titleEl = this.el.querySelector('.title-display');
      if (titleEl) titleEl.textContent = this.title;

      Array.from(this.el.classList).filter(c => c.startsWith('col-start-') || c.startsWith('md:col-start-') || c.startsWith('md:col-span-')).forEach(c => this.el.classList.remove(c));
      this.el.classList.add(this.width);
      this.el.style.height = this.height + 'px';
      this.el.style.setProperty('--ghost-span', this._ghostSpanFromWidth());

      window.dispatchEvent(new Event('resize'));
    }

    getProperties() {
      return { ...this.style };
    }

    getPosition() {
      const pos = this.position || { order: 0, w: 6, h: 300, row_id: 'row_1' };
      return {
        order: pos.order ?? 0,
        w: pos.w,
        h: pos.h,
        row_id: pos.row_id || 'row_1',
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

    // Mezcla profunda de opciones de ApexCharts: los objetos se combinan, el resto (arrays,
    // funciones, valores) lo pisa `extra`.
    static mergeOptions(base, extra) {
      const isPlain = (v) => v && typeof v === 'object' && !Array.isArray(v);
      const out = { ...base };
      Object.entries(extra || {}).forEach(([key, value]) => {
        out[key] = isPlain(value) && isPlain(out[key]) ? BaseWidget.mergeOptions(out[key], value) : value;
      });
      return out;
    }

    // Todo gráfico del lienzo parte de las opciones del tema (ChartTheme): colores, ejes,
    // cuadrícula y leyenda; el widget solo declara lo suyo.
    renderApexChart(container, options) {
      if (this._chart) {
        this._chart.destroy();
        this._chart = null;
      }
      // Un ancho fijo (ej. «Forzar ancho de gráfico») mayor que la tarjeta se recorre con
      // scroll horizontal dentro del contenedor, sin desbordar la tarjeta.
      const fixedWidth = Number(options?.chart?.width) || 0;
      container.style.overflowX = fixedWidth ? 'auto' : '';
      container.style.overflowY = fixedWidth ? 'hidden' : '';
      container.innerHTML = `<div style="height:100%;width:${fixedWidth ? `${fixedWidth}px` : '100%'};"></div>`;
      const mountEl = container.firstElementChild;
      this._chart = new ApexCharts(mountEl, BaseWidget.mergeOptions(this.themeOptions(), options));
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
          <span class="tb-text-bad text-xs">${BaseWidget.escapeHTML(message || 'Error al cargar los datos')}</span>
          ${retryable ? `<button type="button" class="retry-widget-btn tb-retry text-xs font-medium px-3 py-1 transition cursor-pointer">Reintentar</button>` : ''}
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
          <i class="ti ti-sparkles text-lg tb-link" aria-hidden="true"></i>
          <span class="text-xs tb-text-muted">Abre el panel de edición para elegir columnas y métricas, o descríbelo con IA.</span>
        </div>`;
    }

    showEmptyState() {
      const container = this.getContentContainer();
      if (!container) return;
      container.innerHTML = '<div class="tb-text-muted flex items-center justify-center h-full text-sm">Sin datos para mostrar</div>';
    }

    applyRender(entry) {
      if (!entry) return;
      if (entry.position_only) {
        window.dispatchEvent(new Event('resize'));
        return;
      }
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

    aiInsightButtonHTML() {
      if (this.chart_type === 'filter' || !this.id || this.id <= 0) return '';
      return `<button type="button" class="widget-ai-insight-btn p-0.5 rounded-md text-fg-subtle hover:text-accent-text hover:bg-accent-soft transition cursor-pointer shrink-0 inline-flex items-center justify-center" title="Ver análisis con IA" aria-label="Ver análisis con IA">
        <i class="ti ti-bulb text-lg" aria-hidden="true"></i>
      </button>`;
    }

    _attachInsightEvent() {
      if (!this.el) return;
      const btn = this.el.querySelector('.widget-ai-insight-btn');
      if (btn) {
        btn.addEventListener('click', (e) => {
          e.stopPropagation();
          e.preventDefault();
          window.dispatchEvent(new CustomEvent('widget:ai-insight', {
            detail: { id: this.id, title: this.title || this.constructor.label || 'Widget', type: this.chart_type }
          }));
        });
      }
    }

    _attachCommonEvents() {
      const el = this.el;
      if (!el) return;
      this._attachInsightEvent();
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
          start: () => {
            document.body.style.userSelect = 'none';
            window.dispatchEvent(new CustomEvent('board:ruler:highlight', {
              detail: { isResizing: true, element: el }
            }));
          },
          move: (event) => {
            moved = true;
            this._onResizeMove(event);
            const target = this.el || event.target;
            window.dispatchEvent(new CustomEvent('board:ruler:highlight', {
              detail: { isResizing: true, element: target, cols: this.position?.w }
            }));
          },
          end: (event) => {
            document.body.style.userSelect = '';
            window.dispatchEvent(new CustomEvent('board:ruler:clear'));
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
      const pos = this.position || { order: 0, w: 6, h: 300, row_id: 'row_1' };
      this.position = pos;

      const step = 20;
      this.height = Math.max(
        this.constructor.minHeight,
        Math.min(3000, Math.round(event.rect.height / step) * step),
      );
      pos.h = this.height;

      const grid = this._singleColumnWidth();
      if (grid) {
        const cols = Math.max(2, Math.min(12,
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