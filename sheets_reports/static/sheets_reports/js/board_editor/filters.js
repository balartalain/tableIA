(function () {
  // Selección de la caja de filtros de la página actual: {columna: [valores]}. Es del que mira
  // el tablero (no se guarda en el widget): viaja en la URL como ?page=<id>&filters=[{field,
  // op: 'in', value}] — el mismo formato que valida el backend (WidgetService.parse_board_filters) —,
  // se comparte con el enlace y define el universo de los widgets de esa página. Cada página
  // tiene su caja: la selección de las otras queda en `_pageFilters` mientras no se ven.
  document.addEventListener('alpine:init', () => {
    const store = Alpine.store('dashboard');

    store.boardFilters = {};
    store._pageFilters = {};

    store.initBoardFiltersFromURL = function () {
      const raw = new URLSearchParams(window.location.search).get('filters');
      if (!raw) return;
      try {
        const list = JSON.parse(raw);
        if (!Array.isArray(list)) return;
        const filters = {};
        list.forEach(c => {
          if (c && c.op === 'in' && typeof c.field === 'string' && Array.isArray(c.value) && c.value.length) {
            filters[c.field] = c.value;
          }
        });
        this.boardFilters = filters;
      } catch (e) {
        // Una URL mal formada no debe impedir cargar el tablero: se ignora.
      }
    };

    store._boardConditions = function () {
      return Object.entries(this.boardFilters)
        .filter(([, values]) => values && values.length)
        .map(([field, value]) => ({ field, op: 'in', value }));
    };

    // La URL refleja la página actual y sus filtros.
    store._writeFiltersURL = function () {
      const url = new URL(window.location);
      const conditions = this._boardConditions();
      if (conditions.length) url.searchParams.set('filters', JSON.stringify(conditions));
      else url.searchParams.delete('filters');
      if (this.currentPageId) url.searchParams.set('page', this.currentPageId);
      history.replaceState(history.state, '', url);
    };

    store._applyBoardFilters = function (filters) {
      this.boardFilters = filters;
      this._writeFiltersURL();
      window.dispatchEvent(new CustomEvent('dashboard:filters-changed'));
    };

    // Al cambiar de página: guarda la selección de la que se deja y retoma la de la otra.
    store.swapPageFilters = function (fromPage, toPage) {
      if (fromPage) this._pageFilters[fromPage] = this.boardFilters;
      this.boardFilters = this._pageFilters[toPage] || {};
      this.currentPageId = toPage;
      this._writeFiltersURL();
    };

    // Una página eliminada se lleva su selección.
    store.dropPageFilters = function (pageId) {
      delete this._pageFilters[pageId];
    };

    // Valores elegidos en un control; una lista vacía quita el filtro de esa columna.
    store.setBoardFilter = function (field, values) {
      const filters = { ...this.boardFilters };
      if (values && values.length) filters[field] = values;
      else delete filters[field];
      this._applyBoardFilters(filters);
    };

    // Quita la selección de varias columnas (filtros quitados de la caja o la caja borrada).
    store.clearBoardFilters = function (fields) {
      const present = (fields || []).filter(f => f in this.boardFilters);
      if (!present.length) return;
      const filters = { ...this.boardFilters };
      present.forEach(f => delete filters[f]);
      this._applyBoardFilters(filters);
    };

    // Query string para /render/ y para el enlace de "Compartir".
    store.getFilterQueryString = function () {
      const conditions = this._boardConditions();
      return conditions.length ? `filters=${encodeURIComponent(JSON.stringify(conditions))}` : '';
    };

    store.initBoardFiltersFromURL();
  });
})();
