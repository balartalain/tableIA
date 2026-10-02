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
    // Qué se ve en cada porción. El backend siempre manda los valores; el porcentaje lo
    // calcula ApexCharts sobre el total de las porciones.
    static FIELD_LABEL_MODE = {
      key: 'labelMode',
      label: 'Mostrar en las porciones',
      type: 'select',
      options: [
        { value: 'percent', label: 'Porcentaje' },
        { value: 'value', label: 'Valor' },
      ],
    };

    static get drawerFields() {
      return [...super.drawerFields, this.FIELD_LABEL_MODE];
    }

    constructor(raw) {
      super(raw);
      this.labelMode = raw.labelMode === 'value' ? 'value' : 'percent';
    }

    getProperties() {
      return { ...super.getProperties(), labelMode: this.labelMode };
    }

    static mockData() {
      return { series: [44, 55, 13, 33], labels: ['Norte', 'Sur', 'Este', 'Oeste'] };
    }

    buildElement() {
      return this.buildStandardCardElement();
    }

    renderContent(container, data) {
      const payload = data || this.constructor.mockData();
      // Formato nativo de ApexCharts para donut (ver widgets/donut.py: DonutWidget.compile).
      const series = (payload.series || []).map(v => v ?? 0);
      const labels = payload.labels || [];
      const options = {
        chart: { type: 'donut', height: '100%', width: '100%', fontFamily: 'inherit', toolbar: this.chartExportToolbar() },
        plotOptions: {
          pie: {
            donut: {
              size: '50%',
              labels: {
                show: true,
                value: {
                  show: true,
                  fontSize: '18px',         // <--- Modifica el tamaño del número
                  fontFamily: 'Inter, sans-serif',
                  fontWeight: 'bold',
                  color: '#111111',
                  offsetY: 8                // Opcional: ajusta la separación vertical respecto al texto
                },
                total: {
                  show: true,
                  showAlways: true,      // Muestra el total de forma fija (no solo al pasar el mouse)
                  label: 'Total',        // Texto encima del número
                  fontSize: '16px',      // Estilo del texto del label
                  fontFamily: 'Inter, sans-serif',
                  color: '#888888',
                  formatter: function (w) {
                    // Suma automáticamente todos los valores de la serie
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
        legend: { position: 'bottom', fontSize: '10px' },
        dataLabels: {
          enabled: true,
          style: {
            fontSize: '10px',
            fontFamily: 'Roboto, sans-serif',
            fontWeight: '400',
            colors: ['#000000'],
          },
          // Sin la sombra por defecto: está pensada para texto blanco y ensucia el negro.
          dropShadow: { enabled: false },
          // `val` es el porcentaje que calcula ApexCharts; el valor de la porción es el que
          // mandó el backend (series[i]).
          formatter: (val, opts) => (this.labelMode === 'value'
            ? Number(opts.w.globals.series[opts.seriesIndex]).toLocaleString(undefined, { maximumFractionDigits: 2 })
            : `${Number(val).toFixed(1)}%`),
        }
      };
      this.renderApexChart(container, options);
    }
  }

  WidgetRegistry.register(DonutWidget);
})();
