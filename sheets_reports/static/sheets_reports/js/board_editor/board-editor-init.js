// Grupos de la barra de módulos, en este orden (palette.category de cada widget).
const PALETTE_GROUPS = [
  { id: 'controls', label: 'Controles' },
  { id: 'charts', label: 'Gráficos' },
  { id: 'stats', label: 'Análisis Estadísticos' },
  { id: 'data', label: 'Datos' },
];

// Contenedor donde va cada widget: el grid de su fila o la franja fija de arriba.
function containerFor(widget) {
  const WidgetClass = typeof widget === 'function' ? widget : (widget.constructor || {});
  if (WidgetClass.placement === 'header') {
    return document.getElementById('dashboard-filters');
  }
  const rowId = (typeof widget === 'object' && widget.position?.row_id) ? widget.position.row_id : 'row_1';
  const canvasEl = document.getElementById('dashboard-canvas');
  let rowBody = canvasEl.querySelector(`.canvas-row-body[data-row-id="${rowId}"]`);
  if (!rowBody) {
    rowBody = canvasEl.querySelector('.canvas-row-body');
  }
  return rowBody || canvasEl;
}

const RAIL_STORAGE_KEY = 'tableia:rail-collapsed';

// Texto comparable del buscador: minúsculas y sin acentos.
function searchText(text) {
  return String(text || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().trim();
}

// Lista plana (encabezado de grupo seguido de sus filas): Sortable solo arrastra los hijos
// directos con [data-type], así los encabezados quedan fijos.
function renderPalette(sidebarEl) {
  const esc = BaseWidget.escapeHTML;
  // Los widgets de la franja superior (filtros) no se arrastran: se crean con «Añadir filtro».
  const entries = WidgetRegistry.getPaletteEntries()
    .filter(entry => WidgetRegistry.get(entry.type).placement !== 'header');
  sidebarEl.innerHTML = PALETTE_GROUPS.map(group => {
    const rows = entries.filter(e => (e.category || 'data') === group.id);
    if (!rows.length) return '';
    return `<p class="rail-group text-[10px] font-bold uppercase tracking-wider text-ink/40 pt-3 first:pt-0 pb-1.5 px-1"
               data-group="${group.id}">${group.label}</p>` + rows.map(entry => `
      <div class="rail-item flex items-center gap-2.5 p-2 mb-1.5 bg-white hover:bg-paper border border-line rounded-lg cursor-grab active:cursor-grabbing transition-colors"
           data-type="${entry.type}" data-group="${group.id}"
           data-search="${esc(searchText(`${entry.label} ${entry.description}`))}"
           title="${esc(`${entry.label} — ${entry.description}`)}"
           style="--ghost-span: ${entry.ghostSpan};">
        <span class="rail-icon shrink-0 w-[30px] h-[30px] rounded-md bg-paper border border-line/70 text-ink/70 flex items-center justify-center">
          <i class="ti ${entry.icon} text-[17px]" aria-hidden="true"></i>
        </span>
        <span class="rail-text min-w-0 flex-1">
          <span class="block truncate text-sm font-semibold text-ink">${esc(entry.label)}</span>
          <span class="block truncate text-[11px] text-ink/50">${esc(entry.description)}</span>
        </span>
      </div>`).join('');
  }).join('') + '<p class="rail-empty hidden px-1 py-2 text-xs text-ink/50">Sin resultados</p>';
}

// Filtra las filas visibles por nombre/descripción sin volver a pintar (no toca Sortable).
function filterPalette(sidebarEl, query) {
  const q = searchText(query);
  let visible = 0;
  sidebarEl.querySelectorAll('[data-type]').forEach(row => {
    const match = !q || row.dataset.search.includes(q);
    row.hidden = !match;
    if (match) visible++;
  });
  sidebarEl.querySelectorAll('.rail-group').forEach(header => {
    header.hidden = !sidebarEl.querySelector(`[data-type][data-group="${header.dataset.group}"]:not([hidden])`);
  });
  sidebarEl.querySelector('.rail-empty').classList.toggle('hidden', visible > 0);
}

// Panel contraíble a una tira de íconos. El estado inicial ya lo aplicó el script en línea
// de la plantilla (html[data-rail="collapsed"]) antes de pintar, para que no parpadee.
function initRail(sidebarEl) {
  const rail = document.getElementById('module-rail');
  const toggle = document.getElementById('rail-toggle');
  const search = document.getElementById('rail-search');
  const root = document.documentElement;
  const isCollapsed = () => root.dataset.rail === 'collapsed';

  const sync = () => {
    const collapsed = isCollapsed();
    const label = collapsed ? 'Expandir panel' : 'Contraer panel';
    toggle.setAttribute('aria-expanded', String(!collapsed));
    toggle.setAttribute('aria-label', label);
    toggle.title = label;
  };
  sync();
  // La transición se habilita después del primer pintado: restaurar el estado no se anima.
  requestAnimationFrame(() => requestAnimationFrame(() => rail.classList.add('rail-animated')));

  toggle.addEventListener('click', () => {
    const collapsed = !isCollapsed();
    if (collapsed) root.dataset.rail = 'collapsed';
    else delete root.dataset.rail;
    try { localStorage.setItem(RAIL_STORAGE_KEY, collapsed ? '1' : '0'); } catch (e) {}
    // Contraído no se ve el buscador: no dejar módulos ocultos por un filtro invisible.
    if (collapsed && search.value) {
      search.value = '';
      filterPalette(sidebarEl, '');
    }
    sync();
  });
  // El lienzo cambia de ancho: gráficos y tablas se reajustan al terminar la animación.
  rail.addEventListener('transitionend', (e) => {
    if (e.target === rail && e.propertyName === 'width') window.dispatchEvent(new Event('resize'));
  });

  search.addEventListener('input', () => filterPalette(sidebarEl, search.value));
}

// Global: el store la usa para avisar de un guardado fallido.
window.showToast = showToast;

// Nombre del tablero en el header, editable en el lugar: Enter guarda, Escape revierte.
function boardTitle() {
  return {
    name: '',
    saved: '',

    init() {
      this.name = this.saved = this.$el.dataset.name || '';
    },

    revert() {
      this.name = this.saved;
    },

    async save() {
      const name = this.name.trim();
      if (!name || name === this.saved) {
        this.name = this.saved;
        return;
      }
      try {
        const r = await fetch(apiUrl(`/api/dashboards/${window.DASHBOARD_ID}/`), {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ nombre: name }),
        });
        const data = await r.json().catch(() => null);
        if (!r.ok || !data) throw new Error((data && data.error) || 'No se pudo guardar el nombre');
        this.name = this.saved = data.nombre;
        document.title = `${data.nombre} — Editor de tablero`;
        showToast('Nombre guardado');
      } catch (e) {
        this.revert();
        showToast(e.message === 'Failed to fetch' ? 'No se pudo conectar con el servidor.' : e.message);
      }
    },
  };
}
function showToast(message) {
  let container = document.getElementById('toast-container');
  if (!container) {
    container = document.createElement('div');
    container.id = 'toast-container';
    container.className = 'fixed top-6 left-1/2 -translate-x-1/2 z-[100] flex flex-col items-center gap-2';
    document.body.appendChild(container);
  }

  const toast = document.createElement('div');
  toast.className = 'bg-ink text-white text-sm font-medium px-4 py-2.5 rounded shadow-lg opacity-0 transition-opacity duration-200';
  toast.textContent = message;
  container.appendChild(toast);

  requestAnimationFrame(() => {
    toast.classList.remove('opacity-0');
  });

  setTimeout(() => {
    toast.classList.add('opacity-0');
    setTimeout(() => toast.remove(), 200);
  }, 2000);
}

async function copyToClipboard(text) {
  if (navigator.clipboard) {
    try {
      await navigator.clipboard.writeText(text);
      return;
    } catch (err) {
      // fall through to legacy fallback below
    }
  }
  const textarea = document.createElement('textarea');
  textarea.value = text;
  textarea.style.position = 'fixed';
  textarea.style.opacity = '0';
  document.body.appendChild(textarea);
  textarea.select();
  document.execCommand('copy');
  document.body.removeChild(textarea);
}

document.addEventListener('DOMContentLoaded', async () => {
  const canvasEl = document.getElementById('dashboard-canvas');
  const sidebarEl = document.getElementById('sidebar-components');
  const store = Alpine.store('dashboard');

  renderPalette(sidebarEl);
  initRail(sidebarEl);

  // Tablero nuevo: todavía no hay hoja. Solo el selector de fuente; al confirmarlo se crea el
  // tablero y se recarga su editor.
  if (!window.DASHBOARD_ID) {
    sidebarEl.classList.add('opacity-50', 'pointer-events-none');
    Alpine.store('bottomSheet').open({ title: 'Conectar datos', dismissible: false });
    return;
  }

  // El panel cambia el ancho del lienzo: gráficos y tablas se reajustan al terminar la animación.
  const drawerEl = document.getElementById('edit-drawer');
  drawerEl.addEventListener('transitionend', (e) => {
    if (e.target === drawerEl && e.propertyName === 'width') window.dispatchEvent(new Event('resize'));
  });

  let entries = {};
  try {
    entries = await store.loadBoard();
  } catch (e) {
    canvasEl.insertAdjacentHTML('beforebegin', `<p class="text-sm text-red-600 mb-3">${BaseWidget.escapeHTML(e.message)}</p>`);
  }

  // Espera al siguiente rAF para que Alpine haya renderizado las filas .canvas-row en el DOM
  requestAnimationFrame(() => {
    store.widgets.forEach(w => containerFor(w).appendChild(w.mount()));
    requestAnimationFrame(() => requestAnimationFrame(() => {
      store.widgets.forEach(w => w.applyRender(entries[w.id]));
    }));
  });

  window.addEventListener('dashboard:filters-changed', () => store.refreshData());

  const noTextSelection = {
    onChoose: () => { document.body.style.userSelect = 'none'; },
    onUnchoose: () => { document.body.style.userSelect = ''; },
  };

  new Sortable(sidebarEl, {
    group: { name: 'canvas-widgets', pull: 'clone', put: false },
    sort: false,
    draggable: '[data-type]',
    animation: 150,
    forceFallback: true,
    fallbackTolerance: 3,
    fallbackOnBody: true,
    ...noTextSelection,
    onStart: function (evt) {
      const wUnits = getWidgetWidth(evt.item);
      window._activeDragSpan = wUnits;
      window.dispatchEvent(new CustomEvent('board:ruler:highlight', {
        detail: { isDragging: true, span: wUnits, start: 1, end: wUnits }
      }));
    },
    onEnd: function () {
      window._activeDragSpan = null;
      window.dispatchEvent(new CustomEvent('board:ruler:clear'));
    },
  });

  // Sortable para la franja de controles/filtros superiores
  const filtersEl = document.getElementById('dashboard-filters');
  if (filtersEl) {
    new Sortable(filtersEl, {
      group: {
        name: 'canvas-widgets',
        pull: true,
        put: function (to, from, item) {
          return item.getAttribute('data-type') === 'filter';
        },
      },
      animation: 150,
      filter: '#add-filter-chip-btn',
      draggable: '[data-widget-id], [data-type]',
      forceFallback: true,
      fallbackTolerance: 3,
      fallbackOnBody: true,
      ...noTextSelection,
    });
  }
  new Sortable(canvasEl, {
    animation: 150,
    draggable: '.canvas-row',
    handle: '.row-drag-handle',
    forceFallback: true,
    fallbackTolerance: 3,
    fallbackOnBody: true,
    ...noTextSelection,
    onEnd: function () {
      store.reorderWidgets();
    },
  });

  function getWidgetWidth(item) {
    if (!item) return 6;
    const widgetId = parseInt(item.getAttribute('data-widget-id'), 10);
    if (widgetId) {
      const w = store.widgets.find(w => w.id === widgetId);
      if (w && typeof w.position?.w === 'number') return w.position.w;
    }
    const type = item.getAttribute('data-type');
    if (type) {
      const WidgetClass = WidgetRegistry.get(type);
      const wStr = WidgetClass?.defaults?.width || 'md:col-span-6';
      const match = wStr.match(/col-span-(\d+)/);
      return match ? parseInt(match[1], 10) : 6;
    }
    return 6;
  }

  let _lastToastTime = 0;
  function showToastThrottled(msg) {
    const now = Date.now();
    if (now - _lastToastTime > 2000) {
      _lastToastTime = now;
      showToast(msg);
    }
  }

  function initRowBodySortable(rowBodyEl) {
    if (rowBodyEl._sortable) return;
    rowBodyEl._sortable = new Sortable(rowBodyEl, {
      group: {
        name: 'canvas-widgets',
        pull: true,
        put: function (to, from, item) {
          const toRowId = to.el.getAttribute('data-row-id');
          const fromRowId = from.el.getAttribute('data-row-id');
          const widgetId = parseInt(item.getAttribute('data-widget-id'), 10) || null;
          const wUnits = getWidgetWidth(item);

          // Si el movimiento es dentro de la misma fila, siempre es válido
          if (toRowId && fromRowId && toRowId === fromRowId) return true;

          // Descartar el filtro en las filas del lienzo
          if (item.getAttribute('data-type') === 'filter') return false;

          // Si viene de otra fila o del sidebar, se valida la capacidad acumulada (<= 12)
          const fits = store.canAddWidgetToRow(toRowId, wUnits, widgetId);
          if (!fits) {
            const currentW = store.getRowWidth(toRowId, widgetId);
            const avail = 12 - currentW;
            showToastThrottled(`No hay espacio suficiente en esta fila. Disponible: ${avail} de 12 columnas.`);
            return false;
          }
          return true;
        },
      },
      animation: 150,
      ghostClass: 'grid-ghost-preview',
      handle: '.drag-handle',
      draggable: '[data-widget-id]',
      forceFallback: true,
      fallbackTolerance: 3,
      fallbackOnBody: true,
      ...noTextSelection,

      onStart: function (evt) {
        const wUnits = getWidgetWidth(evt.item);
        window._activeDragSpan = wUnits;
        window.dispatchEvent(new CustomEvent('board:ruler:highlight', {
          detail: { isDragging: true, element: evt.item, span: wUnits }
        }));
      },

      onAdd: function (evt) {
        window._activeDragSpan = null;
        window.dispatchEvent(new CustomEvent('board:ruler:clear'));
        const toRowId = evt.to.getAttribute('data-row-id');
        const isNewFromSidebar = !evt.item.hasAttribute('data-widget-id');
        if (isNewFromSidebar) {
          const type = evt.item.getAttribute('data-type');
          if (!type) return;
          const WidgetClass = WidgetRegistry.get(type);
          const existing = WidgetClass.singleton && store.widgets.find(w => w.chart_type === type);
          if (existing) {
            evt.item.remove();
            showToast(`El tablero ya tiene un widget «${WidgetClass.palette.label}».`);
            store.openDrawer(existing.id);
            return;
          }
          const widget = store.addWidget(type, toRowId);
          const widgetEl = widget.mount();
          if (WidgetClass.placement === 'header') {
            evt.item.remove();
            containerFor(widget).appendChild(widgetEl);
          } else {
            evt.item.replaceWith(widgetEl);
            store.reorderWidgets();
          }
          requestAnimationFrame(() => {
            window.dispatchEvent(new Event('resize'));
          });
          store.openDrawer(widget.id);
        } else {
          store.reorderWidgets();
        }
      },

      onEnd: function (evt) {
        window._activeDragSpan = null;
        window.dispatchEvent(new CustomEvent('board:ruler:clear'));
        store.reorderWidgets();
      },
    });
  }

  function syncRowSortables() {
    canvasEl.querySelectorAll('.canvas-row-body').forEach(el => initRowBodySortable(el));
  }

  syncRowSortables();
  const observer = new MutationObserver(() => syncRowSortables());
  observer.observe(canvasEl, { childList: true, subtree: true });

  // El diseño se guarda solo; si al salir queda un cambio sin enviar, se envía con keepalive
  // para que la petición no se corte al cerrar la página.
  window.addEventListener('pagehide', () => {
    if (store.hasPendingLayout) store.flushLayoutSave({ keepalive: true });
  });

  // Enlace de la vista compartida con los filtros elegidos: lo copian «Compartir» y lo abre
  // «Vista previa».
  const shareUrl = () => {
    const base = `${window.location.origin}${window.SHARE_PATH}`;
    const qs = store.getFilterQueryString ? store.getFilterQueryString() : '';
    return qs ? `${base}?${qs}` : base;
  };

  document.getElementById('share-btn').addEventListener('click', async () => {
    await copyToClipboard(shareUrl());
    showToast('Enlace copiado');
  });

  document.getElementById('preview-btn').addEventListener('click', async () => {
    // La pestaña se abre en el mismo clic (si no, el navegador la bloquea como popup); la URL
    // se pone después de guardar el acomodo pendiente, para que la vista lo muestre.
    const win = window.open('', '_blank');
    if (store.hasPendingLayout) await store.flushLayoutSave();
    if (win) win.location = shareUrl();
    else window.location.href = shareUrl();
  });
});