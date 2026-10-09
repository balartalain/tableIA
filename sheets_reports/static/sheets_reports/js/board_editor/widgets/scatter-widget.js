(function () {
  const COLORS = BaseWidget.CHART_COLORS;
  // Sin color por categoría: puntos en el primer color y la tendencia en el de las referencias.
  const SINGLE_TREND_COLOR = BaseWidget.REFERENCE_COLOR;
  const trendName = (name) => `${name || 'Filas'} · tendencia`;

  class ScatterWidget extends BaseWidget {
    static type = 'scatter';
    static palette = {
      icon: 'ti-chart-dots',
      category: 'charts',
      label: 'Gráfico de dispersión',
      description: 'Relación entre dos columnas numéricas, fila a fila',
    };
    static defaults = { title: 'Gráfico de dispersión', width: 'md:col-span-6', height: 340 };

    static mockData() {
      const points = [[1, 18], [2, 22], [3, 21], [4, 29], [5, 31], [6, 30], [7, 38], [8, 41], [9, 39], [10, 47]];
      const trend = { slope: 3.1, intercept: 15.2, r: 0.98, from: 1, to: 10 };
      return {
        x: { field: 'x', label: 'Años de experiencia' },
        y: { field: 'y', label: 'Salario' },
        color: null,
        groups: [{ name: '', points, rows: points.length, trend }],
        rows: points.length,
        shown: points.length,
        trend,
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

    // Pie: cuántas filas se dibujan (con muestra, «2.000 de 15.000») y la r de la tendencia global.
    _footerHTML(payload, showTrend) {
      const parts = [];
      if (payload.shown < payload.rows) {
        parts.push(`Muestra de ${formatNumber(payload.shown)} de ${formatNumber(payload.rows)} filas`);
      } else {
        parts.push(`${formatNumber(payload.rows)} ${payload.rows === 1 ? 'fila' : 'filas'}`);
      }
      if (showTrend && payload.trend && payload.trend.r != null) {
        parts.push(`r = ${formatNumber(payload.trend.r, { decimals: 2 })}${payload.color ? ' (todas)' : ''}`);
      }
      return `<div class="shrink-0 pt-1 text-[11px] text-ink/50 tabular-nums">${parts.join(' · ')}</div>`;
    }

    draw(data, style, title) {
      this.style = { ...this.style, ...style };
      if (title) this.title = title;

      const payload = data || this.constructor.mockData();
      this._lastData = payload;
      const showTrend = this.style.showTrend !== false;

      if (this._chart) {
        this._chart.destroy();
        this._chart = null;
      }
      const container = this.getContentContainer();
      container.className = 'flex flex-col h-full min-h-0';
      if (!payload.x || !payload.y) {
        container.innerHTML = '<p class="m-auto text-xs text-ink/40">Elige dos columnas numéricas: la del eje X y la del eje Y.</p>';
        return;
      }
      const groups = payload.groups || [];
      if (!groups.length) {
        container.innerHTML = '<p class="m-auto text-xs text-ink/40">Ninguna fila tiene valor en las dos columnas.</p>';
        return;
      }
      // ApexCharts mide el 100 % de alto contra el padre del elemento donde se monta: la caja
      // flex-1 (lo que deja el pie), no el contenedor entero de la tarjeta. Las medidas van en
      // línea: el CDN de Tailwind genera las clases nuevas tras un tick y en la vista compartida
      // (donde `h-full` no sale en el HTML) ApexCharts mediría 0 px de alto.
      container.style.height = '100%';
      container.innerHTML = `<div class="flex-1 min-h-0 relative" style="flex:1 1 0%;min-height:0;position:relative"><div class="scatter-chart absolute inset-0" style="position:absolute;inset:0"></div></div>${this._footerHTML(payload, showTrend)}`;

      // Primero una serie de puntos por grupo y después, si hay, la recta de cada uno (mismo
      // color que sus puntos). Así el índice de la serie es el del grupo en tooltip y leyenda.
      const colored = !!payload.color;
      const pointColors = groups.map((_, i) => COLORS[i % COLORS.length]);
      const series = groups.map(g => ({ name: g.name || 'Filas', type: 'scatter', data: g.points }));
      const colors = [...pointColors];
      groups.forEach((g, i) => {
        const t = showTrend && g.trend;
        if (!t) return;
        const at = (x) => t.slope * x + t.intercept;
        series.push({ name: trendName(g.name), type: 'line', data: [[t.from, at(t.from)], [t.to, at(t.to)]] });
        colors.push(colored ? pointColors[i] : SINGLE_TREND_COLOR);
      });
      const lines = series.length - groups.length;
      const esc = BaseWidget.escapeHTML;
      const size = Number(this.style.markerSize) || 5;

      // Sin animación: ApexCharts anima el trazo de la recta y, discontinua, la deja a medias.
      const options = {
        chart: {
          type: 'line', height: '100%', width: '100%', fontFamily: 'inherit', toolbar: this.chartExportToolbar(),
          zoom: { enabled: false }, animations: { enabled: false },
        },
        series,
        colors,
        markers: {
          size: [...groups.map(() => size), ...Array(lines).fill(0)],
          strokeWidth: 0, fillOpacity: 0.7, hover: { sizeOffset: 2 },
        },
        stroke: {
          width: [...groups.map(() => 0), ...Array(lines).fill(2)],
          dashArray: [...groups.map(() => 0), ...Array(lines).fill(5)],
          curve: 'straight',
        },
        legend: {
          show: colored,
          position: 'top',
          horizontalAlign: 'left',
          fontSize: '11px',
          customLegendItems: groups.map(g => g.name),
          markers: { fillColors: pointColors },
          onItemClick: { toggleDataSeries: false },
        },
        dataLabels: { enabled: false },
        grid: { show: this.style.showGrid !== false },
        xaxis: {
          type: 'numeric',
          tickAmount: 6,
          title: { text: payload.x.label, style: { fontSize: '11px', fontWeight: 500 } },
          labels: { formatter: BaseWidget.axisNumber, style: { fontSize: '10px' } },
          tooltip: { enabled: false },
        },
        yaxis: {
          title: { text: payload.y.label, style: { fontSize: '11px', fontWeight: 500 } },
          labels: { formatter: BaseWidget.axisNumber, style: { fontSize: '10px' } },
        },
        tooltip: {
          shared: false,
          intersect: true,
          custom: ({ seriesIndex, dataPointIndex }) => {
            const group = groups[seriesIndex];
            if (!group) return '';
            const [x, y] = group.points[dataPointIndex];
            const r = showTrend && group.trend && group.trend.r != null && colored
              ? ` <span class="text-ink/50">(r = ${formatNumber(group.trend.r, { decimals: 2 })})</span>` : '';
            const head = colored
              ? `<div class="font-semibold flex items-center gap-1.5"><span class="inline-block h-2 w-2 rounded-full" style="background:${pointColors[seriesIndex]}"></span>${esc(group.name)}${r}</div>`
              : '';
            return `<div class="px-2.5 py-1.5 text-xs tabular-nums">${head}
                <div><span class="text-ink/60">${esc(payload.x.label)}:</span> <b>${formatNumber(x)}</b></div>
                <div><span class="text-ink/60">${esc(payload.y.label)}:</span> <b>${formatNumber(y)}</b></div>
              </div>`;
          },
        },
      };
      const chart = new ApexCharts(container.querySelector('.scatter-chart'), options);
      this._chart = chart;
      chart.render().then(() => {
        if (colored) this._wireLegend(container, chart, groups, showTrend);
      });
    }

    // La leyenda lista solo los grupos (`customLegendItems`) y con ella ApexCharts no avisa del
    // clic: al pulsar un grupo se ocultan o muestran sus puntos y su recta. El listener va en el
    // contenedor porque ApexCharts rehace la leyenda en cada toggleSeries.
    _wireLegend(container, chart, groups, showTrend) {
      if (this._legendClick) container.removeEventListener('click', this._legendClick);
      this._legendClick = (event) => {
        const item = event.target.closest('.apexcharts-legend-series');
        if (!item || this._chart !== chart) return;
        const index = [...item.parentElement.querySelectorAll('.apexcharts-legend-series')].indexOf(item);
        const group = groups[index];
        if (!group) return;
        chart.toggleSeries(group.name);
        if (showTrend && group.trend) chart.toggleSeries(trendName(group.name));
      };
      container.addEventListener('click', this._legendClick);
    }
  }

  WidgetRegistry.register(ScatterWidget);
})();
