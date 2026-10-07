(function () {
  function applySeriesOrder(series, order) {
    if (!order || !order.length) return series;
    const byName = new Map(series.map(s => [s.name, s]));
    const ordered = order.filter(name => byName.has(name)).map(name => byName.get(name));
    if (!ordered.length) return series;
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

    static mockData() {
      return {
        series: [{ name: 'Ejecutado', data: [14200, 19800, 8500, 11000, 6400, 15000] }],
        categories: ['Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun'],
      };
    }

    constructor(raw) {
      super(raw);
      this._seriesColors = new Map();
      this.seriesOrder = Array.isArray(this.style.seriesOrder) ? this.style.seriesOrder : null;
    }

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
        // Transform tabular data to ApexCharts format
        const meta = payload.metadata || {};
        const dimensionField = meta.dimension || payload.columns[0];
        const metricFields = meta.metrics || payload.columns.slice(1);

        categories = payload.rows.map(row => row[dimensionField]);
        series = metricFields.map(field => ({
          name: field,
          data: payload.rows.map(row => row[field] ?? 0),
        }));
      } else if (payload.series) {
        // Legacy format support
        series = payload.series || [{ name: 'Datos', data: [] }];
        categories = payload.categories || [];
      }

      if (data) this._colorsFor(series);
      series = applySeriesOrder(series, this.seriesOrder);

      const horizontal = this.style.horizontal ?? false;
      const allPercent = BaseWidget.allSeriesPercent(payload, series);
      const percentAxis = { formatter: allPercent ? (val) => `${Math.round(val)}%` : BaseWidget.axisNumber };

      const reference = BaseWidget.referenceAnnotations(payload.referenceLines || [], series, {
        horizontal, format: BaseWidget.referenceFormat(allPercent),
      });
      const valueRange = {
        min: (min) => Math.min(min, 0, reference.min ?? 0),
        max: (max) => Math.max(max, reference.max ?? max) * 1.12,
      };

      const roundLabel = (val) => (val == null || isNaN(val) ? val : Math.round(Number(val)));
      const template = this.style.dataLabelFormatter;
      const withTemplate = (format) => (val) => (val == null ? val : template.replace('{value}', format(val)));

      const options = {
        chart: { type: 'bar', stacked: !!this.style.stacked, height: '100%', width: this.style.chartWidth || '100%', fontFamily: 'inherit', toolbar: this.chartExportToolbar() },
        colors: series.map((s, i) => this._seriesColors.get(s.name) || COLOR_PALETTE[i % COLOR_PALETTE.length]),
        series,
        xaxis: {
          categories: categories.map((cat) => formatearEtiquetaApex(cat, 18)),
          labels: {
            rotate: 0,
            align: 'center',
            style: {
              fontSize: '12px',
              cssClass: 'apexcharts-xaxis-label-centered'
            },
            ...(horizontal && percentAxis),
          },
          maxHeight: 150
        },
        annotations: reference.annotations,
        plotOptions: { bar: { horizontal, borderRadius: 4, borderRadiusApplication: 'end',
          [horizontal ? 'barHeight' : 'columnWidth']: (this.style.barWidth ?? 70) + '%',
          dataLabels: { position: 'top' }
        }},
        grid: { show: this.style.showGrid ?? false },
        dataLabels: {
          enabled: true,
          formatter: template
            ? withTemplate(roundLabel)
            : BaseWidget.percentAwareFormatter(payload.percent, roundLabel, 0),
          offsetY: !horizontal && !this.style.stacked ? -20 : 0,
          offsetX: horizontal && !this.style.stacked ? 20 : 0,
          style: {
            fontSize: '11px',
            colors: ['#333'],
          },
          dropShadow: {
            enabled: false, top: 1, left: 1, blur: 1, color: '#000000', opacity: 0.7
          }
        },
        stroke: { show: true, width: 1, colors: ['#fff'] },
        tooltip: {
          shared: true, intersect: false,
          y: { formatter: template ? withTemplate((val) => val) : BaseWidget.percentAwareFormatter(payload.percent) },
        },
        yaxis: {
          labels: {
            ...(this.style.yAxisWidth && { maxWidth: this.style.yAxisWidth }),
            ...(!horizontal && percentAxis),
          },
          ...valueRange,
        }
      };

      this.renderApexChart(this.getContentContainer(), options).then(() => {
        if (!this._readOnly) this._wireLegendDrag(this.getContentContainer(), series);
      });
    }

    _wireLegendDrag(container, series) {
      this._destroyLegendSortable();
      if (this._legendObserver) { this._legendObserver.disconnect(); this._legendObserver = null; }
      if (series.length <= 1 || typeof Sortable === 'undefined') return;
      const legendEl = container.querySelector('.apexcharts-legend');
      if (!legendEl) return;
      this._legendSortable = new Sortable(legendEl, {
        animation: 150,
        draggable: '.apexcharts-legend-series',
        onEnd: (evt) => {
          if (evt.oldIndex === evt.newIndex) return;
          this._onLegendReorder(legendEl);
        },
      });
      this._legendObserver = new MutationObserver(() => {
        const current = container.querySelector('.apexcharts-legend');
        if (current && current !== legendEl) this._wireLegendDrag(container, series);
      });
      this._legendObserver.observe(container, { childList: true, subtree: true });
    }

    _destroyLegendSortable() {
      const sortable = this._legendSortable;
      this._legendSortable = null;
      if (!sortable) return;
      if (Sortable.active || Sortable.dragged) {
        const endEvents = ['dragend', 'mouseup', 'touchend'];
        const done = () => {
          endEvents.forEach(type => document.removeEventListener(type, done));
          setTimeout(() => sortable.destroy());
        };
        endEvents.forEach(type => document.addEventListener(type, done));
      } else {
        sortable.destroy();
      }
    }

    _onLegendReorder(legendEl) {
      const names = [...legendEl.querySelectorAll('.apexcharts-legend-series')]
        .map(el => el.querySelector('.apexcharts-legend-text')?.textContent)
        .filter(Boolean);
      if (!names.length) return;
      this.style.seriesOrder = names;
      this._dirty = true;
      const store = window.Alpine && Alpine.store('dashboard');
      if (store && typeof store._saveWidget === 'function') {
        store._saveWidget(this);
      }
      const container = this.getContentContainer();
      if (container) this.draw(this._lastData, this.style, this.title);
    }

    destroy() {
      this._destroyLegendSortable();
      if (this._legendObserver) { this._legendObserver.disconnect(); this._legendObserver = null; }
      super.destroy();
    }
  }

  WidgetRegistry.register(BarWidget);
})();