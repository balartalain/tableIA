// Grupos de la barra de módulos, en este orden (palette.category de cada widget).
const PALETTE_GROUPS = [
  { id: 'charts', label: 'Gráficos' },
  { id: 'data', label: 'Datos' },
  { id: 'controls', label: 'Controles' },
];

// Contenedor donde va cada widget: el grid o, si su clase lo pide, la franja fija de arriba.
function containerFor(WidgetClass) {
  return document.getElementById(WidgetClass.placement === 'header' ? 'dashboard-filters' : 'dashboard-canvas');
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
  const entries = WidgetRegistry.getPaletteEntries();
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
function showToast(message) {
  let container = document.getElementById('toast-container');
  if (!container) {
    container = document.createElement('div');
    container.id = 'toast-container';
    container.className = 'fixed top-6 left-1/2 -translate-x-1/2 z-[100] flex flex-col items-center gap-2';
    document.body.appendChild(container);
  }

  const toast = document.createElement('div');
  toast.className = 'bg-ink text-white text-sm font-medium px-4 py-2.5 rounded-xl shadow-lg opacity-0 transition-opacity duration-200';
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

  // El panel cambia el ancho del lienzo: gráficos y tablas se reajustan al terminar la animación.
  const drawerEl = document.getElementById('edit-drawer');
  drawerEl.addEventListener('transitionend', (e) => {
    if (e.target === drawerEl && e.propertyName === 'width') window.dispatchEvent(new Event('resize'));
  });

  store.loadSchema();

  let entries = {};
  try {
    entries = await store.loadBoard();
  } catch (e) {
    canvasEl.insertAdjacentHTML('beforebegin', `<p class="text-sm text-red-600 mb-3">${BaseWidget.escapeHTML(e.message)}</p>`);
  }
  store.widgets.forEach(w => containerFor(w.constructor).appendChild(w.mount()));
  // Doble rAF: ApexCharts necesita que el contenedor ya tenga su tamaño final al montar.
  requestAnimationFrame(() => requestAnimationFrame(() => {
    store.widgets.forEach(w => w.applyRender(entries[w.id]));
  }));

  window.addEventListener('dashboard:filters-changed', () => store.refreshData());
  if (window.REFRESH_MINUTES > 0) {
    setInterval(() => store.refreshData(), window.REFRESH_MINUTES * 60 * 1000);
  }

  new Sortable(sidebarEl, {
    group: { name: 'shared', pull: 'clone', put: false },
    sort: false,
    draggable: '[data-type]',
    animation: 150,
  });

  new Sortable(canvasEl, {
    group: 'shared',
    animation: 150,
    ghostClass: 'grid-ghost-preview',
    handle: '.drag-handle',
    // Arrastre de Sortable en vez del nativo: la copia del navegador se dibuja encima del widget
    // reducida y desvanecida (si es grande) y, al mover despacio, se ve como un flash. La de
    // Sortable es un clon del mismo tamaño que sigue al cursor.
    forceFallback: true,
    fallbackTolerance: 3,
    // El clon va en <body>: dentro del lienzo sería otro hijo del grid con el mismo data-widget-id.
    fallbackOnBody: true,

    onAdd: function (evt) {
      const type = evt.item.getAttribute('data-type');
      const WidgetClass = WidgetRegistry.get(type);
      // Widgets únicos (la caja de filtros): si ya hay uno, se abre ese en vez de crear otro.
      const existing = WidgetClass.singleton && store.widgets.find(w => w.chart_type === type);
      if (existing) {
        evt.item.remove();
        showToast(`El tablero ya tiene un widget «${WidgetClass.palette.label}».`);
        store.openDrawer(existing.id);
        return;
      }
      const widget = store.addWidget(type);
      const widgetEl = widget.mount();
      if (WidgetClass.placement === 'header') {
        // Siempre arriba y a todo el ancho, sin importar dónde se soltó.
        evt.item.remove();
        containerFor(WidgetClass).appendChild(widgetEl);
      } else {
        evt.item.replaceWith(widgetEl);
        store.reorderWidgets();
        store.fluidStartColOnDrop(widgetEl);
      }
      requestAnimationFrame(() => {
        window.dispatchEvent(new Event('resize'));
      });
      // Un widget nuevo no tiene datos todavía: abrir el panel para describirlo.
      store.openDrawer(widget.id);
    },

    onEnd: function (evt) {
      store.reorderWidgets();
      store.fluidStartColOnDrop(evt.item);
    },
  });

  // El diseño se guarda solo; si al salir queda un cambio sin enviar, se envía con keepalive
  // para que la petición no se corte al cerrar la página.
  window.addEventListener('pagehide', () => {
    if (store.hasPendingLayout) store.flushLayoutSave({ keepalive: true });
  });

  document.getElementById('share-btn').addEventListener('click', async () => {
    const base = `${window.location.origin}${window.SHARE_PATH}`;
    const qs = Alpine.store('dashboard').getFilterQueryString
      ? Alpine.store('dashboard').getFilterQueryString()
      : '';
    const url = qs ? base + '?' + qs : base;
    await copyToClipboard(url);
    showToast('Enlace copiado');
  });
});
