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
        chart: {
          type: 'line', height: '100%', width: '100%', fontFamily: 'inherit',
          // Solo descargar (igual que barras y dona): el zoom/pan no sirve en un eje de
          // categorías y sus íconos se dibujaban encima del gráfico.
          toolbar: this.chartExportToolbar(),
        },
        colors: BaseWidget.CHART_COLORS,
        // monotoneCubic: curva suave que no se pasa de los puntos ('smooth' inventaba picos y
        // valles entre categorías, incluso por debajo de 0).
        stroke: { curve: 'monotoneCubic', width: 3 },
        markers: { size: 3, hover: { size: 5 } },
        series,
        xaxis: {
          // Mismo formato que las barras: etiquetas largas en varias líneas, sin inclinar.
          categories: categories.map((cat) => formatearEtiquetaApex(cat, 18)),
          labels: { rotate: 0, hideOverlappingLabels: true, style: { fontSize: '11px' } },
          tooltip: { enabled: false },
        },
        tooltip: { shared: true, intersect: false, y: { formatter: BaseWidget.percentAwareFormatter(payload.percent) } },
        ...(BaseWidget.allSeriesPercent(payload, series) && {
          yaxis: { labels: { formatter: (val) => `${Math.round(val)}%` } },
        }),
        // Margen a los lados para que las etiquetas de los extremos no queden cortadas.
        grid: { padding: { left: 12, right: 18 } },
      };
      this.renderApexChart(container, options);
    }
  }

  WidgetRegistry.register(LineWidget);
})();
