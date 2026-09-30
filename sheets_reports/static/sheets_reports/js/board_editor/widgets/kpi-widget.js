(function () {
  class KpiWidget extends BaseWidget {
    static type = 'kpi';
    static palette = {
      icon: '🔢',
      label: 'Tarjeta KPI',
      description: 'Indicador numérico único',
      chipClass: 'bg-emerald-50/60 border border-emerald-200 hover:bg-emerald-100/80',
      titleClass: 'text-emerald-950',
      descClass: 'text-emerald-700/80',
    };
    static defaults = { title: 'Tarjeta KPI', width: 'md:col-span-4', height: 300 };
    static supportsDimension = false;
    static supportsPivot = false;
    static maxMetrics = 1;

    static mockData() {
      return { value: 412900, label: 'Monto Consumido' };
    }

    buildElement() {
      return this.buildStandardCardElement();
    }

    renderContent(container, data) {
      const payload = data || this.constructor.mockData();
      const value = payload.value ?? '—';
      let formattedValue = typeof value === 'number'
        ? value.toLocaleString(undefined, { maximumFractionDigits: 2 })
        : value;
      if (payload.percent && typeof value === 'number') formattedValue += '%';
      container.className = 'flex flex-col items-center justify-center h-full pb-3';
      container.innerHTML = `
        <span class="text-2xl font-black text-ink tracking-tight">${BaseWidget.escapeHTML(String(formattedValue))}</span>
        <span class="text-[11px] font-semibold text-ink/40 mt-0.5 uppercase tracking-wide">${BaseWidget.escapeHTML(payload.label || '')}</span>
      `;
    }
  }

  WidgetRegistry.register(KpiWidget);
})();
