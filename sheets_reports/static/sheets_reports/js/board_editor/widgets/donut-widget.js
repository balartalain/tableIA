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
    // Reparte UNA métrica entre las categorías: sin pivote.
    static supportsPivot = false;
    static maxMetrics = 1;

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
        chart: { type: 'donut', height: '90%', width: '100%', fontFamily: 'inherit', toolbar: this.chartExportToolbar() },
        colors: ['#2563eb', '#f5a623', '#1F8A5F', '#60a5fa', '#93c5fd', '#bfdbfe', '#dbeafe'],
        series,
        labels,
        legend: { position: 'bottom', fontSize: '11px' },
        dataLabels: {
          enabled: true//,
          /*formatter: function (val) {
            // Usamos Math.round() para redondear al entero más cercano (ej: 44.25 -> 44)
            return Math.round(val) + "%";

            // O si prefieres dejar exactamente 1 decimal (ej: 44.3%):
            // return val.toFixed(1) + "%";
          }*/
        }
      };
      this.renderApexChart(container, options);
    }
  }

  WidgetRegistry.register(DonutWidget);
})();
