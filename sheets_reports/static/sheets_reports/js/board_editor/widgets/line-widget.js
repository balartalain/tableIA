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

    buildElement() {
      return this.buildStandardCardElement();
    }

    renderContent(container, data) {
      const payload = data || this.constructor.mockData();
      const series = payload.series || [{ name: 'Datos', data: [] }];
      const categories = payload.categories || [];
      const options = {
        chart: { type: 'line', height: '100%', width: '100%', fontFamily: 'inherit',
          toolbar: {
            show: true, // Muestra el toolbar
            tools: {
              download: true,  // Botón de descargar (SVG, PNG, CSV)
              selection: true, // Herramienta de selección
              zoom: true,      // Zoom por selección
              zoomin: true,    // Acercar
              zoomout: true,   // Alejar
              pan: true,       // Desplazamiento (Pan)
              reset: true      // Reiniciar zoom
            }
          }
        },
        colors: ['#7c3aed', '#2563eb', '#f5a623', '#10b981', '#ef4444', '#0ea5e9'],
        stroke: { curve: 'smooth', width: 3 },
        series,
        xaxis: { categories, labels: { style: { fontSize: '11px' } } },
        tooltip: { y: { formatter: BaseWidget.percentAwareFormatter(payload.percent) } },
        ...(BaseWidget.allSeriesPercent(payload, series) && {
          yaxis: { labels: { formatter: (val) => `${Math.round(val)}%` } },
        }),
        grid: { padding: { bottom: 25 } },
      };
      this.renderApexChart(container, options);
    }
  }

  WidgetRegistry.register(LineWidget);
})();
