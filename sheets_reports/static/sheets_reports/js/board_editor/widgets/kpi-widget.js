(function () {
  // Estado contra la meta: color del valor y relleno de la barra (tablero-theme.css).
  const STATUS_CLASS = { good: 'tb-status-good', warn: 'tb-status-warn', bad: 'tb-status-bad' };
  const STATUS_BAR = { good: 'tb-fill-good', warn: 'tb-fill-warn', bad: 'tb-fill-bad' };

  class KpiWidget extends BaseWidget {
    static type = 'kpi';
    static palette = {
      icon: 'ti-gauge',
      category: 'data',
      label: 'Tarjeta KPI',
      description: 'Indicador con comparación, meta y tendencia',
    };
    static defaults = { title: 'Tarjeta KPI', width: 'md:col-span-4', height: 300 };

    static mockData() {
      return { value: 412900, label: 'Monto Consumido' };
    }

    constructor(raw) {
      super(raw);
    }

    buildElement() {
      return this.buildStandardCardElement();
    }

    // Miniatura: el valor real de la tarjeta (o uno de reserva) y su subrayado.
    thumbnailContent(ctx) {
      const { x, y, w, h, primary, el } = ctx;
      let val = '';
      if (el) {
        const v = el.querySelector('.tb-kpi-value');
        if (v) val = v.textContent.trim();
      }
      const display = BaseWidget.escapeHTML((val || '1.250').slice(0, 12));
      const fs = Math.min(16, Math.max(9, Math.round(h * 0.28)));
      return `
        <text x="${x + 8}" y="${y + Math.max(24, h - 10)}" font-family="system-ui, -apple-system, sans-serif" font-size="${fs}" font-weight="800" fill="${primary}">${display}</text>
        <rect x="${x + 8}" y="${y + Math.max(26, h - 6)}" width="${Math.min(w - 16, 34)}" height="2.5" rx="1.25" fill="${primary}" opacity="0.3"/>`;
    }

    getProperties() {
      return { ...super.getProperties() };
    }

    format(value, { percent = false, signed = false } = {}) {
      if (typeof value !== 'number') return value ?? '—';
      const decimals = this.style.decimals === '' || this.style.decimals == null ? null : Number(this.style.decimals);
      return formatNumber(value, {
        percent, signed, decimals, currency: !percent && !!this._currency,
        compact: !percent && (this.style.abbreviate ?? false),
        prefix: this.style.prefix ?? '', suffix: this.style.suffix ?? '',
      });
    }

    _compareHTML(compare, percent) {
      if (!compare) return '';
      const esc = BaseWidget.escapeHTML;
      const delta = compare.mode === 'abs' ? compare.delta : compare.delta_pct;
      const variant = compare.better === true ? 'tb-delta-up'
        : compare.better === false ? 'tb-delta-down' : 'tb-delta-flat';
      const arrow = delta > 0 ? '▲' : delta < 0 ? '▼' : '';
      const text = delta == null ? '—'
        : compare.mode === 'abs' ? this.format(delta, { percent, signed: true })
          : this.format(delta, { percent: true, signed: true });
      const title = `${compare.label}: ${this.format(compare.value, { percent })}`;
      return `<span class="tb-delta ${variant} mt-2" title="${esc(title)}">
          ${arrow ? `<span aria-hidden="true">${arrow}</span>` : ''}${esc(String(text))}
          <span class="font-normal opacity-70">respecto a ${esc(compare.label)}</span>
        </span>`;
    }

    _targetHTML(target, status, percent) {
      if (!target) return '';
      const esc = BaseWidget.escapeHTML;
      const pct = target.pct;
      const width = pct == null ? 0 : Math.max(0, Math.min(100, pct));
      const barClass = STATUS_BAR[status] || '';
      const text = pct == null ? 'Sin datos para la meta'
        : `${this.format(pct, { percent: true })} de ${esc(target.label.toLowerCase())} (${esc(String(this.format(target.value, { percent })))})`;
      return `<div class="mt-3 w-full max-w-[16rem]">
          <div class="tb-progress w-full" role="progressbar"
               aria-valuemin="0" aria-valuemax="100" aria-valuenow="${width}">
            <span class="${barClass}" style="width:${width}%"></span>
          </div>
          <span class="tb-kpi-sub mt-1 block">${text}</span>
        </div>`;
    }

    draw(data, style, title) {
      this.style = { ...this.style, ...style };
      if (title) this.title = title;

      const payload = data || this.constructor.mockData();
      this._lastData = payload;

      const esc = BaseWidget.escapeHTML;
      // El formato de la métrica principal lo manda el servidor; los decimales, prefijo y sufijo, el estilo.
      const percent = payload.format === 'percent' || payload.format === 'progress';
      this._currency = payload.format === 'currency' && !this.style.prefix;
      const statusClass = STATUS_CLASS[payload.status] || '';
      const valueText = this.format(payload.value, { percent });

      const main = payload.text
        ? `<span class="tb-kpi-value text-center ${statusClass}">${esc(payload.text)}</span>
           <span class="text-sm font-semibold tb-text-muted mt-0.5">${esc(String(valueText))}</span>`
        : `<span class="tb-kpi-value ${statusClass}">${esc(String(valueText))}</span>`;

      if (this._chart) {
        this._chart.destroy();
        this._chart = null;
      }
      const container = this.getContentContainer();
      container.className = 'flex flex-col items-center justify-center h-full pb-3 min-h-0';
      container.innerHTML = `
        <div class="flex flex-col items-center justify-center flex-1 min-h-0 w-full">
          ${main}
          <span class="tb-kpi-label mt-1 text-center">${esc(payload.label || '')}</span>
          ${this._compareHTML(payload.compare, percent)}
          ${this._targetHTML(payload.target, payload.status, percent)}
        </div>
        ${payload.trend ? '<div class="kpi-trend w-full h-12 shrink-0"></div>' : ''}
      `;
      if (payload.trend) this._renderTrend(container.querySelector('.kpi-trend'), payload.trend, percent);
    }

    _renderTrend(el, trend, percent) {
      this._chart = new ApexCharts(el, BaseWidget.mergeOptions(this.themeOptions(), {
        chart: { type: 'area', height: 48, sparkline: { enabled: true }, fontFamily: 'inherit', animations: { enabled: false } },
        series: [{ name: this.title, data: trend.data }],
        labels: trend.categories,
        stroke: { curve: 'smooth', width: 2 },
        fill: { type: 'gradient', gradient: { opacityFrom: 0.35, opacityTo: 0 } },
        tooltip: {
          x: { show: true },
          y: { formatter: (v) => this.format(v, { percent }), title: { formatter: () => '' } },
        },
      }));
      this._chart.render();
    }
  }

  WidgetRegistry.register(KpiWidget);
})();