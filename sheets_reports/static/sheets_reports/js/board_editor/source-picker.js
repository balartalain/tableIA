// Selector de una fuente de datos (dentro del bottom sheet): fuente → documento de Drive →
// una pestaña → columnas con su tipo y su nombre a mostrar. Cuatro modos:
// - `create`: tablero nuevo; al confirmar crea el tablero (con esta fuente) y abre su editor.
// - `add`: agrega la fuente al tablero abierto.
// - `edit`: va directo a las columnas de una fuente existente y guarda sus tipos y nombres.
// - `replace`: «Cambiar hoja» desde `edit`: elige otra hoja para la fuente; sus widgets la
//   siguen usando y las columnas de igual encabezado conservan lo elegido en la edición.
//   «Cancelar» vuelve a la edición tal como estaba.
// Al editar o reemplazar, antes de guardar se pregunta al servidor qué widgets se romperían
// (`dry_run`) y, si hay alguno, se pide confirmación.
// «Actualizar datos» relee la pestaña de Google y muestra su estructura actual sin perder lo
// que el usuario ya cambió; se guarda con el resto.
// El gestor de fuentes lo arranca con el evento `source-picker:start` ({mode, source}) y
// recibe `sources:changed` al guardar (o al volver tras actualizar datos) y `sources:back`.

// «Actualizado hace 5 min»; sin fecha, vacío. Lo usan el selector y el gestor de fuentes.
function timeAgoLabel(iso) {
  if (!iso) return '';
  const minutes = Math.max(0, Math.round((Date.now() - new Date(iso)) / 60000));
  if (minutes < 1) return 'Actualizado ahora';
  if (minutes < 60) return `Actualizado hace ${minutes} min`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `Actualizado hace ${hours} h`;
  const days = Math.round(hours / 24);
  return `Actualizado hace ${days} ${days === 1 ? 'día' : 'días'}`;
}

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
    sourceName: '',
    headers: true,
    impact: null,
    emailCopied: false,
    refreshing: false,
    // Se releyó la hoja: al salir sin guardar, el tablero igual se recalcula con los datos nuevos.
    dataRefreshed: false,
    refreshedAt: null,
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
    _columnsRequest: 0,
    // Al cambiar de hoja, la edición de la que se partió (columnas, filas, encabezados).
    _editSnapshot: null,

    get includedCount() {
      return this.columns.filter(c => c.include).length;
    },

    get canSubmit() {
      return this.includedCount > 0 && (this.mode !== 'create' || this.name.trim() !== '');
    },

    get submitLabel() {
      if (this.saving) return this.mode === 'create' ? 'Creando…' : 'Guardando…';
      return { create: 'Crear tablero', add: 'Agregar fuente', edit: 'Guardar cambios',
               replace: 'Cambiar hoja' }[this.mode];
    },

    // Nombre que tendría la fuente sin uno propio: «Documento · Pestaña».
    get originalLabel() {
      if (this.mode === 'edit' && this.editing) return this.editing.original_label;
      return `${this.spreadsheet ? this.spreadsheet.name.trim() : ''} · ${this.tab ? this.tab.title : ''}`;
    },

    start({ mode, source = null }) {
      Object.assign(this, {
        mode, editing: source, step: 'source', source: null, query: '', spreadsheets: [],
        spreadsheet: null, tabs: [], tab: null, columns: [], rows: 0, name: '',
        sourceName: source ? source.name || '' : '',
        headers: source ? source.first_row_headers !== false : true, impact: null,
        refreshing: false, dataRefreshed: false, refreshedAt: source ? source.refreshed_at : null,
        sheetsError: '', tabsError: '', columnsError: '', createError: '', saving: false,
      });
      if (mode === 'edit') this.loadSavedColumns();
    },

    // Atrás: del paso de columnas a la elección de hoja; desde la hoja, al cambiarla, vuelve a
    // la edición; al editar (o desde la hoja, al agregar) vuelve a la tabla de fuentes.
    back() {
      if (this.impact) this.impact = null;
      else if (this.step === 'columns' && this.mode !== 'edit') this.step = 'source';
      else if (this.mode === 'replace') this._backToEdit();
      else window.dispatchEvent(new CustomEvent(this.dataRefreshed ? 'sources:changed' : 'sources:back'));
    },

    // `refresh`: relee la pestaña de Google y conserva lo que el usuario ya cambió.
    async loadSavedColumns({ refresh = false } = {}) {
      const request = ++this._columnsRequest;
      this.step = 'columns';
      this.loadingColumns = true;
      try {
        const data = await this._get(
          `/api/sources/${this.editing.id}/columns/?headers=${this.headers ? 1 : 0}${refresh ? '&refresh=1' : ''}`);
        if (request !== this._columnsRequest) return;
        this.columns = refresh ? this._keepEdits(data.columns || []) : data.columns || [];
        this.rows = data.rows || 0;
        this.refreshedAt = data.source ? data.source.refreshed_at : this.refreshedAt;
      } catch (e) {
        if (request === this._columnsRequest) this.createError = e.message;
      } finally {
        if (request === this._columnsRequest) this.loadingColumns = false;
      }
    },

    // «Actualizar datos»: la estructura actual de la hoja (columnas nuevas, quitadas o
    // renombradas en Google), sin perder los cambios todavía sin guardar.
    async refreshColumns() {
      if (this.refreshing) return;
      this.refreshing = true;
      this.createError = '';
      this.columnsError = '';
      if (this.mode === 'edit') await this.loadSavedColumns({ refresh: true });
      else await this.loadColumns({ refresh: true });
      this.refreshing = false;
      if (!this.createError && !this.columnsError) this.dataRefreshed = true;
    },

    // Columnas recién leídas con lo que el usuario ya eligió en las que siguen (por encabezado).
    _keepEdits(fresh, previous = this.columns) {
      const current = Object.fromEntries(previous.map(c => [c.name, c]));
      return fresh.map(c => {
        const edited = current[c.name];
        return edited ? { ...c, type: edited.type, include: edited.include, label: edited.label || '' }
          : { ...c, include: c.include ?? true, label: c.label || '' };
      });
    },

    // «Cambiar hoja»: elige otra hoja para la fuente, partiendo de lo que hay en la edición.
    changeSheet() {
      this._editSnapshot = { columns: this.columns, rows: this.rows, headers: this.headers };
      Object.assign(this, { mode: 'replace', step: 'source', impact: null, createError: '',
                            spreadsheet: null, tabs: [], tab: null });
      if (!this.source) this.selectSource('google');
    },

    _backToEdit() {
      const snapshot = this._editSnapshot || {};
      Object.assign(this, { mode: 'edit', step: 'columns', impact: null, createError: '',
                            columns: snapshot.columns || [], rows: snapshot.rows || 0,
                            headers: snapshot.headers ?? this.headers });
    },

    // «Usar la primera fila como encabezado»: otra lectura de la pestaña, otras columnas.
    toggleHeaders() {
      if (this.mode === 'edit') this.loadSavedColumns();
      else this.loadColumns();
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

    async loadColumns({ refresh = false } = {}) {
      if (!this.spreadsheet || !this.tab) return;
      const request = ++this._columnsRequest;
      this.loadingColumns = true;
      this.columnsError = '';
      try {
        const id = encodeURIComponent(this.spreadsheet.id);
        const gid = encodeURIComponent(this.tab.gid);
        const data = await this._get(`/api/sources/google/spreadsheets/${id}/tabs/${gid}/columns/`
          + `?headers=${this.headers ? 1 : 0}${refresh ? '&refresh=1' : ''}`);
        if (request !== this._columnsRequest) return;
        // Al actualizar se conserva lo que ya está en la tabla; al cambiar de hoja, lo que había
        // en la edición (las columnas de igual encabezado).
        if (refresh) this.columns = this._keepEdits(data.columns || []);
        else if (this.mode === 'replace') this.columns = this._keepEdits(data.columns || [], this._editSnapshot.columns);
        else this.columns = (data.columns || []).map(c => ({ ...c, include: true, label: '' }));
        this.rows = data.rows || 0;
        const doc = this.spreadsheet.name.trim();
        if (this.mode === 'create' && !refresh) {
          this.name = this.tabs.length > 1 && this.tab.title !== doc ? `${doc} — ${this.tab.title}` : doc;
        }
        this.createError = '';
        this.step = 'columns';
      } catch (e) {
        if (request === this._columnsRequest) this.columnsError = e.message;
      } finally {
        if (request === this._columnsRequest) this.loadingColumns = false;
      }
    },

    formatDate(iso) {
      return iso ? new Date(iso).toLocaleDateString('es', { day: 'numeric', month: 'short', year: 'numeric' }) : '';
    },

    async copyEmail(email) {
      try {
        await navigator.clipboard.writeText(email);
        this.emailCopied = true;
        setTimeout(() => { this.emailCopied = false; }, 2000);
      } catch (e) {
        // Sin permiso de portapapeles: el email sigue visible para copiarlo a mano.
      }
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

    _payload() {
      const columns = this.columns.map(({ name, type, include, label }) =>
        ({ name, type, include, label: (label || '').trim() }));
      const body = { columns, first_row_headers: this.headers };
      if (this.mode !== 'edit') {
        Object.assign(body, {
          sheet_id: this.spreadsheet.id,
          sheet_gid: this.tab.gid,
          sheet_name: this.spreadsheet.name.trim(),
          tab_name: this.tab.title,
        });
      }
      if (this.mode === 'edit' || this.mode === 'replace') body.name = this.sourceName.trim();
      return body;
    },

    // `confirmed`: el usuario ya vio qué widgets se rompen y eligió «Guardar igual».
    async submit({ confirmed = false } = {}) {
      if (!this.canSubmit || this.saving) return;
      this.saving = true;
      this.createError = '';
      const body = this._payload();
      try {
        if (this.mode === 'edit' || this.mode === 'replace') {
          const url = `/api/sources/${this.editing.id}/`;
          if (!confirmed) {
            const { impact } = await this._send('PUT', url, { ...body, dry_run: true });
            if (impact && impact.length) {
              this.impact = impact;
              this.saving = false;
              return;
            }
          }
          await this._send('PUT', url, body);
        } else if (this.mode === 'create') {
          const data = await this._send('POST', '/api/dashboards/', { nombre: this.name.trim(), ...body });
          window.location.replace(window.BOARD_EDITOR_URL.replace('/0/', `/${data.id}/`));
          return;
        } else {
          await this._send('POST', `/api/dashboard/${window.DASHBOARD_ID}/sources/`, body);
        }
        this.saving = false;
        this.impact = null;
        window.dispatchEvent(new CustomEvent('sources:changed'));
      } catch (e) {
        this.createError = e.message === 'Failed to fetch' ? 'No se pudo conectar con el servidor.' : e.message;
        this.saving = false;
      }
    },
  };
}
