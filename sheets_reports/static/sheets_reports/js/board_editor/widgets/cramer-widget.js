(function () {
  // La fuerza de la asociación según V de Cramér (de 0 a 1):
  // La misma para el color de las celdas, el tooltip, «Lo más asociado» y la leyenda del panel (window.CRAMER_SCALE).
  const SCALE = [
    { min: 0, max: 0.1, label: 'Muy débil', color: '#f4f3ef' },
    { min: 0.1, max: 0.3, label: 'Débil', color: '#cfe6d6' },
    { min: 0.3, max: 0.5, label: 'Moderada', color: '#9ec7aa' },
    { min: 0.5, max: 0.8, label: 'Fuerte', color: '#4fa87d' },
    { min: 0.8, max: 1.0, label: 'Muy fuerte', color: '#1f8a5f' },
  ];
  window.CRAMER_SCALE = SCALE;

  const RANGES = SCALE.map(level => ({
    from: level.min,
    to: level.max,
    color: level.color,
    name: level.label,
  }));

  const HIDDEN = 2;
  const NO_DATA = 3;
  const SENTINEL_RANGES = [
    { from: 1.5, to: 2.5, color: '#ffffff', name: ' ' },
    { from: 2.5, to: 3.5, color: '#e4e2dc', name: 'Sin datos' },
  ];

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

    getProperties() {
      return { ...super.getProperties() };
    }

    _topHTML(top) {
      if (this.style.showTop === false || !(top || []).length) return '';
      const esc = BaseWidget.escapeHTML;
      const items = top.map(t => `<li class="truncate"><span class="font-medium text-ink/70">${esc(t.a)} y ${esc(t.b)}</span>:
          ${formatNumber(t.r, { decimals: 2 })} <span class="text-ink/45">(${strength(t.r)})</span></li>`).join('');
      return `<div class="shrink-0 pt-2 mt-1 border-t border-line text-[11px] text-ink/55">
          <p class="font-semibold text-ink/60 mb-0.5">Lo más asociado</p>
          <ul class="space-y-0.5">${items}</ul>
        </div>`;
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
      const lowerOnly = this.style.lowerOnly !== false;
      const showValues = this.style.showValues !== false;

      if (this._chart) {
        this._chart.destroy();
        this._chart = null;
      }
      const container = this.getContentContainer();
      container.className = 'flex flex-col h-full min-h-0';
      if (size < 2) {
        container.innerHTML = '<p class="m-auto text-xs text-ink/40">Elige al menos dos columnas categóricas.</p>';
        return;
      }

      container.style.height = '100%';
      container.innerHTML = `<div class="flex-1 min-h-0 relative" style="flex:1 1 0%;min-height:0;position:relative"><div class="cramer-chart absolute inset-0" style="position:absolute;inset:0"></div></div>${this._topHTML(payload.top)}`;

      const series = variables.map((_, k) => {
        const i = size - 1 - k;
        return {
          name: variables[i].label,
          data: variables.map((col, j) => {
            const r = matrix[i] ? matrix[i][j] : null;
            const y = lowerOnly && j > i ? HIDDEN : (r == null ? NO_DATA : r);
            return { x: col.label, y };
          }),
        };
      });

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
            colorScale: { ranges: [...RANGES, ...SENTINEL_RANGES] },
          },
        },
        stroke: { width: 1, colors: ['#ffffff'] },
        legend: { show: false },
        dataLabels: {
          enabled: showValues,
          style: { fontSize: '11px', fontWeight: 500, colors: ['#1f2328'] },
          formatter: (val) => (val > 1 ? '' : formatNumber(val, { decimals: 2 })),
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
            const body = r == null
              ? `Sin datos suficientes${n < 3 ? ` (${n} ${n === 1 ? 'fila' : 'filas'} con los dos valores)` : ' (columna con una sola categoría)'}`
              : `<b>V = ${formatNumber(r, { decimals: 2 })}</b> · ${strength(r)} · ${n} filas`;
            return `<div class="px-2.5 py-1.5 text-xs"><div class="font-semibold">${pair}</div><div>${body}</div></div>`;
          },
        },
      };
      this._chart = new ApexCharts(container.querySelector('.cramer-chart'), options);
      this._chart.render();
    }
  }

  WidgetRegistry.register(CramerWidget);
})();
