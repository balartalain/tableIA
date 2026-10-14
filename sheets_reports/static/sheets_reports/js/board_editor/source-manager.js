// Gestor de las fuentes de datos del tablero (bottom sheet «Fuentes de datos»): la tabla de
// fuentes, y el selector (source-picker.js) para agregar una o editarla. Cada fila tiene su estado
// (si la cuenta de servicio todavía llega a la pestaña) y sus acciones: actualizar (relee la hoja
// de Google: el tablero no lo hace por su cuenta), cambiar hoja, abrir en Google Sheets, editar
// columnas y eliminar (avisa qué widgets se quedan sin datos).
const SOURCE_STATUS = {
  no_access: { label: 'Sin acceso',
    title: 'La cuenta de servicio ya no puede abrir la hoja (se dejó de compartir o se borró). El tablero muestra los últimos datos leídos.' },
  tab_missing: { label: 'Pestaña no encontrada',
    title: 'La pestaña se borró o se reemplazó: usa «Cambiar fuente». El tablero muestra los últimos datos leídos.' },
};

function sourceManager() {
  return {
    view: 'list',
    sources: [],
    loading: false,
    error: '',
    // Fuente por eliminar, con los widgets que la usan: {source, impact}.
    pendingDelete: null,
    // Por fuente: su estado ({id: "ok" | "no_access" | "tab_missing" | null}), si se está
    // actualizando y el error del último «Actualizar».
    statuses: {},
    refreshing: {},
    refreshErrors: {},
    // Vista de datos transformados (view === 'data')
    previewSource: null,
    loadingData: false,
    dataError: '',
    previewColumns: [],
    totalRows: 0,
    // Filas ya cargadas en la tabla de datos (crece al hacer scroll).
    loadedRows: 0,
    isFormResponse: false,
    _previewTable: null,
    selectedColumnJump: '',

    init() {
      window.addEventListener('sources:open', () => this.open());
      window.addEventListener('sources:back', () => { this.view = 'list'; });
      window.addEventListener('sources:changed', () => this.afterChange());
      window.addEventListener('bottom-sheet:resized', () => {
        if (this.view === 'data' && this._previewTable) {
          this._previewTable.redraw(true);
        }
      });
    },

    open() {
      this.closeDataView();
      this.view = 'list';
      this.pendingDelete = null;
      this.load();
    },

    async load() {
      this.loading = true;
      this.error = '';
      try {
        const { r, data } = await fetchJsonSafe(apiUrl(`/api/dashboard/${window.DASHBOARD_ID}/sources/`));
        if (!r.ok || !data) throw new Error((data && data.error) || `Error ${r.status}`);
        this.sources = data.sources || [];
        this.sources.forEach(s => this.checkStatus(s));
      } catch (e) {
        this.error = e.message === 'Failed to fetch' ? 'No se pudo conectar con el servidor.' : e.message;
      } finally {
        this.loading = false;
      }
    },

    columnsLabel(s) {
      return s.columns_total == null ? 'Todas' : `${s.columns_included} de ${s.columns_total} incluidas`;
    },

    refreshedLabel(s) {
      if (!s.refreshed_at) return 'Sin leer todavía';
      const ago = timeAgo(s.refreshed_at);
      return ago[0].toUpperCase() + ago.slice(1);
    },

    statusInfo(s) {
      return SOURCE_STATUS[this.statuses[s.id]] || null;
    },

    // Si la cuenta de servicio todavía llega a la pestaña (sin bloquear la tabla).
    async checkStatus(s) {
      const { r, data } = await fetchJsonSafe(apiUrl(`/api/sources/${s.id}/status/`));
      if (r.ok && data) this.statuses = { ...this.statuses, [s.id]: data.status };
    },

    // «Actualizar»: relee la hoja de Google y recalcula el tablero.
    async refresh(s) {
      if (this.refreshing[s.id]) return;
      this.refreshing = { ...this.refreshing, [s.id]: true };
      this.refreshErrors = { ...this.refreshErrors, [s.id]: '' };
      const { r, data } = await fetchJsonSafe(apiUrl(`/api/sources/${s.id}/refresh/`), { method: 'POST' });
      if (data && 'status' in data) this.statuses = { ...this.statuses, [s.id]: data.status };
      this.refreshing = { ...this.refreshing, [s.id]: false };
      if (!r.ok || !data || data.error) {
        this.refreshErrors = { ...this.refreshErrors, [s.id]: (data && data.error) || 'No se pudo actualizar.' };
        return;
      }
      await this.afterChange();
    },

    sheetUrl(s) {
      return `https://docs.google.com/spreadsheets/d/${encodeURIComponent(s.sheet_id)}/edit#gid=${encodeURIComponent(s.gid)}`;
    },

    add() {
      this.view = 'picker';
      window.dispatchEvent(new CustomEvent('source-picker:start', { detail: { mode: 'add' } }));
    },

    changeSheet(source) {
      this.view = 'picker';
      window.dispatchEvent(new CustomEvent('source-picker:start', { detail: { mode: 'edit', source, changeSheet: true } }));
    },

    edit(source) {
      this.view = 'picker';
      window.dispatchEvent(new CustomEvent('source-picker:start', { detail: { mode: 'edit', source } }));
    },

    async remove(source) {
      const { r, data } = await fetchJsonSafe(apiUrl(`/api/sources/${source.id}/?dry_run=1`), { method: 'DELETE' });
      if (!r.ok || !data) {
        showToast('No se pudo eliminar la fuente.');
        return;
      }
      this.pendingDelete = { source, impact: data.impact || [] };
    },

    async confirmRemove() {
      const { source } = this.pendingDelete || {};
      if (!source) return;
      const r = await fetch(apiUrl(`/api/sources/${source.id}/`), { method: 'DELETE' }).catch(() => null);
      this.pendingDelete = null;
      if (!r || !r.ok) {
        showToast('No se pudo eliminar la fuente.');
        return;
      }
      this.afterChange();
    },

    // Ver datos transformados de una fuente en Tabulator dentro del bottom sheet
    async viewData(source) {
      this.previewSource = source;
      this.view = 'data';
      this.loadingData = true;
      this.dataError = '';
      this.previewColumns = [];
      this.totalRows = 0;
      this.loadedRows = 0;
      this.isFormResponse = false;
      if (this._previewTable) {
        this._previewTable.destroy();
        this._previewTable = null;
      }
      try {
        const { r, data } = await fetchJsonSafe(apiUrl(`/api/sources/${source.id}/rows/?page=1&size=1`));
        if (!r.ok || !data || data.error) throw new Error((data && data.error) || 'No se pudieron cargar los datos.');
        this.previewColumns = data.columns || [];
        this.totalRows = data.total_rows || 0;
        this.isFormResponse = !!data.is_form_response;
        this.loadingData = false;
        Alpine.nextTick(() => {
          setTimeout(() => this.renderPreviewTabulator(), 50);
        });
      } catch (e) {
        this.dataError = e.message || 'Error al cargar los datos de la fuente.';
        this.loadingData = false;
      }
    },

    renderPreviewTabulator() {
      const container = this.$refs.previewTabulatorHost;
      if (!container) return;
      if (this._previewTable) {
        this._previewTable.destroy();
        this._previewTable = null;
      }
      container.innerHTML = '';
      if (!this.previewColumns.length) return;

      const columns = this.previewColumns.map(col => ({
        title: col,
        field: col,
        headerHozAlign: 'left',
        hozAlign: 'left',
        minWidth: 100,
        maxInitialWidth: 300,
        resizable: true,
        tooltip: true,
      }));

      // La tabla pide al servidor un bloque de filas y, al llegar con el scroll al final, el
      // siguiente, que se suma a los ya cargados. El orden se aplica en el servidor, sobre
      // toda la fuente: al cambiarlo se vuelve a empezar desde el primer bloque.
      const rowsUrl = apiUrl(`/api/sources/${this.previewSource.id}/rows/`);
      this._previewTable = new Tabulator(container, {
        autoResize: false,
        nestedFieldSeparator: false,
        columns,
        layout: 'fitDataStretch',
        resizableColumnGuide: true,
        progressiveLoad: 'scroll',
        progressiveLoadScrollMargin: 200,
        sortMode: 'remote',
        paginationSize: 100,
        ajaxURL: rowsUrl,
        ajaxURLGenerator: (url, config, params) => {
          const query = new URLSearchParams({ page: params.page || 1, size: params.size || 100 });
          const sorter = (params.sort || [])[0];
          if (sorter) {
            query.set('sort', sorter.field);
            query.set('dir', sorter.dir);
          }
          return `${url}?${query}`;
        },
        ajaxResponse: (url, params, response) => {
          this.totalRows = response.total_rows || 0;
          return { last_page: response.last_page || 1, last_row: this.totalRows, data: response.rows || [] };
        },
        // La carga por scroll necesita una altura fija: la del contenedor.
        height: '100%',
      });
      this._previewTable.on('dataProcessed', () => {
        if (this._previewTable) this.loadedRows = this._previewTable.getDataCount();
      });

      this._previewTable.on('tableBuilt', () => {
        setTimeout(() => {
          if (this._previewTable) this._previewTable.redraw(true);
        }, 50);
      });
    },

    // Salta horizontalmente a la columna elegida y la resalta temporalmente
    jumpToColumn(colName) {
      if (!colName || !this._previewTable) return;
      try {
        const colComponent = this._previewTable.getColumn(colName);
        if (colComponent) {
          const scrollPromise = this._previewTable.scrollToColumn(colComponent, 'center', false);
          if (scrollPromise && typeof scrollPromise.then === 'function') {
            scrollPromise.then(() => {
              const holder = this._previewTable && this._previewTable.rowManager && this._previewTable.rowManager.element;
              const header = this._previewTable && this._previewTable.columnManager && this._previewTable.columnManager.headersElement;
              if (holder && header && header.scrollLeft !== holder.scrollLeft) {
                header.scrollLeft = holder.scrollLeft;
              }
            }).catch(() => {});
          }

          const headerEle = colComponent.getElement();
          if (headerEle) {
            headerEle.style.transition = 'all 0.3s ease';
            headerEle.style.backgroundColor = '#dcfce7';
            headerEle.style.boxShadow = 'inset 0 0 0 2px #4d7c0f';
            setTimeout(() => {
              headerEle.style.backgroundColor = '';
              headerEle.style.boxShadow = '';
            }, 2000);
          }
        }
      } catch (e) {
        console.error('Error al saltar a la columna:', e);
      }
      setTimeout(() => {
        this.selectedColumnJump = '';
      }, 50);
    },

    closeDataView() {
      if (this._previewTable) {
        this._previewTable.destroy();
        this._previewTable = null;
      }
      this.selectedColumnJump = '';
      this.previewSource = null;
      this.view = 'list';
    },

    // Otra lista de fuentes y otras columnas: el panel de los widgets y el lienzo se recalculan.
    async afterChange() {
      this.closeDataView();
      await this.load();
      const store = Alpine.store('dashboard');
      store.resetSources(this.sources.map(s => ({ id: s.id, label: s.label })));
      await store.refreshData();
      // El panel abierto vuelve a leer su widget: sus columnas pueden haberse renombrado.
      if (store.editingId != null) store.openDrawer(store.editingId);
    },
  };
}
