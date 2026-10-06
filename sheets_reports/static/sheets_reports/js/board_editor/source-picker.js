// Selector de la fuente de datos de un tablero nuevo (dentro del bottom sheet):
// fuente → documento de Drive → una pestaña → columnas con su tipo. Al confirmar crea el
// tablero y abre su editor.
function sourcePicker() {
  return {
    TYPES: [
      { value: 'text', label: 'Texto' },
      { value: 'number', label: 'Número' },
      { value: 'date', label: 'Fecha' },
    ],
    step: 'source',
    source: null,
    query: '',
    spreadsheets: [],
    spreadsheet: null,
    tabs: [],
    tab: null,
    columns: [],
    rows: 0,
    name: '',
    loadingSheets: false,
    loadingTabs: false,
    loadingColumns: false,
    creating: false,
    sheetsError: '',
    tabsError: '',
    columnsError: '',
    createError: '',
    // Respuestas viejas (búsquedas o clics más rápidos que la red) no pisan a las nuevas.
    _sheetsRequest: 0,
    _tabsRequest: 0,

    get includedCount() {
      return this.columns.filter(c => c.include).length;
    },

    get canCreate() {
      return this.includedCount > 0 && this.name.trim() !== '';
    },

    async _get(path) {
      const { r, data } = await fetchJsonSafe(apiUrl(path));
      if (!r.ok || !data) throw new Error((data && data.error) || `Error ${r.status}`);
      return data;
    },

    selectSource(source) {
      if (this.source === source) return;
      this.source = source;
      this.loadSpreadsheets();
    },

    async loadSpreadsheets() {
      const request = ++this._sheetsRequest;
      this.loadingSheets = true;
      this.sheetsError = '';
      try {
        const q = encodeURIComponent(this.query.trim());
        const data = await this._get(`/api/sources/google/spreadsheets/${q ? `?q=${q}` : ''}`);
        if (request === this._sheetsRequest) this.spreadsheets = data.spreadsheets || [];
      } catch (e) {
        if (request === this._sheetsRequest) {
          this.spreadsheets = [];
          this.sheetsError = e.message;
        }
      } finally {
        if (request === this._sheetsRequest) this.loadingSheets = false;
      }
    },

    async selectSpreadsheet(s) {
      if (this.spreadsheet && this.spreadsheet.id === s.id) return;
      const request = ++this._tabsRequest;
      this.spreadsheet = s;
      this.tabs = [];
      this.tab = null;
      this.tabsError = '';
      this.columnsError = '';
      this.loadingTabs = true;
      try {
        const data = await this._get(`/api/sources/google/spreadsheets/${encodeURIComponent(s.id)}/tabs/`);
        if (request !== this._tabsRequest) return;
        this.tabs = data.tabs || [];
        // Una sola pestaña: queda elegida.
        if (this.tabs.length === 1) this.tab = this.tabs[0];
      } catch (e) {
        if (request === this._tabsRequest) this.tabsError = e.message;
      } finally {
        if (request === this._tabsRequest) this.loadingTabs = false;
      }
    },

    async loadColumns() {
      if (!this.spreadsheet || !this.tab) return;
      this.loadingColumns = true;
      this.columnsError = '';
      try {
        const id = encodeURIComponent(this.spreadsheet.id);
        const gid = encodeURIComponent(this.tab.gid);
        const data = await this._get(`/api/sources/google/spreadsheets/${id}/tabs/${gid}/columns/`);
        this.columns = (data.columns || []).map(c => ({ ...c, include: true }));
        this.rows = data.rows || 0;
        const doc = this.spreadsheet.name.trim();
        this.name = this.tabs.length > 1 && this.tab.title !== doc ? `${doc} — ${this.tab.title}` : doc;
        this.createError = '';
        this.step = 'columns';
      } catch (e) {
        this.columnsError = e.message;
      } finally {
        this.loadingColumns = false;
      }
    },

    formatDate(iso) {
      return iso ? new Date(iso).toLocaleDateString('es', { day: 'numeric', month: 'short', year: 'numeric' }) : '';
    },

    async create() {
      if (!this.canCreate || this.creating) return;
      this.creating = true;
      this.createError = '';
      try {
        const r = await fetch(apiUrl('/api/dashboards/'), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            nombre: this.name.trim(),
            sheet_id: this.spreadsheet.id,
            sheet_gid: this.tab.gid,
            sheet_name: this.spreadsheet.name.trim(),
            tab_name: this.tab.title,
            columns: this.columns.map(({ name, type, include }) => ({ name, type, include })),
          }),
        });
        const data = await r.json().catch(() => null);
        if (!r.ok || !data) throw new Error((data && data.error) || `Error ${r.status}`);
        window.location.replace(window.BOARD_EDITOR_URL.replace('/0/', `/${data.id}/`));
      } catch (e) {
        this.createError = e.message === 'Failed to fetch' ? 'No se pudo conectar con el servidor.' : e.message;
        this.creating = false;
      }
    },
  };
}
