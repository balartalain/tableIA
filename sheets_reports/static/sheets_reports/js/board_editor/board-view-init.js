// Barra de carga de la página (board_view.html): visible mientras haya algún request en curso.
// Arranca en 1 porque el HTML la trae visible para la carga inicial.
let pendingLoads = 1;
function setPageLoading(isLoading) {
  pendingLoads = Math.max(0, pendingLoads + (isLoading ? 1 : -1));
  const bar = document.getElementById('page-loader');
  if (bar) bar.classList.toggle('hidden', pendingLoads === 0);
  // Mientras carga no se cambia de página: las pestañas quedan tapadas (.is-page-loading).
  document.documentElement.classList.toggle('is-page-loading', pendingLoads > 0);
}

function isPageLoading() {
  return pendingLoads > 0;
}

document.addEventListener('alpine:init', () => {
  Alpine.store('dashboard', {
    dashboardId: window.DASHBOARD_ID,
    widgets: [],
    sources: [],
    // Página a la vista: solo se calculan y montan sus widgets. Arranca con la de la URL.
    currentPageId: new URLSearchParams(window.location.search).get('page'),

    _renderUrl() {
      const url = apiUrl(`/api/dashboard/${this.dashboardId}/render/`);
      const params = [this.currentPageId ? `page=${encodeURIComponent(this.currentPageId)}` : '',
                      this.getFilterQueryString ? this.getFilterQueryString() : ''].filter(Boolean);
      return params.length ? `${url}?${params.join('&')}` : url;
    },

    // Número de la última petición de datos de la página (loadPage o refreshData): solo su
    // respuesta se aplica a los widgets.
    _renderSeq: 0,
    // Las peticiones de datos de la página ocupan un solo lugar en la barra de carga: lo
    // libera la última (la de _renderSeq); las anteriores, o las de otra página, no la tocan.
    // Arranca ocupado: el HTML trae la barra visible para la carga inicial.
    _renderLoading: true,
    _setRenderLoading(on) {
      if (this._renderLoading === on) return;
      this._renderLoading = on;
      setPageLoading(on);
    },

    // Todos los widgets de la página se calculan en un solo request al endpoint render (sin IA).
    async refreshData() {
      const seq = ++this._renderSeq;
      this._setRenderLoading(true);
      try {
        const r = await fetch(this._renderUrl());
        const data = await r.json().catch(() => null);
        // Llegó otra petición después (ej. otra opción del filtro): esta ya no vale.
        if (seq !== this._renderSeq) return null;
        if (!r.ok || !data) {
          const message = (data && data.error) || `Error ${r.status} al cargar los datos`;
          this.widgets.forEach(w => w.renderError(message, { retryable: true }));
          return null;
        }
        const byId = Object.fromEntries(data.widgets.map(w => [w.id, w]));
        if (data.dashboard) {
          if (data.dashboard.sources) this.sources = data.dashboard.sources;
        }
        this.widgets.forEach(w => w.applyRender(byId[w.id]));
        requestAnimationFrame(() => {
          window.dispatchEvent(new Event('resize'));
        });
        return data;
      } catch (e) {
        if (seq !== this._renderSeq) return null;
        this.widgets.forEach(w => w.renderError('No se pudo conectar con el servidor', { retryable: true }));
        return null;
      } finally {
        if (seq === this._renderSeq) this._setRenderLoading(false);
      }
    },

    // Carga la página `pageId` y monta sus widgets en lugar de los que había.
    // Foto de cada página visitada ({page, dashboard: {sources}, widgets}, como /render/): al
    // volver a ella se monta sin pedirla otra vez (sus filtros son suyos, sus datos no cambian
    // mientras no se ve). Vive hasta recargar.
    _pageCache: {},
    _loadedPage: null,  // la página cuyos datos están dibujados

    // La foto de la página dibujada; ninguna si todavía hay datos en camino.
    _snapshotPage() {
      if (!this._loadedPage || isPageLoading()) return null;
      return {
        page: this._loadedPage,
        dashboard: { sources: this.sources },
        widgets: this.widgets.map(w => ({
          id: w.id, type: w.type, source_id: w.source_id, title: w.title, position: w.position,
          fields: w.fields, style: w.style,
          data: w._lastEntry ? w._lastEntry.data ?? null : null,
          error: w._lastEntry ? w._lastEntry.error ?? null : null,
        })),
      };
    },

    // Muestra la página `pageId` en lugar de la actual: desde su foto o pidiéndola.
    async loadPage(pageId) {
      const canvasEl = document.getElementById('dashboard-canvas');
      const headerEl = document.getElementById('dashboard-filters');
      const snapshot = this._snapshotPage();
      if (snapshot) this._pageCache[snapshot.page] = snapshot;
      // Su propia secuencia: las respuestas en vuelo de la página anterior ya no valen.
      const seq = ++this._renderSeq;
      this.widgets.forEach(w => w.destroy());
      this.widgets = [];
      this._loadedPage = null;
      canvasEl.innerHTML = '';
      headerEl.innerHTML = '';
      if (this.swapPageFilters && this.currentPageId !== pageId) this.swapPageFilters(this.currentPageId, pageId);
      this.currentPageId = pageId;

      const cached = pageId && this._pageCache[pageId];
      if (cached) {
        this._setRenderLoading(false);
        this._mountPage(cached);
        return;
      }
      this._setRenderLoading(true);

      let r = null;
      let data = null;
      try {
        r = await fetch(this._renderUrl());
        data = await r.json().catch(() => null);
      } catch (e) {
        // Sin conexión: cae en el mensaje de error de abajo.
      } finally {
        if (seq === this._renderSeq) this._setRenderLoading(false);
      }
      if (seq !== this._renderSeq) return;  // ya se pidió otra página
      if (!r || !r.ok || !data) {
        canvasEl.innerHTML = `<p class="col-span-12 text-sm text-danger">${BaseWidget.escapeHTML((data && data.error) || 'No se pudo cargar el tablero')}</p>`;
        return;
      }
      // Una página que ya no existe cae en la primera: la URL lo refleja.
      if (pageId && data.page !== pageId) {
        this.currentPageId = data.page;
        if (this._writeFiltersURL) this._writeFiltersURL();
      }
      this._mountPage(data);
    },

    // Monta los widgets de una respuesta de /render/ (o de una foto) en filas de solo lectura.
    _mountPage(data) {
      const canvasEl = document.getElementById('dashboard-canvas');
      const headerEl = document.getElementById('dashboard-filters');
      this.currentPageId = data.page;
      this._loadedPage = data.page;
      this.sources = (data.dashboard && data.dashboard.sources) || [];
      this.widgets = data.widgets
        .map(w => BaseWidget.fromServer(w))
        .filter(Boolean)
        .sort((a, b) => (a.position?.order ?? 0) - (b.position?.order ?? 0));
      const rowGrids = {};
      this.widgets.forEach(w => {
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
        this.widgets.forEach(w => w.applyRender(byId[w.id]));
        requestAnimationFrame(() => {
          window.dispatchEvent(new Event('resize'));
        });
      });
    },
  });
});

// Pestañas de la vista: una por página (data-page) y, con el envío activado, la de correos
// (data-tab="emails", también con #correos en la URL).
document.addEventListener('DOMContentLoaded', async () => {
  const store = Alpine.store('dashboard');
  const tabs = Array.from(document.querySelectorAll('#view-tabs .view-tab'));
  const chartsPanel = document.getElementById('tab-charts');
  const emailsPanel = document.getElementById('tab-emails');

  const select = (tab) => tabs.forEach(t => t.setAttribute('aria-selected', t === tab));

  const showPage = async (pageId) => {
    if (isPageLoading()) return;
    select(tabs.find(t => t.dataset.page === pageId) || null);
    const fromEmails = chartsPanel.classList.contains('hidden');
    chartsPanel.classList.remove('hidden');
    if (emailsPanel) {
      emailsPanel.classList.add('hidden');
      if (window.emailReport) window.emailReport.hide();
      if (window.location.hash) history.replaceState(history.state, '', window.location.pathname + window.location.search);
    }
    if (pageId !== store.currentPageId) {
      await store.loadPage(pageId);
    } else if (fromEmails) {
      // Los gráficos dibujados con la pestaña oculta toman su ancho al volver.
      window.dispatchEvent(new Event('resize'));
    }
  };

  const showEmails = () => {
    if (isPageLoading()) return;
    select(tabs.find(t => t.dataset.tab === 'emails'));
    chartsPanel.classList.add('hidden');
    emailsPanel.classList.remove('hidden');
    history.replaceState(history.state, '', '#correos');
    if (window.emailReport) window.emailReport.show();
  };

  tabs.forEach(tab => tab.addEventListener('click', () => {
    if (tab.dataset.tab === 'emails') showEmails();
    else showPage(tab.dataset.page);
  }));

  window.addEventListener('dashboard:filters-changed', () => store.refreshData());

  await store.loadPage(store.currentPageId);
  if (window.location.hash === '#correos' && emailsPanel) showEmails();
  else select(tabs.find(t => t.dataset.page === store.currentPageId) || null);
});
