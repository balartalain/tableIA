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
    return `<p class="rail-group ui-sidebar-label pt-3 first:pt-0 pb-1.5 px-1"
               data-group="${group.id}">${group.label}</p>` + rows.map(entry => `
      <div class="rail-item ui-module-card mb-1.5 active:cursor-grabbing transition-colors"
           data-type="${entry.type}" data-group="${group.id}"
           data-search="${esc(searchText(`${entry.label} ${entry.description}`))}"
           title="${esc(`${entry.label} — ${entry.description}`)}"
           style="--ghost-span: ${entry.ghostSpan};">
        <span class="rail-icon ui-module-icon">
          <i class="ti ${entry.icon} text-base" aria-hidden="true"></i>
        </span>
        <span class="rail-text min-w-0 flex-1">
          <span class="block truncate text-sm font-semibold text-fg">${esc(entry.label)}</span>
          <span class="block truncate text-xs text-fg-muted">${esc(entry.description)}</span>
        </span>
      </div>`).join('');
  }).join('') + '<p class="rail-empty hidden px-1 py-2 text-xs text-fg-muted">Sin resultados</p>';
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
        // Sincronizar input de configuración si está presente
        const settingsInput = document.getElementById('settings-board-name');
        if (settingsInput) settingsInput.value = data.nombre;
        showToast('Nombre guardado');
      } catch (e) {
        this.revert();
        showToast(e.message === 'Failed to fetch' ? 'No se pudo conectar con el servidor.' : e.message);
      }
    },
  };
}

// Miniatura del tablero: mide la geometría REAL del DOM (#dashboard-canvas) y delega el
// contenido de cada tarjeta en su widget (widget.thumbnailContent → BaseWidget). Sin
// dependencias ni rasterizado: los gráficos incrustan su SVG real de ApexCharts.
async function generateRealBoardThumbnail() {
  const totalW = 340;
  const padding = 8;
  const availableW = totalW - (padding * 2);

  const canvasEl = document.getElementById('dashboard-canvas');
  // Colores del tema del tablero, resueltos: el SVG va como <img> y no ve las variables CSS.
  const theme = ChartTheme.read(canvasEl);
  const widgetEls = canvasEl
    ? Array.from(canvasEl.querySelectorAll('.canvas-row-body > [data-widget-id]'))
    : [];

  if (!widgetEls.length) {
    return 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(
      `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${totalW} 190" width="${totalW}" height="190">
        <rect width="${totalW}" height="190" fill="${theme.bg}"/>
        <rect x="12" y="12" width="${totalW - 24}" height="166" rx="4" fill="${theme.card}" stroke="${theme.border}" stroke-dasharray="4 4"/>
        <circle cx="${totalW / 2}" cy="82" r="16" fill="${theme.track}"/>
        <path d="M${totalW / 2 - 8} 82 h16 M${totalW / 2} 74 v16" stroke="${theme.categorical[1]}" stroke-width="2" stroke-linecap="round"/>
        <text x="${totalW / 2}" y="118" font-family="system-ui, -apple-system, sans-serif" font-size="9" fill="${theme.textMuted}" text-anchor="middle">Tablero sin widgets</text>
      </svg>`
    );
  }

  const store = window.Alpine && Alpine.store('dashboard');
  const findWidget = el => (store && store.widgets ? store.widgets.find(w => w.el === el) : null) || null;

  // Medir la geometría real ANTES de cualquier await (el layout no cambia durante la espera).
  const measured = widgetEls.map((el, idx) => {
    const apexEl = el.querySelector('svg.apexcharts-svg');
    return {
      el,
      idx,
      widget: findWidget(el),
      wr: el.getBoundingClientRect(),
      apexEl,
      apexRect: apexEl ? apexEl.getBoundingClientRect() : null,
    };
  });

  let minTop = Infinity;
  let maxBottom = -Infinity;
  let minLeft = Infinity;
  let maxRight = -Infinity;
  measured.forEach(m => {
    minTop = Math.min(minTop, m.wr.top);
    maxBottom = Math.max(maxBottom, m.wr.bottom);
    minLeft = Math.min(minLeft, m.wr.left);
    maxRight = Math.max(maxRight, m.wr.right);
  });

  const contentW = Math.max(1, maxRight - minLeft);
  const contentH = Math.max(1, maxBottom - minTop);
  const scale = availableW / contentW;
  const totalH = Math.max(120, Math.round(contentH * scale) + padding * 2);

  let innerSvg = '';

  for (const m of measured) {
    const { el, idx, widget, wr, apexEl, apexRect } = m;
    const x = padding + Math.round((wr.left - minLeft) * scale);
    const y = padding + Math.round((wr.top - minTop) * scale);
    const w = Math.max(18, Math.round(wr.width * scale));
    const h = Math.max(18, Math.round(wr.height * scale));

    const rawTitle = (el.querySelector('.title-display')?.textContent || (widget && widget.title) || '').trim();
    const title = BaseWidget.escapeHTML(rawTitle.length > 26 ? rawTitle.slice(0, 25) + '…' : rawTitle);

    // Tarjeta base (mismo look que los widgets: fondo y borde de la tarjeta del tema).
    innerSvg += `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="4" fill="${theme.card}" stroke="${theme.border}" stroke-width="1"/>`;
    if (title) {
      innerSvg += `<text x="${x + 5}" y="${y + 9}" font-family="system-ui, -apple-system, sans-serif" font-size="6" font-weight="700" fill="${theme.textMuted}">${title}</text>`;
    }

    // La paleta sale del propio widget o del tema del tablero.
    const palette = (widget && typeof widget.thumbnailPalette === 'function')
      ? widget.thumbnailPalette()
      : theme.categorical;
    const primary = palette[0];

    // Caja real del gráfico (para incrustar su SVG de ApexCharts), si la hay.
    const content = apexRect ? {
      x: padding + Math.round((apexRect.left - minLeft) * scale),
      y: padding + Math.round((apexRect.top - minTop) * scale),
      w: Math.max(12, Math.round(apexRect.width * scale)),
      h: Math.max(12, Math.round(apexRect.height * scale)),
    } : null;

    const ctx = { x, y, w, h, scale, el, apexEl, content, colors: palette, primary, theme };

    // Cada widget dibuja su propio contenido; si no implementa nada, barras genéricas.
    let contentSvg = '';
    if (widget && typeof widget.thumbnailContent === 'function') {
      try {
        contentSvg = (await widget.thumbnailContent(ctx)) || '';
      } catch (e) {
        console.warn(`Miniatura: fallo al dibujar el widget "${widget && widget.type}"`, e);
      }
    }
    innerSvg += contentSvg || BaseWidget.thumbBars(ctx);
  }

  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${totalW} ${totalH}" width="${totalW}" height="${totalH}"><rect width="${totalW}" height="${totalH}" fill="${theme.bg}"/>${innerSvg}</svg>`;
  return 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg);
}

// Pestañas de las páginas del editor: renombrar en el sitio y reordenar arrastrando. Al soltar,
// Sortable devuelve la pestaña a su sitio y el store guarda el orden nuevo: Alpine repinta.
function pageTabs() {
  return {
    editingId: null,
    draft: '',
    menuOpen: false,

    init() {
      if (typeof Sortable === 'undefined') return;
      new Sortable(this.$refs.list, {
        draggable: '[data-page-id]',
        filter: 'input',
        preventOnFilter: false,
        animation: 150,
        onEnd: (evt) => {
          if (evt.oldIndex === evt.newIndex) return;
          // Los índices «draggable» no cuentan el <template> del x-for; los del DOM, sí.
          const ids = Alpine.store('dashboard').pages.map(p => p.id);
          const [moved] = ids.splice(evt.oldDraggableIndex, 1);
          ids.splice(evt.newDraggableIndex, 0, moved);
          const list = evt.from;
          list.insertBefore(evt.item, list.children[evt.oldIndex + (evt.oldIndex > evt.newIndex ? 1 : 0)] || null);
          Alpine.store('dashboard').reorderPages(ids);
        },
      });
    },

    startRename(page) {
      this.editingId = page.id;
      this.draft = page.name;
      this.$nextTick(() => {
        const input = this.$el.querySelector(`[data-page-id="${page.id}"] input`);
        if (input) { input.focus(); input.select(); }
      });
    },

    commit() {
      if (!this.editingId) return;
      const id = this.editingId;
      this.editingId = null;
      Alpine.store('dashboard').renamePage(id, this.draft);
    },

    cancel() {
      this.editingId = null;
    },
  };
}

function boardHeaderMenu() {
  return {
    open: false,

    async duplicateBoard() {
      if (!window.DASHBOARD_ID) return;
      try {
        const r = await fetch(apiUrl(`/api/dashboards/${window.DASHBOARD_ID}/duplicate/`), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
        });
        const data = await r.json().catch(() => null);
        if (!r.ok || !data) throw new Error((data && data.error) || 'No se pudo duplicar el tablero');
        showToast('Tablero duplicado');
        if (data.id) {
          window.location.href = window.BOARD_EDITOR_URL.replace('/0/', `/${data.id}/`);
        }
      } catch (e) {
        showToast(e.message || 'Error al duplicar el tablero');
      }
    },

    async saveTemplate() {
      if (!window.DASHBOARD_ID) return;
      // Espera a que el lienzo esté pintado y las animaciones de los gráficos hayan terminado.
      await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      const thumbnail = await generateRealBoardThumbnail();

      try {
        const r = await fetch(apiUrl(`/api/dashboards/${window.DASHBOARD_ID}/template/`), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ thumbnail }),
        });
        const data = await r.json().catch(() => null);
        if (!r.ok || !data) throw new Error((data && data.error) || 'No se pudo guardar la plantilla');
        showToast('Plantilla guardada. Disponible en el menú Plantillas.');
      } catch (e) {
        showToast(e.message || 'Error al guardar la plantilla');
      }
    },
  };
}

async function saveBoardNameFromSettings(newName) {
  const name = (newName || '').trim();
  if (!name || !window.DASHBOARD_ID) return;
  try {
    const r = await fetch(apiUrl(`/api/dashboards/${window.DASHBOARD_ID}/`), {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ nombre: name }),
    });
    const data = await r.json().catch(() => null);
    if (!r.ok || !data) throw new Error((data && data.error) || 'No se pudo guardar el nombre');
    const titleInput = document.getElementById('board-title');
    if (titleInput) {
      titleInput.value = data.nombre;
      titleInput.dataset.name = data.nombre;
    }
    document.title = `${data.nombre} — Editor de tablero`;
    showToast('Nombre guardado');
  } catch (e) {
    showToast(e.message || 'Error al guardar el nombre');
  }
}

async function deleteCurrentDashboard() {
  if (!window.DASHBOARD_ID) return;
  if (!confirm('¿Estás seguro de que deseas eliminar este tablero? Esta acción no se puede deshacer.')) return;
  try {
    const r = await fetch(apiUrl(`/api/dashboards/${window.DASHBOARD_ID}/`), {
      method: 'DELETE',
    });
    if (!r.ok) throw new Error('No se pudo eliminar el tablero');
    window.location.href = window.HOME_URL || '/';
  } catch (e) {
    showToast(e.message || 'Error al eliminar el tablero');
  }
}

window.boardHeaderMenu = boardHeaderMenu;
window.saveBoardNameFromSettings = saveBoardNameFromSettings;
window.deleteCurrentDashboard = deleteCurrentDashboard;
function showToast(message) {
  let container = document.getElementById('toast-container');
  if (!container) {
    container = document.createElement('div');
    container.id = 'toast-container';
    container.className = 'fixed top-6 left-1/2 -translate-x-1/2 z-[100] flex flex-col items-center gap-2';
    document.body.appendChild(container);
  }

  const toast = document.createElement('div');
  toast.className = 'bg-primary text-primary-on text-sm font-medium px-4 py-2.5 rounded shadow-lg opacity-0 transition-opacity duration-200';
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
    canvasEl.insertAdjacentHTML('beforebegin', `<p class="text-sm text-danger mb-3">${BaseWidget.escapeHTML(e.message)}</p>`);
  }

  store.mountWidgets(entries);

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
            showToast(`La página ya tiene un widget «${WidgetClass.palette.label}».`);
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
    // Abre la página que se está editando, con sus filtros.
    const base = `${window.location.origin}${window.SHARE_PATH}`;
    const params = [store.pages.length > 1 ? `page=${encodeURIComponent(store.currentPageId)}` : '',
                    store.getFilterQueryString ? store.getFilterQueryString() : ''].filter(Boolean);
    return params.length ? `${base}?${params.join('&')}` : base;
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