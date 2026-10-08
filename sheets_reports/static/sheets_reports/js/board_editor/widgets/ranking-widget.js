(function () {
  class RankingWidget extends BaseWidget {
    static type = 'ranking';
    static palette = {
      icon: 'ti-trophy',
      category: 'data',
      label: 'Ranking',
      description: 'Top N: los mejores o los peores',
    };
    static defaults = { title: 'Ranking', width: 'md:col-span-4', height: 340 };

    static mockData() {
      return {
        order: 'best',
        metric: { alias: 'total', label: 'Ventas', format: null },
        items: [
          { rank: 1, label: 'Ana', value: 9200, share: 31 },
          { rank: 2, label: 'Luis', value: 7400, share: 25 },
          { rank: 3, label: 'Eva', value: 5100, share: 17 },
          { rank: 4, label: 'Raúl', value: 3900, share: 13 },
        ],
        rest: { count: 6, value: 4200, share: 14 },
      };
    }

    constructor(raw) {
      super(raw);
    }

    buildElement() {
      return this.buildStandardCardElement();
    }

    buildReadOnlyElement() {
      const el = super.buildReadOnlyElement();
      el.querySelector('.actions-slot').innerHTML = this.downloadButtonHTML('Descargar CSV');
      return el;
    }

    getProperties() {
      return { ...super.getProperties() };
    }

    // El formato de la métrica lo manda el servidor (moneda, porcentaje); abreviar, el estilo.
    format(value, format) {
      const percent = format === 'percent' || format === 'progress';
      return formatNumber(value, {
        percent, currency: format === 'currency', compact: !percent && !!this.style.abbreviate,
      });
    }

    _rankHTML(item) {
      return `<span class="shrink-0 min-w-[1.5rem] text-center text-sm font-bold tabular-nums text-ink/50">${item.rank}</span>`;
    }

    // Ancho de la barra: el % de la métrica respecto al total (la barra y el % cuentan lo
    // mismo). Una métrica sin total (promedio, mínimo…) no tiene %: relativo al mayor mostrado.
    _barWidth(item, max) {
      if (this.style.showBars === false) return 0;
      const pct = item.share != null ? item.share : (max ? Math.abs(item.value || 0) / max * 100 : 0);
      return pct > 0 ? Math.min(100, Math.max(1, pct)) : 0;
    }

    _itemHTML(item, payload, max) {
      const esc = BaseWidget.escapeHTML;
      const best = payload.order !== 'worst';
      const showShare = this.style.showShare !== false && item.share != null;
      const width = this._barWidth(item, max);
      // La barra, fina y debajo de la fila, alineada con el nombre (después de la posición).
      const bar = width
        ? `<span class="block h-1 ml-[2.125rem] mt-1 rounded-full bg-line overflow-hidden" aria-hidden="true">
             <span class="block h-full rounded-full ${best ? 'bg-moss-500' : 'bg-red-400'}" style="width:${width}%"></span>
           </span>`
        : '';
      return `<li class="px-2 py-1.5">
          <span class="flex items-center gap-2.5 min-w-0">
            ${this._rankHTML(item)}
            <span class="min-w-0 flex-1 truncate text-sm font-medium text-ink" title="${esc(item.label)}">${esc(item.label)}</span>
            <span class="shrink-0 text-right">
              <span class="block text-sm font-semibold tabular-nums text-ink">${esc(String(this.format(item.value, payload.metric.format)))}</span>
              ${showShare ? `<span class="block text-[11px] tabular-nums text-ink/45">${formatNumber(item.share, { percent: true, decimals: 1 })}</span>` : ''}
            </span>
          </span>
          ${bar}
        </li>`;
    }

    _restHTML(rest) {
      if (!rest || !rest.count || this.style.showRest === false) return '';
      const share = this.style.showShare !== false && rest.share != null
        ? ` · ${formatNumber(rest.share, { percent: true, decimals: 1 })} del total` : '';
      return `<p class="shrink-0 pt-2 mt-1 border-t border-line text-[11px] text-ink/50">+ ${rest.count} más${share}</p>`;
    }

    draw(data, style, title) {
      this.style = { ...this.style, ...style };
      if (title) this.title = title;

      const payload = data || this.constructor.mockData();
      this._lastData = payload;
      const items = payload.items || [];
      const container = this.getContentContainer();
      container.className = 'flex flex-col h-full min-h-0';

      if (!items.length) {
        container.innerHTML = '<p class="m-auto text-xs text-ink/40">Sin datos que cumplan la condición.</p>';
        return;
      }
      const max = Math.max(...items.map(i => Math.abs(i.value || 0)));
      container.innerHTML = `
        <ol class="flex-1 min-h-0 overflow-auto space-y-0.5">
          ${items.map(item => this._itemHTML(item, payload, max)).join('')}
        </ol>
        ${this._restHTML(payload.rest)}`;

      const downloadBtn = this.el && this.el.querySelector('.download-csv-btn');
      if (downloadBtn) downloadBtn.onclick = () => this._downloadCSV(payload);
    }

    _downloadCSV(payload) {
      const headers = ['Posición', payload.dimension || 'Nombre', payload.metric.label, '% del total'];
      const rows = (payload.items || []).map(i => [i.rank, i.label, i.value, i.share]);
      this.downloadRowsAsCSV(headers, rows, `${this._filenameSlug('ranking')}.csv`);
    }
  }

  WidgetRegistry.register(RankingWidget);
})();
