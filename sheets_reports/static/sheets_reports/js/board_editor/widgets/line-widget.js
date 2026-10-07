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
        colors: BaseWidget.CHART_COLORS,
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
          labels: { formatter: allPercent ? (val) => `${Math.round(val)}%` : BaseWidget.axisNumber },
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