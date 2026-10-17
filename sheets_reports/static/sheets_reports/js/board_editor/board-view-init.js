// Barra de carga de la página (board_view.html): visible mientras haya algún request en curso.
// Arranca en 1 porque el HTML la trae visible para la carga inicial.
let pendingLoads = 1;
function setPageLoading(isLoading) {
  pendingLoads = Math.max(0, pendingLoads + (isLoading ? 1 : -1));
  const bar = document.getElementById('page-loader');
  if (bar) bar.classList.toggle('hidden', pendingLoads === 0);
}

document.addEventListener('alpine:init', () => {
  Alpine.store('dashboard', {
    dashboardId: window.DASHBOARD_ID,
    primaryColor: window.DASHBOARD_PRIMARY_COLOR || '#1F8A5F',
    widgets: [],
    sources: [],

    init() {
      this.applyThemeVariables(this.primaryColor);
    },

    applyThemeVariables(color) {
      const base = color || '#1F8A5F';
      const root = document.documentElement;

      if (typeof window.chroma === 'function') {
        const c = window.chroma(base);
        const p500 = base;
        const p600 = c.darken(0.35).hex();
        const p700 = c.darken(0.7).hex();
        const p300 = c.brighten(0.6).hex();
        const p100 = c.brighten(1.4).desaturate(0.4).hex();
        const p50 = c.brighten(1.8).desaturate(0.6).hex();

        root.style.setProperty('--board-primary', p500);
        root.style.setProperty('--board-primary-600', p600);
        root.style.setProperty('--board-primary-700', p700);
        root.style.setProperty('--board-primary-300', p300);
        root.style.setProperty('--board-primary-100', p100);
        root.style.setProperty('--board-primary-50', p50);
        root.style.setProperty('--board-primary-tint', `color-mix(in srgb, ${p500} 10%, white)`);
      } else {
        root.style.setProperty('--board-primary', base);
        root.style.setProperty('--board-primary-600', base);
        root.style.setProperty('--board-primary-700', base);
        root.style.setProperty('--board-primary-300', base);
        root.style.setProperty('--board-primary-100', base);
        root.style.setProperty('--board-primary-50', base);
        root.style.setProperty('--board-primary-tint', `color-mix(in srgb, ${base} 10%, white)`);
      }
    },

    setPrimaryColor(color) {
      this.primaryColor = color || '#1F8A5F';
      this.applyThemeVariables(this.primaryColor);
      this.widgets.forEach(w => {
        if (w && typeof w.draw === 'function') {
          w.draw(w._lastData);
        }
      });
    },

    _renderUrl() {
      const url = apiUrl(`/api/dashboard/${this.dashboardId}/render/`);
      const qs = this.getFilterQueryString ? this.getFilterQueryString() : '';
      return qs ? `${url}?${qs}` : url;
    },

    // Todos los widgets se calculan en un solo request al endpoint render (sin IA).
    async refreshData() {
      setPageLoading(true);
      try {
        const r = await fetch(this._renderUrl());
        const data = await r.json().catch(() => null);
        if (!r.ok || !data) {
          const message = (data && data.error) || `Error ${r.status} al cargar los datos`;
          this.widgets.forEach(w => { w.setLoading(false); w.renderError(message, { retryable: true }); });
          return null;
        }
        const byId = Object.fromEntries(data.widgets.map(w => [w.id, w]));
        if (data.dashboard) {
          if (data.dashboard.sources) this.sources = data.dashboard.sources;
          if (data.dashboard.primary_color) {
            this.setPrimaryColor(data.dashboard.primary_color);
          }
        }
        this.widgets.forEach(w => w.applyRender(byId[w.id]));
        requestAnimationFrame(() => {
          window.dispatchEvent(new Event('resize'));
        });
        return data;
      } catch (e) {
        this.widgets.forEach(w => { w.setLoading(false); w.renderError('No se pudo conectar con el servidor', { retryable: true }); });
        return null;
      } finally {
        setPageLoading(false);
      }
    },
  });
});

document.addEventListener('DOMContentLoaded', async () => {
  const canvasEl = document.getElementById('dashboard-canvas');
  const store = Alpine.store('dashboard');

  let r = null;
  let data = null;
  try {
    r = await fetch(store._renderUrl());
    data = await r.json().catch(() => null);
  } catch (e) {
    // Sin conexión: cae en el mensaje de error de abajo.
  } finally {
    setPageLoading(false);
  }
  if (!r || !r.ok || !data) {
    canvasEl.innerHTML = `<p class="col-span-12 text-sm text-red-600">${BaseWidget.escapeHTML((data && data.error) || 'No se pudo cargar el tablero')}</p>`;
    return;
  }

  store.sources = (data.dashboard && data.dashboard.sources) || [];
  store.widgets = data.widgets
    .map(w => BaseWidget.fromServer(w))
    .filter(Boolean)
    .sort((a, b) => (a.position?.order ?? 0) - (b.position?.order ?? 0));
  const headerEl = document.getElementById('dashboard-filters');
  const rowGrids = {};
  store.widgets.forEach(w => {
    if (w.constructor.placement === 'header') {
      headerEl.appendChild(w.mountReadOnly());
    } else {
      const rowId = w.position?.row_id || 'row_1';
      if (!rowGrids[rowId]) {
        const rowEl = document.createElement('div');
        rowEl.className = 'canvas-row relative';
        rowEl.dataset.rowId = rowId;
        const rowBody = document.createElement('div');
        rowBody.className = 'canvas-row-body grid grid-cols-12 gap-4';
        rowBody.dataset.rowId = rowId;
        rowEl.appendChild(rowBody);
        canvasEl.appendChild(rowEl);
        rowGrids[rowId] = rowBody;
      }
      rowGrids[rowId].appendChild(w.mountReadOnly());
    }
  });
  const byId = Object.fromEntries(data.widgets.map(w => [w.id, w]));
  requestAnimationFrame(() => {
    store.widgets.forEach(w => w.applyRender(byId[w.id]));
    requestAnimationFrame(() => {
      window.dispatchEvent(new Event('resize'));
    });
  });

  window.addEventListener('dashboard:filters-changed', () => store.refreshData());
});