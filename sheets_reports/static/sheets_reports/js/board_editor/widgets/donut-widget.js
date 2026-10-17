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

    // Miniatura: el SVG real del gráfico y, si no se puede, una dona esquemática.
    async thumbnailContent(ctx) {
      const svg = await this.apexThumbnail(ctx);
      if (svg) return svg;
      const { x, y, w, h, colors } = ctx;
      const cx = x + w / 2;
      const cy = y + h / 2 + 4;
      const r = Math.max(6, Math.min(16, h / 2 - 8));
      const sw = Math.max(3, r * 0.45);
      return `
        <circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="${colors[0]}" stroke-width="${sw}" stroke-dasharray="45 25"/>
        <circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="${colors[1] || colors[0]}" stroke-width="${sw}" stroke-dasharray="25 45" stroke-dashoffset="-45"/>
        <circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="${colors[2] || colors[0]}" stroke-width="${sw}" stroke-dasharray="15 55" stroke-dashoffset="-70"/>`;
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
        colors: this.getDonutColors(series.length),
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
            ? formatNumber(opts.w.globals.series[opts.seriesIndex])
            : formatNumber(val, { percent: true, decimals: 1 })),
        }
      };
      this.renderApexChart(this.getContentContainer(), options);
    }
  }

  WidgetRegistry.register(DonutWidget);
})();