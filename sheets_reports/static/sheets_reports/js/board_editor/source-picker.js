// Selector de una fuente de datos (dentro del bottom sheet): fuente → documento de Drive →
// una pestaña → columnas con su tipo. Tres modos:
// - `create`: tablero nuevo; al confirmar crea el tablero (con esta fuente) y abre su editor.
// - `add`: agrega la fuente al tablero abierto.
// - `edit`: va directo a las columnas de una fuente existente y guarda sus tipos.
// El gestor de fuentes lo arranca con el evento `source-picker:start` ({mode, source}) y
// recibe `sources:changed` al guardar y `sources:back` al volver.
function sourcePicker({ mode = 'create' } = {}) {
  return {
    mode,
    editing: null,
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
    saving: false,
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

    get canSubmit() {
      return this.includedCount > 0 && (this.mode !== 'create' || this.name.trim() !== '');
    },

    get submitLabel() {
      if (this.saving) return this.mode === 'create' ? 'Creando…' : 'Guardando…';
      return { create: 'Crear tablero', add: 'Agregar fuente', edit: 'Guardar cambios' }[this.mode];
    },

    start({ mode, source = null }) {
      Object.assign(this, {
        mode, editing: source, step: 'source', source: null, query: '', spreadsheets: [],
        spreadsheet: null, tabs: [], tab: null, columns: [], rows: 0, name: '',
        sheetsError: '', tabsError: '', columnsError: '', createError: '', saving: false,
      });
      if (mode === 'edit') this.loadSavedColumns(source);
    },

    // Atrás: del paso de columnas a la elección de hoja; al editar (o desde la hoja, al
    // agregar) vuelve a la tabla de fuentes.
    back() {
      if (this.step === 'columns' && this.mode !== 'edit') this.step = 'source';
      else window.dispatchEvent(new CustomEvent('sources:back'));
    },

    async loadSavedColumns(source) {
      this.step = 'columns';
      this.loadingColumns = true;
      try {
        const data = await this._get(`/api/sources/${source.id}/columns/`);
        this.columns = data.columns || [];
        this.rows = data.rows || 0;
      } catch (e) {
        this.createError = e.message;
      } finally {
        this.loadingColumns = false;
      }
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

    async _send(method, path, body) {
      const r = await fetch(apiUrl(path), {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data = await r.json().catch(() => null);
      if (!r.ok || !data) throw new Error((data && data.error) || `Error ${r.status}`);
      return data;
    },

    async submit() {
      if (!this.canSubmit || this.saving) return;
      this.saving = true;
      this.createError = '';
      const columns = this.columns.map(({ name, type, include }) => ({ name, type, include }));
      try {
        if (this.mode === 'edit') {
          await this._send('PUT', `/api/sources/${this.editing.id}/`, { columns });
        } else {
          const sheet = {
            sheet_id: this.spreadsheet.id,
            sheet_gid: this.tab.gid,
            sheet_name: this.spreadsheet.name.trim(),
            tab_name: this.tab.title,
            columns,
          };
          if (this.mode === 'create') {
            const data = await this._send('POST', '/api/dashboards/', { nombre: this.name.trim(), ...sheet });
            window.location.replace(window.BOARD_EDITOR_URL.replace('/0/', `/${data.id}/`));
            return;
          }
          await this._send('POST', `/api/dashboard/${window.DASHBOARD_ID}/sources/`, sheet);
        }
        this.saving = false;
        window.dispatchEvent(new CustomEvent('sources:changed'));
      } catch (e) {
        this.createError = e.message === 'Failed to fetch' ? 'No se pudo conectar con el servidor.' : e.message;
        this.saving = false;
      }
    },
  };
}
