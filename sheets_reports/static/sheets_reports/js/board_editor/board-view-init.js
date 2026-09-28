document.addEventListener('alpine:init', () => {
  Alpine.store('dashboard', {
    dashboardId: window.DASHBOARD_ID,
    widgets: [],

    _renderUrl() {
      const url = apiUrl(`/api/dashboard/${this.dashboardId}/render/`);
      const qs = this.getFilterQueryString ? this.getFilterQueryString() : '';
      return qs ? `${url}?${qs}` : url;
    },

    // Todos los widgets se calculan en un solo request al endpoint render (sin IA).
    async refreshData() {
      try {
        const r = await fetch(this._renderUrl());
        const data = await r.json().catch(() => null);
        if (!r.ok || !data) {
          const message = (data && data.error) || `Error ${r.status} al cargar los datos`;
          this.widgets.forEach(w => { w.setLoading(false); w.renderError(message, { retryable: true }); });
          return null;
        }
        const byId = Object.fromEntries(data.widgets.map(w => [w.id, w]));
        this.widgets.forEach(w => w.applyRender(byId[w.id]));
        return data;
      } catch (e) {
        this.widgets.forEach(w => { w.setLoading(false); w.renderError('No se pudo conectar con el servidor', { retryable: true }); });
        return null;
      }
    },
  });
});

document.addEventListener('DOMContentLoaded', async () => {
  const canvasEl = document.getElementById('dashboard-canvas');
  const store = Alpine.store('dashboard');

  const r = await fetch(store._renderUrl());
  const data = await r.json().catch(() => null);
  if (!r.ok || !data) {
    canvasEl.innerHTML = `<p class="col-span-12 text-sm text-red-600">${BaseWidget.escapeHTML((data && data.error) || 'No se pudo cargar el tablero')}</p>`;
    return;
  }

  store.widgets = data.widgets
    .map(w => BaseWidget.fromServer(w))
    .sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
  store.widgets.forEach(w => canvasEl.appendChild(w.mountReadOnly()));
  const byId = Object.fromEntries(data.widgets.map(w => [w.id, w]));
  requestAnimationFrame(() => store.widgets.forEach(w => w.applyRender(byId[w.id])));

  window.addEventListener('dashboard:filters-changed', () => store.refreshData());
  if (window.REFRESH_MINUTES > 0) {
    setInterval(() => store.refreshData(), window.REFRESH_MINUTES * 60 * 1000);
  }
});
