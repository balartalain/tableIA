(function () {
  class LineWidget extends BaseWidget {
    static type = 'line';
    static palette = {
      icon: 'ti-chart-line',
      category: 'charts',
      label: 'Gráfico de Líneas',
      description: 'Tendencias en el tiempo',
    };
    static defaults = { title: 'Gráfico de Líneas', width: 'md:col-span-6', height: 300 };

    static mockData() {
      return {
        series: [{ name: 'Tendencia', data: [45, 52, 38, 65, 59, 87] }],
        categories: ['Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun'],
      };
    }

    constructor(raw) {
      super(raw);
    }

    buildElement() {
      return this.buildStandardCardElement();
    }

    // Miniatura: el SVG real del gráfico y, si no se puede, una línea esquemática.
    async thumbnailContent(ctx) {
      const svg = await this.apexThumbnail(ctx);
      if (svg) return svg;
      const { x, y, w, h, primary } = ctx;
      const p1 = y + h - 8;
      const p2 = y + 14;
      const p3 = y + h - 16;
      const p4 = y + 12;
      return `
        <path d="M${x + 6} ${p1} Q${x + w * 0.35} ${p2} ${x + w * 0.65} ${p3} T${x + w - 6} ${p4}" fill="none" stroke="${primary}" stroke-width="2" stroke-linecap="round"/>
        <circle cx="${x + w * 0.65}" cy="${p3}" r="2" fill="${primary}"/>
        <circle cx="${x + w - 6}" cy="${p4}" r="2" fill="${primary}"/>`;
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
      let categories = [];

      if (payload.columns && payload.rows) {
        const meta = payload.metadata || {};
        const dimensionField = meta.dimension || payload.columns[0];
        const metricFields = meta.metrics || payload.columns.slice(1);

        categories = payload.rows.map(row => row[dimensionField]);
        series = metricFields.map(field => ({
          name: field,
          data: payload.rows.map(row => row[field] ?? 0),
        }));
      } else if (payload.series) {
        series = payload.series || [{ name: 'Datos', data: [] }];
        categories = payload.categories || [];
      }

      const allPercent = BaseWidget.allSeriesPercent(payload, series);
      const reference = BaseWidget.referenceAnnotations(payload.referenceLines || [], series, {
        format: BaseWidget.referenceFormat(allPercent),
      });

      const options = {
        chart: {
          type: 'line', height: '100%', width: '100%', fontFamily: 'inherit',
          toolbar: this.chartExportToolbar(),
        },
        colors: this.getSeriesColors(series.length),
        stroke: { curve: 'monotoneCubic', width: 3 },
        markers: { size: 3, hover: { size: 5 } },
        series,
        xaxis: {
          categories: categories.map((cat) => formatearEtiquetaApex(cat, 18)),
          labels: { rotate: 0, hideOverlappingLabels: true, style: { fontSize: '11px' } },
          tooltip: { enabled: false },
        },
        tooltip: { shared: true, intersect: false, y: { formatter: BaseWidget.percentAwareFormatter(payload.percent) } },
        annotations: reference.annotations,
        yaxis: {
          labels: { formatter: allPercent ? (val) => formatNumber(val, { percent: true, decimals: 0 }) : BaseWidget.axisNumber },
          ...(reference.max != null && {
            min: (min) => Math.min(min, reference.min),
            max: (max) => Math.max(max, reference.max),
          }),
        },
        grid: { padding: { left: 12, right: 18 } },
      };
      this.renderApexChart(this.getContentContainer(), options);
    }
  }

  WidgetRegistry.register(LineWidget);
})();