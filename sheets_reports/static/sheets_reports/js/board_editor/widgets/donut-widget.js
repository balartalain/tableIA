(function () {
  class DonutWidget extends BaseWidget {
    static type = 'donut';
    static palette = {
      icon: 'ti-chart-donut',
      category: 'charts',
      label: 'Gráfico de Dona',
      description: 'Proporciones por categoría',
    };
    static defaults = { title: 'Gráfico de Dona', width: 'md:col-span-4', height: 300 };

    static mockData() {
      return { series: [44, 55, 13, 33], labels: ['Norte', 'Sur', 'Este', 'Oeste'] };
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

    draw(data, style, title) {
      this.style = { ...this.style, ...style };
      if (title) this.title = title;

      const payload = data || this.constructor.mockData();
      this._lastData = payload;

      let series = [];
      let labels = [];

      if (payload.columns && payload.rows) {
        const meta = payload.metadata || {};
        const dimensionField = meta.dimension || payload.columns[0];
        const metricField = meta.metrics?.[0] || payload.columns[1];

        labels = payload.rows.map(row => row[dimensionField]);
        series = payload.rows.map(row => row[metricField] ?? 0);
      } else if (payload.series) {
        series = (payload.series || []).map(v => v ?? 0);
        labels = payload.labels || [];
      }

      const labelMode = this.style.labelMode === 'value' ? 'value' : 'percent';
      const holeSize = Math.min(80, Math.max(30, Number(this.style.donutSize) || 50));

      const options = {
        chart: { type: 'donut', height: '100%', width: '100%', fontFamily: 'inherit', toolbar: this.chartExportToolbar() },
        plotOptions: {
          pie: {
            donut: {
              size: `${holeSize}%`,
              labels: {
                show: true,
                value: {
                  show: true,
                  fontSize: '18px',
                  fontFamily: 'Inter, sans-serif',
                  fontWeight: 'bold',
                  color: '#111111',
                  offsetY: 8
                },
                total: {
                  show: true,
                  showAlways: true,
                  label: 'Total',
                  fontSize: '16px',
                  fontFamily: 'Inter, sans-serif',
                  color: '#888888',
                  formatter: function (w) {
                    return w.globals.seriesTotals.reduce((a, b) => a + b, 0);
                  }
                }
              }
            }
          }
        },
        colors: BaseWidget.CHART_COLORS,
        series,
        labels,
        legend: { show: this.style.showLegend !== false, position: 'bottom', fontSize: '10px' },
        dataLabels: {
          enabled: true,
          style: {
            fontSize: '10px',
            fontFamily: 'Roboto, sans-serif',
            fontWeight: '400',
            colors: ['#000000'],
          },
          dropShadow: { enabled: false },
          formatter: (val, opts) => (labelMode === 'value'
            ? Number(opts.w.globals.series[opts.seriesIndex]).toLocaleString(undefined, { maximumFractionDigits: 2 })
            : `${Number(val).toFixed(1)}%`),
        }
      };
      this.renderApexChart(this.getContentContainer(), options);
    }
  }

  WidgetRegistry.register(DonutWidget);
})();