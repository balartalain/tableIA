(function () {
  function applySeriesOrder(series, order) {
    if (!order || !order.length) return series;
    const byName = new Map(series.map(s => [s.name, s]));
    const ordered = order.filter(name => byName.has(name)).map(name => byName.get(name));
    if (!ordered.length) return series; // orden guardado 100% obsoleto -> usar el original
    const remaining = series.filter(s => !order.includes(s.name));
    return [...ordered, ...remaining];
  }

  const COLOR_PALETTE = BaseWidget.CHART_COLORS;

  class BarWidget extends BaseWidget {
    static type = 'bar';
    static palette = {
      icon: 'ti-chart-bar',
      category: 'charts',
      label: 'Gráfico de Barras',
      description: 'Comparativas grupales',
    };
    static defaults = { title: 'Gráfico de Barras', width: 'md:col-span-6', height: 300 };

    static supportsStacked = true;
    static FIELD_HORIZONTAL = { key: 'horizontal', label: 'Horizontal', type: 'checkbox' };

    static get drawerFields() {
      return [...super.drawerFields,
        this.FIELD_HORIZONTAL,
        { key: 'yAxisWidth', label: 'Ancho del Eje Y (px)', type: 'number', min: 100, step: 10 },
        { key: 'barWidth', label: 'Ancho de barra', type: 'range', min: 20, max: 90, step: 5 },
        { key: 'dataLabelFormatter', label: 'Formato de Etiquetas de Datos. Ej. {value} %', type: 'text' },
        { key: 'chartWidth', label: 'Forzar ancho de gráfico', type: 'number', min: 100, step: 50 },
        { key: 'showGrid', label: 'Mostrar cuadrícula', type: 'checkbox' },
      ];
    }

    static mockData() {
      return {
        series: [{ name: 'Ejecutado', data: [14200, 19800, 8500, 11000, 6400, 15000] }],
        categories: ['Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun'],
      };
    }

    constructor(raw) {
      super(raw);
      this.horizontal = raw.horizontal ?? false;
      this.yAxisWidth = raw.yAxisWidth;
      this.barWidth = raw.barWidth ?? 70;
      this.dataLabelFormatter = raw.dataLabelFormatter;
      this.chartWidth = raw.chartWidth;
      this.showGrid = raw.showGrid ?? false;
      this.seriesOrder = Array.isArray(raw.seriesOrder) ? raw.seriesOrder : null;
      this._seriesColors = new Map();
    }

    // Asigna un color estable por nombre de serie (no por posición), así el color de cada
    // serie no cambia al reordenar la leyenda y siempre coincide con el tooltip/las barras.
    _colorsFor(series) {
      series.forEach((s) => {
        if (!this._seriesColors.has(s.name)) {
          this._seriesColors.set(s.name, COLOR_PALETTE[this._seriesColors.size % COLOR_PALETTE.length]);
        }
      });
      return series.map((s) => this._seriesColors.get(s.name));
    }

    buildElement() {
      return this.buildStandardCardElement();
    }

    getProperties() {
      return { ...super.getProperties(),
        horizontal: this.horizontal,
        yAxisWidth: this.yAxisWidth,
        barWidth: this.barWidth,
        dataLabelFormatter: this.dataLabelFormatter,
        chartWidth: this.chartWidth,
        showGrid: this.showGrid,
        seriesOrder: this.seriesOrder,
      };
    }

    renderContent(container, data) {
      const payload = data || this.constructor.mockData();
      this._lastData = payload;
      const rawSeries = payload.series || [{ name: 'Datos', data: [] }];
      // El render inicial (sin `data`, mientras se espera el fetch real) usa mockData() como
      // placeholder; no debe consumir la paleta de colores estables, o el primer color quedaría
      // "gastado" en un nombre de serie que nunca vuelve a aparecer.
      if (data) this._colorsFor(rawSeries);
      const series = applySeriesOrder(rawSeries, this.seriesOrder);
      const categories = payload.categories || [];

      if (this.chartWidth) {
        container.style.overflowX = 'scroll';
        container.style.overflowY = '';
      } else {
        container.style.overflowX = '';
        container.style.overflowY = '';
      }

      const options = {
        chart: { type: 'bar', stacked: !!payload.stacked, height: '90%', width: this.chartWidth || '100%', fontFamily: 'inherit', toolbar: this.chartExportToolbar() },
        colors: series.map((s, i) => this._seriesColors.get(s.name) || COLOR_PALETTE[i % COLOR_PALETTE.length]),
        series,
        xaxis: {
          categories: categories.map((cat) => formatearEtiquetaApex(cat, 18)), // Llama a la función para formatear las etiquetas
          labels: {
            rotate: 0,        // Mantiene el texto horizontal (sin inclinar)
            align: 'center',  // Centra las líneas de texto entre sí
            style: {
              fontSize: '12px',
              cssClass: 'apexcharts-xaxis-label-centered'
            }
          },
          maxHeight: 150
        },
        plotOptions: { bar: { horizontal: this.horizontal, borderRadius: 4, borderRadiusApplication: 'end',
          [this.horizontal ? 'barHeight' : 'columnWidth']: this.barWidth + '%',
          dataLabels:{
            position: 'top'
          }
        }},
        grid: {
          show: this.showGrid
        },
        dataLabels: {
          enabled: true,
          // El formato configurado por el usuario manda; si no hay, las series % llevan "%".
          formatter: this.dataLabelFormatter
            ? (val) => this.dataLabelFormatter.replace('{value}', val)
            : BaseWidget.percentAwareFormatter(payload.percent),
          //crop: false,
          offsetY: !this.horizontal && !payload.stacked ? -20 : 0,
          offsetX: this.horizontal && !payload.stacked ? 20 : 0,
          // background: {
          //     enabled: true,
          //     foreColor: '#fff',     // Color del texto DENTRO del fondo (Blanco)
          //     padding: 4,            // Espaciado interno del fondo
          //     borderRadius: 4,       // Bordes redondeados del fondo
          //     borderWidth: 1,        // Grosor del borde
          //     borderColor: '#111',   // Color del borde del fondo
          //     opacity: 0.9,          // Opacidad del fondo
          //     dropShadow: {          // Añade una pequeña sombra al fondo si quieres
          //       enabled: false
          //     }
          //   },
          style: {
            fontSize: '11px',
            colors: ['#333'],
          },
          dropShadow: {
            enabled: false,    // ¡Activa la sombra nativa!
            top: 1,           // Desplazamiento vertical de la sombra
            left: 1,          // Desplazamiento horizontal de la sombra
            blur: 1,          // Qué tan difuminada está la sombra
            color: '#000000', // Color de la sombra (negro)
            opacity: 0.7      // Opacidad (0 es transparente, 1 es oscuro total)
          }
        },
        stroke: {
          show: true,
          width: 1,
          colors: ['#fff'],
        },
        tooltip: {
          shared: true,
          intersect: false,
          y: { formatter: BaseWidget.percentAwareFormatter(payload.percent) },
        },
        yaxis: {
          labels: {
            ...(this.yAxisWidth && { maxWidth: this.yAxisWidth }),
            ...(BaseWidget.allSeriesPercent(payload, series) && { formatter: (val) => `${Math.round(val)}%` }),
          },
          min: (min) => Math.min(min, 0),
          max: (max) => max * 1.12,
        }
      };
      // Reordenar series arrastrando la leyenda edita el widget: no en la vista compartida.
      this.renderApexChart(container, options).then(() => {
        if (!this._readOnly) this._wireLegendDrag(container, series);
      });
    }

    _wireLegendDrag(container, series) {
      if (this._legendSortable) { this._legendSortable.destroy(); this._legendSortable = null; }
      if (this._legendObserver) { this._legendObserver.disconnect(); this._legendObserver = null; }
      if (series.length <= 1 || typeof Sortable === 'undefined') return;
      const legendEl = container.querySelector('.apexcharts-legend');
      if (!legendEl) return;
      this._legendSortable = new Sortable(legendEl, {
        animation: 150,
        draggable: '.apexcharts-legend-series',
        onEnd: (evt) => {
          // onEnd también dispara en un simple click (ej. el toggle de mostrar/ocultar serie de
          // ApexCharts), sin que haya habido arrastre real. Si el índice no cambió, no es un
          // reorden: no tocar el chart y dejar que ApexCharts maneje el toggle por su cuenta.
          if (evt.oldIndex === evt.newIndex) return;
          this._onLegendReorder(legendEl);
        },
      });
      // Al mostrar/ocultar una serie desde la leyenda, ApexCharts recrea por completo el nodo
      // .apexcharts-legend (no solo le cambia estilos), dejando el Sortable de arriba enganchado
      // a un nodo ya removido del DOM. Este observer detecta ese reemplazo y reengancha.
      this._legendObserver = new MutationObserver(() => {
        const current = container.querySelector('.apexcharts-legend');
        if (current && current !== legendEl) this._wireLegendDrag(container, series);
      });
      this._legendObserver.observe(container, { childList: true, subtree: true });
    }

    _onLegendReorder(legendEl) {
      const names = [...legendEl.querySelectorAll('.apexcharts-legend-series')]
        .map(el => el.querySelector('.apexcharts-legend-text')?.textContent)
        .filter(Boolean);
      if (!names.length) return;
      this.seriesOrder = names;
      this._dirty = true;
      const store = window.Alpine && Alpine.store('dashboard');
      if (store && typeof store._saveWidget === 'function') {
        store._saveWidget(this);
      }
      // Variante simple: en vez de updateOptions() (transición animada, pero necesita lidiar a
      // mano con colores/series ocultas que ApexCharts arrastra por índice entre updates), se
      // destruye y recrea el chart entero con los datos ya cargados — el mismo camino que usa
      // cualquier otro refresh del widget (renderContent -> renderApexChart).
      const container = this.getContentContainer();
      if (container) this.renderContent(container, this._lastData);
    }

    destroy() {
      if (this._legendSortable) { this._legendSortable.destroy(); this._legendSortable = null; }
      if (this._legendObserver) { this._legendObserver.disconnect(); this._legendObserver = null; }
      super.destroy();
    }
  }

  WidgetRegistry.register(BarWidget);
})();
