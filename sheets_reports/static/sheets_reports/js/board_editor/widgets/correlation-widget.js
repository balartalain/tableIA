(function () {
  // La fuerza de la relación según |r| (escala de Evans): la misma para el color de las
  // celdas, el tooltip, «Lo más relacionado» y la leyenda del panel (window.CORRELATION_SCALE).
  // `min`: desde qué |r| empieza el nivel. «Muy débil» es un solo tono, sin signo.
  const DEFAULT_DIVERGENT = BaseWidget.generateDivergentScale('#1F8A5F', '#DC2626', '#F4F3EF', 4);
  const SCALE = [
    { min: 0, label: 'Muy débil', negative: DEFAULT_DIVERGENT.neutral, positive: DEFAULT_DIVERGENT.neutral },
    { min: 0.2, label: 'Débil', negative: DEFAULT_DIVERGENT.negative[0], positive: DEFAULT_DIVERGENT.positive[0] },
    { min: 0.4, label: 'Moderada', negative: DEFAULT_DIVERGENT.negative[1], positive: DEFAULT_DIVERGENT.positive[1] },
    { min: 0.6, label: 'Fuerte', negative: DEFAULT_DIVERGENT.negative[2], positive: DEFAULT_DIVERGENT.positive[2] },
    { min: 0.8, label: 'Muy fuerte', negative: DEFAULT_DIVERGENT.negative[3], positive: DEFAULT_DIVERGENT.positive[3] },
  ];
  window.CORRELATION_SCALE = SCALE;

  // Tramos de color de -1 a 1 (9: cuatro por signo y «muy débil» en el centro).
  const RANGES = [
    ...SCALE.slice(1).reverse().map((level, k, list) => ({
      from: -(k === 0 ? 1 : list[k - 1].min), to: -level.min, color: level.negative, name: level.label,
    })),
    { from: -SCALE[1].min, to: SCALE[1].min, color: SCALE[0].positive, name: SCALE[0].label },
    ...SCALE.slice(1).map((level, k, list) => ({
      from: level.min, to: k === list.length - 1 ? 1 : list[k + 1].min, color: level.positive, name: level.label,
    })),
  ];
  // Celdas sin valor: centinelas fuera de [-1, 1] con su propio tramo (un null, ApexCharts lo
  // pintaría con el color del tramo más bajo). HIDDEN: arriba de la diagonal con «solo la
  // mitad inferior»; NO_DATA: el par no tiene filas suficientes o una columna es constante.
  const HIDDEN = 2;
  const NO_DATA = 3;
  const SENTINEL_RANGES = [
    { from: 1.5, to: 2.5, color: '#ffffff', name: ' ' },
    { from: 2.5, to: 3.5, color: '#e4e2dc', name: 'Sin datos' },
  ];
  // La medida de cada par (`measures`): el método elegido, o Spearman con una ordinal.
  const MEASURE_LABELS = { pearson: 'Pearson', spearman: 'Spearman' };

  // La fuerza de la relación en palabras, con los cortes de SCALE.
  function strength(r) {
    const level = [...SCALE].reverse().find(l => Math.abs(r) >= l.min) || SCALE[0];
    return `${level.label.toLowerCase()}, ${r >= 0 ? 'positiva' : 'negativa'}`;
  }

  class CorrelationWidget extends BaseWidget {
    static type = 'correlation';
    static palette = {
      icon: 'ti-chart-grid-dots',
      category: 'stats',
      label: 'Matriz de correlación',
      description: 'Qué tan relacionadas están varias columnas (números, escalas o categorías)',
    };
    static defaults = { title: 'Matriz de correlación', width: 'md:col-span-6', height: 380 };

    static mockData() {
      return {
        method: 'pearson',
        variables: [{ field: 'a', label: 'Ventas', scale: 'continuous' }, { field: 'b', label: 'Costo', scale: 'continuous' },
                    { field: 'c', label: 'Región: Norte', scale: 'nominal', value: 'Norte', group: 'c' }],
        matrix: [[1, 0.82, -0.35], [0.82, 1, -0.12], [-0.35, -0.12, 1]],
        measures: [['pearson', 'pearson', 'spearman'], ['pearson', 'pearson', 'spearman'], ['spearman', 'spearman', 'spearman']],
        n: [[120, 120, 120], [120, 120, 120], [120, 120, 120]],
        rows: 120,
        top: [{ a: 'Ventas', b: 'Costo', r: 0.82, n: 120, measure: 'pearson' }, { a: 'Ventas', b: 'Región: Norte', r: -0.35, n: 120, measure: 'spearman' }],
      };
    }

    constructor(raw) {
      super(raw);
    }

    buildElement() {
      return this.buildStandardCardElement();
    }

    // Miniatura: el SVG real del heatmap y, si no se puede, una rejilla divergente.
    async thumbnailContent(ctx) {
      const svg = await this.apexThumbnail(ctx);
      if (svg) return svg;
      return BaseWidget.thumbGrid(ctx, ['#f4f3ef', '#d3e4f2', '#acccea', '#7fb0dd', '#f9d8cf', '#f4b2a4', '#ec8a7c']);
    }

    getProperties() {
      return { ...super.getProperties() };
    }

    draw(data, style, title) {
      this.style = { ...this.style, ...style };
      if (title) this.title = title;

      const payload = data || this.constructor.mockData();
      this._lastData = payload;
      const variables = payload.variables || [];
      const matrix = payload.matrix || [];
      const counts = payload.n || [];
      const measures = payload.measures || [];
      const measureAt = (i, j) => (measures[i] ? measures[i][j] : null) || payload.method || 'pearson';
      const size = variables.length;
      const lowerOnly = this.style.lowerOnly === true;
      const showValues = this.style.showValues !== false;

      if (this._chart) {
        this._chart.destroy();
        this._chart = null;
      }
      const container = this.getContentContainer();
      container.className = 'flex flex-col h-full min-h-0';
      if (size < 2) {
        container.innerHTML = '<p class="m-auto text-xs text-ink/40">Elige al menos dos variables.</p>';
        return;
      }
      // ApexCharts mide el 100 % de alto contra el padre del elemento donde se monta: la caja
      // flex-1 (lo que deja el pie), no el contenedor entero de la tarjeta. Las medidas van en
      // línea: el CDN de Tailwind genera las clases nuevas tras un tick y en la vista compartida
      // (donde `h-full` no sale en el HTML) ApexCharts mediría 0 px de alto.
      container.style.height = '100%';
      container.innerHTML = `<div class="flex-1 min-h-0 relative" style="flex:1 1 0%;min-height:0;position:relative"><div class="correlation-chart absolute inset-0" style="position:absolute;inset:0"></div></div>`;

      // ApexCharts dibuja la primera serie abajo: en orden inverso, la primera variable queda arriba.
      const series = variables.map((_, k) => {
        const i = size - 1 - k;
        return {
          name: variables[i].label,
          data: variables.map((col, j) => {
            const r = matrix[i] ? matrix[i][j] : null;
            const y = (lowerOnly && j > i) || r == null ? null : r;
            return { x: col.label, y };
          }),
        };
      });

      const posColor = this.getChartColors()[0] || '#1F8A5F';
      const negColor = '#DC2626';
      const scaleData = BaseWidget.generateDivergentScale(posColor, negColor, '#F4F3EF', 4);
      const scaleLevels = [
        { min: 0, label: 'Muy débil', negative: scaleData.neutral, positive: scaleData.neutral },
        { min: 0.2, label: 'Débil', negative: scaleData.negative[0], positive: scaleData.positive[0] },
        { min: 0.4, label: 'Moderada', negative: scaleData.negative[1], positive: scaleData.positive[1] },
        { min: 0.6, label: 'Fuerte', negative: scaleData.negative[2], positive: scaleData.positive[2] },
        { min: 0.8, label: 'Muy fuerte', negative: scaleData.negative[3], positive: scaleData.positive[3] },
      ];
      window.CORRELATION_SCALE = scaleLevels;
      const colorRanges = [
        ...scaleLevels.slice(1).reverse().map((level, k, list) => ({
          from: -(k === 0 ? 1 : list[k - 1].min), to: -level.min, color: level.negative, name: level.label,
        })),
        { from: -scaleLevels[1].min, to: scaleLevels[1].min, color: scaleLevels[0].positive, name: scaleLevels[0].label },
        ...scaleLevels.slice(1).map((level, k, list) => ({
          from: level.min, to: k === list.length - 1 ? 1 : list[k + 1].min, color: level.positive, name: level.label,
        })),
      ];

      const options = {
        chart: { type: 'heatmap', height: '100%', width: '100%', fontFamily: 'inherit', toolbar: this.chartExportToolbar(),
                 animations: { enabled: false } },
        series,
        plotOptions: {
          heatmap: {
            enableShades: false,
            radius: 2,
            colorScale: {
              ranges: colorRanges,
              min: -1,
              max: 1,
              gradientLegend: {
                enabled: true,
                width: '75%',
                thickness: 14,
                showHoverValue: true,
              },
            },
          },
        },
        stroke: { width: 1, colors: ['#ffffff'] },
        legend: { show: true, position: 'right' },
        dataLabels: {
          enabled: showValues,
          style: { fontSize: '11px', fontWeight: 500, colors: ['#1f2328'] },
          formatter: (val) => (val == null || Math.abs(val) > 1 ? '' : formatNumber(val, { decimals: 2 })),
        },
        xaxis: { labels: { rotate: -45, trim: true, style: { fontSize: '10px' } }, tooltip: { enabled: false } },
        yaxis: { labels: { maxWidth: 140, style: { fontSize: '10px' } } },
        states: { hover: { filter: { type: 'none' } } },
        tooltip: {
          custom: ({ seriesIndex, dataPointIndex }) => {
            const i = size - 1 - seriesIndex;
            const j = dataPointIndex;
            if (lowerOnly && j > i) return '';
            const esc = BaseWidget.escapeHTML;
            const r = matrix[i] ? matrix[i][j] : null;
            const n = counts[i] ? counts[i][j] : 0;
            const pair = `${esc(variables[i].label)} × ${esc(variables[j].label)}`;
            const measure = measureAt(i, j);
            // Una dummy (categoría de una nominal) vale 1 si la fila es de esa categoría y 0 si no.
            const dummies = [variables[i], variables[j]].filter(v => v.group)
              .map(v => `${esc(v.label)}: 1 si es «${esc(v.value)}», 0 si no`);
            // Filas cuyo valor no está en el orden de una ordinal: no cuentan.
            const unmatched = [variables[i], variables[j]].filter(v => v.unmatched)
              .map(v => `${esc(v.label)}: ${v.unmatched} ${v.unmatched === 1 ? 'fila' : 'filas'} con valores fuera de su orden`);
            if (r == null) {
              // Dos categorías de la misma nominal: son excluyentes, no se comparan.
              const body = measure === 'same_variable'
                ? 'Categorías de la misma variable: no se comparan'
                : `Sin datos suficientes${n < 3 ? ` (${n} ${n === 1 ? 'fila' : 'filas'})` : ' (una de las columnas no varía)'}`;
              return `<div class="px-2.5 py-1.5 text-xs max-w-[280px] whitespace-normal break-words leading-snug"><div class="font-semibold text-ink/80 mb-0.5">${pair}</div><div class="text-ink/60">${body}</div></div>`;
            }
            return `<div class="px-2.5 py-1.5 text-xs max-w-[280px] whitespace-normal break-words leading-snug">
              <div class="font-semibold text-ink/80 pb-1 mb-1 border-b border-line/60">${pair}</div>
              <div class="flex items-center gap-2 my-0.5">
                <span class="font-bold text-sm text-ink">${formatNumber(r, { decimals: 2 })}</span>
                <span class="text-[11px] font-medium px-1.5 py-0.5 rounded bg-paper border border-line text-ink/80">${strength(r)}</span>
              </div>
              <div class="text-[11px] text-ink/50">${MEASURE_LABELS[measure] || measure} · ${n} filas analizadas</div>
              ${dummies.map(text => `<div class="text-[11px] text-ink/50">${text}</div>`).join('')}
              ${unmatched.map(text => `<div class="text-[11px] text-amber-700">${text}</div>`).join('')}
            </div>`;
          },
        },
      };
      this._chart = new ApexCharts(container.querySelector('.correlation-chart'), options);
      this._chart.render();
    }
  }

  WidgetRegistry.register(CorrelationWidget);
})();
