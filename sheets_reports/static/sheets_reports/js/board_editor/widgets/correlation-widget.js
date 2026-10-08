(function () {
  // La fuerza de la relación según |r| (escala de Evans): la misma para el color de las
  // celdas, el tooltip, «Lo más relacionado» y la leyenda del panel (window.CORRELATION_SCALE).
  // `min`: desde qué |r| empieza el nivel. «Muy débil» es un solo tono, sin signo.
  const SCALE = [
    { min: 0, label: 'Muy débil', negative: '#f4f3ef', positive: '#f4f3ef' },
    { min: 0.2, label: 'Débil', negative: '#f9d8cf', positive: '#d3e4f2' },
    { min: 0.4, label: 'Moderada', negative: '#f4b2a4', positive: '#acccea' },
    { min: 0.6, label: 'Fuerte', negative: '#ec8a7c', positive: '#7fb0dd' },
    { min: 0.8, label: 'Muy fuerte', negative: '#de5b55', positive: '#4f95cf' },
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

  // La fuerza de la relación en palabras, con los cortes de SCALE.
  function strength(r) {
    const level = [...SCALE].reverse().find(l => Math.abs(r) >= l.min) || SCALE[0];
    return `${level.label.toLowerCase()}, ${r >= 0 ? 'positiva' : 'negativa'}`;
  }

  class CorrelationWidget extends BaseWidget {
    static type = 'correlation';
    static palette = {
      icon: 'ti-chart-grid-dots',
      category: 'charts',
      label: 'Matriz de correlación',
      description: 'Qué tan relacionadas están varias columnas numéricas',
    };
    static defaults = { title: 'Matriz de correlación', width: 'md:col-span-6', height: 380 };

    static mockData() {
      return {
        method: 'pearson',
        variables: [{ field: 'a', label: 'Ventas' }, { field: 'b', label: 'Costo' }, { field: 'c', label: 'Descuento' }],
        matrix: [[1, 0.82, -0.35], [0.82, 1, -0.12], [-0.35, -0.12, 1]],
        n: [[120, 120, 120], [120, 120, 120], [120, 120, 120]],
        rows: 120,
        top: [{ a: 'Ventas', b: 'Costo', r: 0.82, n: 120 }, { a: 'Ventas', b: 'Descuento', r: -0.35, n: 120 }],
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
          <p class="font-semibold text-ink/60 mb-0.5">Lo más relacionado</p>
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
      const lowerOnly = !!this.style.lowerOnly;
      const showValues = this.style.showValues !== false;

      if (this._chart) {
        this._chart.destroy();
        this._chart = null;
      }
      const container = this.getContentContainer();
      container.className = 'flex flex-col h-full min-h-0';
      if (size < 2) {
        container.innerHTML = '<p class="m-auto text-xs text-ink/40">Elige al menos dos columnas numéricas.</p>';
        return;
      }
      // ApexCharts mide el 100 % de alto contra el padre del elemento donde se monta: la caja
      // flex-1 (lo que deja el pie), no el contenedor entero de la tarjeta.
      container.innerHTML = `<div class="flex-1 min-h-0 relative"><div class="correlation-chart absolute inset-0"></div></div>${this._topHTML(payload.top)}`;

      // ApexCharts dibuja la primera serie abajo: en orden inverso, la primera variable queda arriba.
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
        chart: { type: 'heatmap', height: '100%', width: '100%', fontFamily: 'inherit', toolbar: this.chartExportToolbar(),
                 animations: { enabled: false } },
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
              ? `Sin datos suficientes${n < 3 ? ` (${n} ${n === 1 ? 'fila' : 'filas'} con los dos valores)` : ' (una de las columnas no varía)'}`
              : `<b>${formatNumber(r, { decimals: 2 })}</b> · ${strength(r)} · ${n} filas`;
            return `<div class="px-2.5 py-1.5 text-xs"><div class="font-semibold">${pair}</div><div>${body}</div></div>`;
          },
        },
      };
      this._chart = new ApexCharts(container.querySelector('.correlation-chart'), options);
      this._chart.render();
    }
  }

  WidgetRegistry.register(CorrelationWidget);
})();
