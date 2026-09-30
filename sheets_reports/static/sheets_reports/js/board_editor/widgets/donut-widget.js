(function () {
  class DonutWidget extends BaseWidget {
    static type = 'donut';
    static palette = {
      icon: '<svg viewBox="0 0 20 20" width="1.25rem" height="1.25rem" class="inline-block"><circle cx="10" cy="10" r="7" fill="none" stroke="currentColor" stroke-width="3" opacity="0.25"/><path d="M10 3A7 7 0 1 1 4.5 16" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"/></svg>',
      label: 'Gráfico de Dona',
      description: 'Proporciones por categoría',
      chipClass: 'bg-rose-50/60 border border-rose-200 hover:bg-rose-100/80',
      titleClass: 'text-rose-950',
      descClass: 'text-rose-700/80',
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
      // Formato nativo de ApexCharts para donut (ver apex_compiler._compile_donut).
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
