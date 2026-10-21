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

    // Miniatura: la lista del ranking (posición + nombre + barra + valor), con datos de reserva.
    thumbnailContent(ctx) {
      const { x, y, w, h } = ctx;
      const top = y + 15;
      const avail = Math.max(6, h - 17);
      const n = Math.max(1, Math.min(5, Math.floor(avail / 11)));
      const rowH = avail / n;
      const pcts = [0.95, 0.8, 0.64, 0.5, 0.38];
      let rows = '';
      for (let i = 0; i < n; i++) {
        const ry = top + i * rowH;
        const barW = Math.max(3, (w - 34) * pcts[i % pcts.length]);
        const labelW = Math.max(8, (w - 44) * 0.5);
        rows += `
        <rect x="${x + 5}" y="${ry + 1}" width="7" height="7" rx="1.5" fill="${ctx.theme.track}"/>
        <rect x="${x + 16}" y="${ry + 1.5}" width="${labelW}" height="2.6" rx="1.3" fill="${ctx.theme.border}"/>
        <rect x="${x + 16}" y="${ry + 6}" width="${barW}" height="2.2" rx="1.1" fill="${i === 0 ? ctx.colors[1] : ctx.primary}"/>
        <rect x="${x + w - 12}" y="${ry + 1.5}" width="7" height="2.6" rx="1.3" fill="${ctx.theme.textMuted}"/>`;
      }
      return rows;
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
      return `<span class="tb-rank-pos shrink-0 min-w-[1.5rem] text-center text-sm tabular-nums">${item.rank}</span>`;
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
      // «Mejores»: el líder destacado y el resto en el primer color; «peores»: en el de alerta.
      const barClass = !best ? 'tb-fill-bad' : item.rank === 1 ? 'tb-bar-lead' : '';
      // La barra, debajo de la fila, alineada con el nombre (después de la posición).
      const bar = width
        ? `<span class="tb-bar block ml-[2.125rem] mt-1" aria-hidden="true">
             <span class="${barClass}" style="width:${width}%"></span>
           </span>`
        : '';
      return `<li class="px-2 py-1.5">
          <span class="flex items-center gap-2.5 min-w-0">
            ${this._rankHTML(item)}
            <span class="min-w-0 flex-1 truncate text-sm font-medium tb-text" title="${esc(item.label)}">${esc(item.label)}</span>
            <span class="shrink-0 text-right">
              <span class="block text-sm font-semibold tabular-nums tb-text">${esc(String(this.format(item.value, payload.metric.format)))}</span>
              ${showShare ? `<span class="block text-xs tabular-nums tb-text-muted">${formatNumber(item.share, { percent: true, decimals: 1 })}</span>` : ''}
            </span>
          </span>
          ${bar}
        </li>`;
    }

    _restHTML(rest) {
      if (!rest || !rest.count || this.style.showRest === false) return '';
      const share = this.style.showShare !== false && rest.share != null
        ? ` · ${formatNumber(rest.share, { percent: true, decimals: 1 })} del total` : '';
      return `<p class="shrink-0 pt-2 mt-1 border-t tb-border text-xs tb-text-muted">+ ${rest.count} más${share}</p>`;
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
        container.innerHTML = '<p class="m-auto text-xs tb-text-muted">Sin datos que cumplan la condición.</p>';
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
