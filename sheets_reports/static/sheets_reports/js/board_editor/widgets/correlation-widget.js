(function () {
  // La fuerza de la relación según |r| (escala de Evans): la misma para el color de las celdas,
  // el tooltip, «Lo más relacionado» y la leyenda del panel (correlationScale). `min`: desde qué
  // |r| empieza el nivel. «Muy débil» es el neutro; cada uno de los demás, un tono por signo.
  const SCALE = [
    { min: 0, label: 'Muy débil' },
    { min: 0.2, label: 'Débil' },
    { min: 0.4, label: 'Moderada' },
    { min: 0.6, label: 'Fuerte' },
    { min: 0.8, label: 'Muy fuerte' },
  ];
  const CUTS = SCALE.slice(1).map(level => level.min);

  // Los niveles con sus colores del tablero y el del texto escrito encima.
  function correlationScale(el) {
    const d = ChartTheme.read(el).diverging;
    return SCALE.map((level, i) => (i === 0
      ? { ...level, negative: d.zero, positive: d.zero, negativeOn: d.zeroOn, positiveOn: d.zeroOn }
      : { ...level, negative: d.neg[i - 1], positive: d.pos[i - 1], negativeOn: d.negOn[i - 1], positiveOn: d.posOn[i - 1] }));
  }
  window.correlationScale = correlationScale;

  // Nivel de un coeficiente, con los cortes de SCALE.
  function levelOf(r) {
    return [...SCALE].reverse().find(l => Math.abs(r) >= l.min) || SCALE[0];
  }

  // Nombre de cada tramo de divergingScale (de −1 a +1), para la leyenda del gráfico.
  const RANGE_NAMES = [
    ...SCALE.slice(1).reverse().map(level => `${level.label} (−)`),
    SCALE[0].label,
    ...SCALE.slice(1).map(level => `${level.label} (+)`),
  ];
  // La medida de cada par (`measures`): el método elegido, o Spearman con una ordinal.
  const MEASURE_LABELS = { pearson: 'Pearson', spearman: 'Spearman' };

  // La fuerza de la relación en palabras, con los cortes de SCALE.
  function strength(r) {
    const level = levelOf(r);
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
      const d = ctx.theme.diverging;
      return BaseWidget.thumbGrid(ctx, [d.pos[3], d.pos[1], d.zero, d.neg[1], d.neg[3], d.pos[2], d.zero]);
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
        container.innerHTML = '<p class="m-auto text-xs tb-text-muted">Elige al menos dos variables.</p>';
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

      const theme = this.themeColors();
      const levels = correlationScale(this.el);
      const colorRanges = ChartTheme.divergingScale(this.el, CUTS).ranges
        .map((range, k) => ({ ...range, name: RANGE_NAMES[k] }));
      // El valor escrito en la celda, legible sobre el tono de su nivel.
      const labelColor = ({ seriesIndex, dataPointIndex, w }) => {
        const val = w.globals.series[seriesIndex][dataPointIndex];
        if (val == null || Math.abs(val) > 1) return theme.label;
        const level = levels[SCALE.indexOf(levelOf(val))];
        return val < 0 ? level.negativeOn : level.positiveOn;
      };

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
        stroke: { width: 1, colors: [theme.stroke] },
        legend: { show: true, position: 'right' },
        dataLabels: {
          enabled: showValues,
          style: { fontSize: '11px', fontWeight: 500, colors: [labelColor] },
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
              return `<div class="px-2.5 py-1.5 text-xs max-w-[280px] whitespace-normal break-words leading-snug"><div class="font-semibold tb-text mb-0.5">${pair}</div><div class="tb-text-muted">${body}</div></div>`;
            }
            return `<div class="px-2.5 py-1.5 text-xs max-w-[280px] whitespace-normal break-words leading-snug">
              <div class="font-semibold tb-text pb-1 mb-1 border-b tb-border">${pair}</div>
              <div class="flex items-center gap-2 my-0.5">
                <span class="font-bold text-sm tb-text">${formatNumber(r, { decimals: 2 })}</span>
                <span class="text-xs font-medium px-1.5 py-0.5 rounded tb-surface-muted border tb-border tb-text">${strength(r)}</span>
              </div>
              <div class="text-xs tb-text-muted">${MEASURE_LABELS[measure] || measure} · ${n} filas analizadas</div>
              ${dummies.map(text => `<div class="text-xs tb-text-muted">${text}</div>`).join('')}
              ${unmatched.map(text => `<div class="text-xs tb-status-warn">${text}</div>`).join('')}
            </div>`;
          },
        },
      };
      this._chart = new ApexCharts(container.querySelector('.correlation-chart'), BaseWidget.mergeOptions(this.themeOptions(), options));
      this._chart.render();
    }
  }

  WidgetRegistry.register(CorrelationWidget);
})();
