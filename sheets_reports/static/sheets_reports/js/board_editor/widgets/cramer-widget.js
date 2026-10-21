(function () {
  // La fuerza de la asociación según V de Cramér (de 0 a 1): la misma para el color de las
  // celdas, el tooltip, «Lo más asociado» y la leyenda del panel (cramerScale). Cada nivel
  // toma un tono de la escala secuencial del tablero (--tb-seq-1…5), del más claro al más oscuro.
  const SCALE = [
    { min: 0, max: 0.1, label: 'Muy débil' },
    { min: 0.1, max: 0.3, label: 'Débil' },
    { min: 0.3, max: 0.5, label: 'Moderada' },
    { min: 0.5, max: 0.8, label: 'Fuerte' },
    { min: 0.8, max: 1.0, label: 'Muy fuerte' },
  ];

  // Los niveles con su color y el del texto escrito encima.
  function cramerScale(el) {
    const t = ChartTheme.read(el);
    return SCALE.map((level, i) => ({ ...level, color: t.sequential[i], on: t.onSequential[i] }));
  }
  window.cramerScale = cramerScale;

  function strength(v) {
    const level = [...SCALE].reverse().find(l => v >= l.min) || SCALE[0];
    return `asociación ${level.label.toLowerCase()}`;
  }

  class CramerWidget extends BaseWidget {
    static type = 'cramer';
    static palette = {
      icon: 'ti-grid-pattern',
      category: 'stats',
      label: 'V de Cramér',
      description: 'Asociación entre variables categóricas',
    };
    static defaults = { title: 'V de Cramér', width: 'md:col-span-6', height: 380 };

    static mockData() {
      return {
        variables: [{ field: 'a', label: 'Categoría' }, { field: 'b', label: 'Región' }, { field: 'c', label: 'Canal' }],
        matrix: [[1, 0.45, 0.18], [0.45, 1, 0.62], [0.18, 0.62, 1]],
        n: [[120, 120, 120], [120, 120, 120], [120, 120, 120]],
        rows: 120,
        top: [{ a: 'Región', b: 'Canal', r: 0.62, n: 120 }, { a: 'Categoría', b: 'Región', r: 0.45, n: 120 }],
      };
    }

    constructor(raw) {
      super(raw);
    }

    buildElement() {
      return this.buildStandardCardElement();
    }

    // Miniatura: el SVG real del heatmap y, si no se puede, una rejilla con la escala del tablero.
    async thumbnailContent(ctx) {
      const svg = await this.apexThumbnail(ctx);
      if (svg) return svg;
      return BaseWidget.thumbGrid(ctx, ctx.theme.sequential);
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
        container.innerHTML = '<p class="m-auto text-xs tb-text-muted">Elige al menos dos columnas categóricas.</p>';
        return;
      }

      container.style.height = '100%';
      container.innerHTML = `<div class="flex-1 min-h-0 relative" style="flex:1 1 0%;min-height:0;position:relative"><div class="cramer-chart absolute inset-0" style="position:absolute;inset:0"></div></div>`;

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
      const scaleLevels = cramerScale(this.el);
      const colorRanges = scaleLevels.map(level => ({
        from: level.min, to: level.max, color: level.color, name: level.label,
      }));
      // El valor escrito en la celda, legible sobre el tono de su nivel.
      const labelColor = ({ seriesIndex, dataPointIndex, w }) => {
        const val = w.globals.series[seriesIndex][dataPointIndex];
        if (val == null || val > 1) return theme.label;
        const level = [...scaleLevels].reverse().find(l => val >= l.min) || scaleLevels[0];
        return level.on;
      };

      const options = {
        chart: {
          type: 'heatmap',
          height: '100%',
          width: '100%',
          fontFamily: 'inherit',
          toolbar: this.chartExportToolbar(),
          animations: { enabled: false },
        },
        series,
        plotOptions: {
          heatmap: {
            enableShades: false,
            radius: 2,
            colorScale: {
              ranges: colorRanges,
              min: 0,
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
          formatter: (val) => (val == null || val > 1 ? '' : formatNumber(val, { decimals: 2 })),
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
            if (r == null) {
              const body = `Sin datos suficientes${n < 3 ? ` (${n} ${n === 1 ? 'fila' : 'filas'})` : ' (columna con una sola categoría)'}`;
              return `<div class="px-2.5 py-1.5 text-xs max-w-[280px] whitespace-normal break-words leading-snug"><div class="font-semibold tb-text mb-0.5">${pair}</div><div class="tb-text-muted">${body}</div></div>`;
            }
            return `<div class="px-2.5 py-1.5 text-xs max-w-[280px] whitespace-normal break-words leading-snug">
              <div class="font-semibold tb-text pb-1 mb-1 border-b tb-border">${pair}</div>
              <div class="flex items-center gap-2 my-0.5">
                <span class="font-bold text-sm tb-text">V = ${formatNumber(r, { decimals: 2 })}</span>
                <span class="text-xs font-medium px-1.5 py-0.5 rounded tb-surface-muted border tb-border tb-text">${strength(r)}</span>
              </div>
              <div class="text-xs tb-text-muted">${n} filas analizadas</div>
            </div>`;
          },
        },
      };
      this._chart = new ApexCharts(container.querySelector('.cramer-chart'), BaseWidget.mergeOptions(this.themeOptions(), options));
      this._chart.render();
    }
  }

  WidgetRegistry.register(CramerWidget);
})();
