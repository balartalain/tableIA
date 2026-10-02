(function () {
  // Color del número según el semáforo (payload.status) y de la variación según si mejora.
  const STATUS_CLASS = { good: 'text-emerald-600', warn: 'text-amber-600', bad: 'text-red-600' };
  const STATUS_BAR = { good: 'bg-emerald-500', warn: 'bg-amber-500', bad: 'bg-red-500' };
  const SPARK_COLOR = '#16a34a';

  class KpiWidget extends BaseWidget {
    static type = 'kpi';
    static palette = {
      icon: 'ti-gauge',
      category: 'data',
      label: 'Tarjeta KPI',
      description: 'Indicador con comparación, meta y tendencia',
    };
    static defaults = { title: 'Tarjeta KPI', width: 'md:col-span-4', height: 300 };
    // El nombre a mostrar de cada métrica es su etiqueta en la tarjeta.
    static supportsLabels = true;

    static get drawerFields() {
      return [...super.drawerFields,
        { key: 'decimals', label: 'Decimales', type: 'number', min: 0, step: 1 },
        { key: 'abbreviate', label: 'Abreviar (1,2 M)', type: 'checkbox' },
        { key: 'prefix', label: 'Prefijo (ej. RD$)', type: 'text' },
        { key: 'suffix', label: 'Sufijo (ej. uds.)', type: 'text' },
      ];
    }

    static mockData() {
      return { value: 412900, label: 'Monto Consumido' };
    }

    constructor(raw) {
      super(raw);
      this.decimals = raw.decimals ?? '';
      this.abbreviate = raw.abbreviate ?? false;
      this.prefix = raw.prefix ?? '';
      this.suffix = raw.suffix ?? '';
    }

    getProperties() {
      return { ...super.getProperties(),
        decimals: this.decimals, abbreviate: this.abbreviate, prefix: this.prefix, suffix: this.suffix,
      };
    }

    buildElement() {
      return this.buildStandardCardElement();
    }

    // Número con el formato de la tarjeta. Los porcentajes llevan "%" y no prefijo/sufijo.
    format(value, { percent = false, signed = false } = {}) {
      if (typeof value !== 'number') return value ?? '—';
      const decimals = this.decimals === '' || this.decimals == null ? null : Number(this.decimals);
      const options = decimals === null
        ? { maximumFractionDigits: 2 }
        : { minimumFractionDigits: decimals, maximumFractionDigits: decimals };
      if (this.abbreviate && !percent) {
        Object.assign(options, { notation: 'compact', maximumFractionDigits: decimals ?? 1 });
      }
      if (signed) options.signDisplay = 'exceptZero';
      const text = value.toLocaleString(undefined, options);
      return percent ? `${text}%` : `${this.prefix || ''}${text}${this.suffix ? ` ${this.suffix}` : ''}`;
    }

    _compareHTML(compare, percent) {
      if (!compare) return '';
      const esc = BaseWidget.escapeHTML;
      const delta = compare.mode === 'abs' ? compare.delta : compare.delta_pct;
      const color = compare.better === true ? 'text-emerald-700 bg-emerald-50'
        : compare.better === false ? 'text-red-700 bg-red-50' : 'text-ink/50 bg-paper';
      const arrow = delta > 0 ? '▲' : delta < 0 ? '▼' : '';
      const text = delta == null ? '—'
        : compare.mode === 'abs' ? this.format(delta, { percent, signed: true })
          : this.format(delta, { percent: true, signed: true });
      const title = `${compare.label}: ${this.format(compare.value, { percent })}`;
      return `<span class="mt-2 inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-semibold ${color}" title="${esc(title)}">
          ${arrow ? `<span aria-hidden="true">${arrow}</span>` : ''}${esc(String(text))}
          <span class="font-normal opacity-70">vs ${esc(compare.label)}</span>
        </span>`;
    }

    _targetHTML(target, status, percent) {
      if (!target) return '';
      const esc = BaseWidget.escapeHTML;
      const pct = target.pct;
      const width = pct == null ? 0 : Math.max(0, Math.min(100, pct));
      const bar = STATUS_BAR[status] || 'bg-moss-500';
      const text = pct == null ? 'Sin datos para la meta'
        : `${this.format(pct, { percent: true })} de ${esc(target.label.toLowerCase())} (${esc(String(this.format(target.value, { percent })))})`;
      return `<div class="mt-3 w-full max-w-[16rem]">
          <div class="h-1.5 w-full rounded-full bg-line overflow-hidden" role="progressbar"
               aria-valuemin="0" aria-valuemax="100" aria-valuenow="${width}">
            <div class="h-full rounded-full ${bar}" style="width:${width}%"></div>
          </div>
          <span class="mt-1 block text-[11px] text-ink/50">${text}</span>
        </div>`;
    }

    renderContent(container, data) {
      const payload = data || this.constructor.mockData();
      const esc = BaseWidget.escapeHTML;
      const percent = !!payload.percent;
      const statusClass = STATUS_CLASS[payload.status] || 'text-ink';
      const valueText = this.format(payload.value, { percent });
      // Un ranking (top/bottom) muestra el grupo grande y su valor como detalle.
      const main = payload.text
        ? `<span class="text-2xl font-black tracking-tight text-center leading-tight ${statusClass}">${esc(payload.text)}</span>
           <span class="text-sm font-semibold text-ink/60 mt-0.5">${esc(String(valueText))}</span>`
        : `<span class="text-2xl font-black tracking-tight ${statusClass}">${esc(String(valueText))}</span>`;

      if (this._chart) {
        this._chart.destroy();
        this._chart = null;
      }
      container.className = 'flex flex-col items-center justify-center h-full pb-3 min-h-0';
      container.innerHTML = `
        <div class="flex flex-col items-center justify-center flex-1 min-h-0 w-full">
          ${main}
          <span class="text-[11px] font-semibold text-ink/40 mt-0.5 uppercase tracking-wide text-center">${esc(payload.label || '')}</span>
          ${this._compareHTML(payload.compare, percent)}
          ${this._targetHTML(payload.target, payload.status, percent)}
        </div>
        ${payload.trend ? '<div class="kpi-trend w-full h-12 shrink-0"></div>' : ''}
      `;
      if (payload.trend) this._renderTrend(container.querySelector('.kpi-trend'), payload.trend, percent);
    }

    _renderTrend(el, trend, percent) {
      this._chart = new ApexCharts(el, {
        chart: { type: 'area', height: 48, sparkline: { enabled: true }, fontFamily: 'inherit', animations: { enabled: false } },
        series: [{ name: this.title, data: trend.data }],
        labels: trend.categories,
        stroke: { curve: 'smooth', width: 2 },
        fill: { type: 'gradient', gradient: { opacityFrom: 0.35, opacityTo: 0 } },
        colors: [SPARK_COLOR],
        tooltip: {
          x: { show: true },
          y: { formatter: (v) => this.format(v, { percent }), title: { formatter: () => '' } },
        },
      });
      this._chart.render();
    }
  }

  WidgetRegistry.register(KpiWidget);
})();
