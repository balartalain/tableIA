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
// Al editar, la pestaña «Campos calculados» define fórmulas sobre las columnas (por fila o
// agregadas, ver engine/formulas.py) armándolas con bloques (formula-builder.js), con una
// vista previa que calcula el servidor.
// El gestor de fuentes lo arranca con el evento `source-picker:start` ({mode, source}) y
// recibe `sources:changed` al guardar (o al volver tras actualizar datos) y `sources:back`.

// «hace 5 min»; sin fecha, vacío. Lo usan el selector y el gestor de fuentes.
function timeAgo(iso) {
  if (!iso) return '';
  const minutes = Math.max(0, Math.round((Date.now() - new Date(iso)) / 60000));
  if (minutes < 1) return 'ahora';
  if (minutes < 60) return `hace ${minutes} min`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `hace ${hours} h`;
  const days = Math.round(hours / 24);
  return `hace ${days} ${days === 1 ? 'día' : 'días'}`;
}

// «Actualizado hace 5 min»; sin fecha, vacío.
function timeAgoLabel(iso) {
  return iso ? `Actualizado ${timeAgo(iso)}` : '';
}

// Ancho (px) de la columna «Columna» de la tabla de columnas: lo ajusta el usuario arrastrando
// el borde del encabezado y se recuerda en este navegador.
const NAME_COLUMN_STORAGE_KEY = 'dataviz:source-name-column-width';
const NAME_COLUMN_WIDTH = { default: 288, min: 160, max: 960, step: 24 };

function storedNameColumnWidth() {
  try {
    const width = Number(localStorage.getItem(NAME_COLUMN_STORAGE_KEY));
    return width >= NAME_COLUMN_WIDTH.min && width <= NAME_COLUMN_WIDTH.max ? width : NAME_COLUMN_WIDTH.default;
  } catch (e) {
    return NAME_COLUMN_WIDTH.default;
  }
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
    // Formato de una columna numérica (lo heredan las métricas que la suman, promedian…).
    FORMATS: [
      { value: '', label: 'Número' },
      { value: 'currency', label: 'Moneda' },
      { value: 'percent', label: 'Porcentaje (%)' },
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
    // ¿La pestaña elegida proviene de un Google Forms? (null = aún sin
    // preguntar; el popup lo pregunta al confirmar la pestaña). Con «sí»
    // la fuente se marca como respuestas de formulario y el servidor la
    // sirve en modelo tidy: una fila por opción seleccionada.
    formQuestion: null,
    formPopup: false,
    impact: null,
    emailCopied: false,
    refreshing: false,
    // Se releyó la hoja: al salir sin guardar, el tablero igual se recalcula con los datos nuevos.
    dataRefreshed: false,
    refreshedAt: null,
    loadingSheets: false,
    loadingTabs: false,
    loadingColumns: false,
    generatingLabels: false,
    saving: false,
    sheetsError: '',
    // Login de Google y Google Picker: lo llena la plantilla (window.DATAVIZ_GOOGLE) desde la
    // app google_auth. Sin configurar, el selector sigue usando la cuenta de servicio.
    google: window.DATAVIZ_GOOGLE || { configured: false, connected: false, email: '',
                                       picker_api_key: '', picker_app_id: '' },
    googleError: '',
    _pickerSdk: null,
    tabsError: '',
    columnsError: '',
    generateLabelsError: '',
    createError: '',
    // Respuestas viejas (búsquedas o clics más rápidos que la red) no pisan a las nuevas.
    _sheetsRequest: 0,
    _tabsRequest: 0,
    _columnsRequest: 0,
    // Al cambiar de hoja, la edición de la que se partió (columnas, filas, encabezados).
    _editSnapshot: null,
    // Pestaña del paso de columnas al editar: 'columns' | 'calculated'.
    columnsTab: 'columns',
    // Lo escrito en «Buscar columna…»: filtra las filas de la tabla por el nombre de la columna.
    columnFilter: '',
    nameColumnWidth: storedNameColumnWidth(),
    // Campos calculados: {id, name, formula, format} + su árbol de bloques (_tree; _unreadable
    // si la fórmula guardada no se pudo leer; _rev cuenta sus cambios) y el estado de la vista
    // previa (_kind 'row'|'aggregated', _values, _error, _checking). Al servidor va el árbol
    // (él lo escribe como texto); `formula` es el último texto que devolvió. «Generar con IA»:
    // _aiPrompt, _aiLoading, _aiError (por campo).
    calculated: [],
    // Si el reemplazo de hoja empezó en la tabla de fuentes («Cambiar fuente») y no en la edición.
    replaceFromList: false,
    _previewTimers: {},
    // Constructor: el campo abierto, el lugar del árbol elegido con clic y el valor que se escribe.
    selectedId: null,
    activePaletteSection: 'columns',
    fbSelected: null,
    valueType: 'number',
    valueInput: '',
    columnQuery: '',
    // Piezas del panel del constructor y la barra de operadores, del catálogo del servidor.
    CONDITION_PIECES: [
      ...FormulaBlocks.catalog.functions.filter(f => f.group === 'condicion').map(({ name, label, title }) =>
        ({ label, title, tall: true, piece: FormulaBlocks.pieces.func(name) })),
      ...FormulaBlocks.catalog.compare.slice(0, 1).map(({ op, symbol }) => ({
        label: `[ ] ${symbol} [ ]`, piece: FormulaBlocks.pieces.operator(op),
        title: `Comparación: ${FormulaBlocks.catalog.compare.map(o => o.symbol).join(' ')}`,
      })),
      ...FormulaBlocks.catalog.logic.map(({ op, symbol, title }) => ({
        label: op === 'NOT' ? `${symbol} [ ]` : `[ ] ${symbol} [ ]`, title, piece: FormulaBlocks.pieces.operator(op),
      })),
    ],
    // Las funciones del catálogo en sus grupos de la paleta: las agregaciones, las de texto y las
    // de fecha (SI va con las piezas de condición). `tall`: las de varios huecos, que se dibujan
    // como bloque vertical (el color de SI).
    FUNCTION_GROUPS: [['Agregaciones', undefined], ['Funciones de Texto', 'texto'], ['Funciones de Fecha', 'fecha']].map(([title, group]) => ({
      title,
      pieces: FormulaBlocks.catalog.functions.filter(f => f.group === group).map(({ name, label, title, slots }) =>
        ({ label, title, tall: slots.length > 1, piece: FormulaBlocks.pieces.func(name) })),
    })),
    OPERATOR_PIECES: FormulaBlocks.catalog.arithmetic.map(({ op, symbol, title }) =>
      ({ label: symbol, title, piece: FormulaBlocks.pieces.operator(op) })),
    // Nombre a mostrar de cada columna la última vez que se miraron las fórmulas (para renombrar).
    _columnNames: {},

    // Al volver de conectar la cuenta de Google (`?google=connected`), el Picker se abre solo:
    // el callback agrega ese parámetro a la URL de vuelta. Se quita de la URL enseguida para que
    // un refresco no lo vuelva a abrir.
    init() {
      const url = new URL(window.location.href);
      if (url.searchParams.get('google') !== 'connected') return;
      url.searchParams.delete('google');
      window.history.replaceState({}, '', url.pathname + url.search + url.hash);
      if (!this.google.configured) return;
      this.google = { ...this.google, connected: true };
      this.source = 'google';
      if (this.mode !== 'create') {
        Alpine.store('bottomSheet').open({ title: 'Gestionar fuente de datos', view: 'sources' });
      }
      setTimeout(() => this.openGooglePicker(), 0);
    },

    get includedCount() {
      return this.columns.filter(c => c.include).length;
    },

    // La columna pasa el buscador: su nombre contiene lo escrito, sin mayúsculas ni acentos.
    matchesColumn(c) {
      const query = searchText(this.columnFilter);
      return !query || searchText(c.name).includes(query);
    },

    get shownColumns() {
      return this.columns.filter(c => this.matchesColumn(c));
    },

    // Fija el ancho de la columna «Columna» dentro de sus límites y lo recuerda.
    setNameColumnWidth(width) {
      this.nameColumnWidth = Math.round(Math.min(NAME_COLUMN_WIDTH.max, Math.max(NAME_COLUMN_WIDTH.min, width)));
      try { localStorage.setItem(NAME_COLUMN_STORAGE_KEY, String(this.nameColumnWidth)); } catch (e) {}
    },

    // Arrastrar el borde del encabezado «Columna» cambia su ancho.
    startNameColumnResize(event) {
      const startX = event.clientX;
      const startWidth = this.nameColumnWidth;
      const move = (e) => this.setNameColumnWidth(startWidth + e.clientX - startX);
      const stop = () => {
        window.removeEventListener('pointermove', move);
        document.body.classList.remove('select-none', 'cursor-col-resize');
      };
      event.preventDefault();
      document.body.classList.add('select-none', 'cursor-col-resize');
      window.addEventListener('pointermove', move);
      window.addEventListener('pointerup', stop, { once: true });
    },

    // Con el teclado: flechas para ajustar e Inicio para volver al ancho original.
    onNameColumnResizeKey(event) {
      const delta = { ArrowLeft: -NAME_COLUMN_WIDTH.step, ArrowRight: NAME_COLUMN_WIDTH.step }[event.key];
      if (delta) this.setNameColumnWidth(this.nameColumnWidth + delta);
      else if (event.key === 'Home') this.setNameColumnWidth(NAME_COLUMN_WIDTH.default);
      else return;
      event.preventDefault();
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

    // `changeSheet`: al editar, pasa directo a elegir otra hoja («Cambiar hoja» de la tabla de fuentes).
    start({ mode, source = null, changeSheet = false }) {
      Object.assign(this, {
        mode, editing: source, step: 'source', source: null,
        spreadsheet: null, tabs: [], tab: null, columns: [], rows: 0, name: '',
        sourceName: source ? source.name || '' : '',
        headers: source ? source.first_row_headers !== false : true, impact: null,
        refreshing: false, dataRefreshed: false, refreshedAt: source ? source.refreshed_at : null,
        columnsTab: 'columns', columnFilter: '', replaceFromList: changeSheet,
        // Al editar no se elige la hoja: el flag guardado es el que se
        // conserva (y se reenvía al guardar). Al cambiarla se vuelve a preguntar.
        formQuestion: source && mode === 'edit' ? !!source.is_form_response : null,
        formPopup: false,
        calculated: ((source && source.calculated_fields) || []).map(({ tree, ...f }) => ({
          ...f, _tree: tree || null, _unreadable: !tree && !!(f.formula || '').trim(), _rev: 0,
          _aiPrompt: '', _aiLoading: false, _aiError: '',
          _kind: null, _values: null, _error: '', _checking: false,
        })),
        fbSelected: null, valueInput: '', columnQuery: '',
        sheetsError: '', googleError: '', tabsError: '', columnsError: '', createError: '', saving: false,
      });
      this.selectedId = this.calculated.length ? this.calculated[0].id : null;
      // Cambiar de hoja parte de las columnas guardadas: se espera a tenerlas.
      if (mode === 'edit') this.loadSavedColumns().then(() => { if (changeSheet) this.changeSheet(); });
      FormulaBlocks.setupDrag();
    },

    // Atrás: del paso de columnas a la elección de hoja; desde la hoja, al cambiarla dentro de la
    // edición, vuelve a la edición; al editar, al agregar o al cambiar la hoja desde la tabla de
    // fuentes («Cambiar fuente»), vuelve a la tabla.
    back() {
      if (this.impact) this.impact = null;
      else if (this.step === 'columns' && this.mode !== 'edit') this.step = 'source';
      else if (this.mode === 'replace' && !this.replaceFromList) this._backToEdit();
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
        if (!refresh) this._columnNames = this._displayNames();
        this.calculated.forEach(f => this.previewCalculated(f, { now: true }));
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
      return fresh.map((c, i) => {
        const edited = current[c.name];
        const alias = (edited && edited.alias) || c.alias || `col_${i + 1}`;
        return edited ? { ...c, alias, type: edited.type, include: edited.include, label: edited.label || '', format: edited.format || '' }
          : { ...c, alias, include: c.include ?? true, label: c.label || '', format: c.format || '' };
      });
    },

    // «Cambiar hoja»: elige otra hoja para la fuente, partiendo de lo que hay en la edición.
    changeSheet() {
      this._editSnapshot = { columns: this.columns, rows: this.rows, headers: this.headers,
                              formQuestion: this.formQuestion };
      Object.assign(this, { mode: 'replace', step: 'source', impact: null, createError: '', columnsTab: 'columns', columnFilter: '',
                            spreadsheet: null, tabs: [], tab: null, formQuestion: null });
      if (!this.source) this.selectSource('google');
    },

    _backToEdit() {
      const snapshot = this._editSnapshot || {};
      Object.assign(this, { mode: 'edit', step: 'columns', impact: null, createError: '',
                            columns: snapshot.columns || [], rows: snapshot.rows || 0,
                            headers: snapshot.headers ?? this.headers,
                            formQuestion: snapshot.formQuestion ?? null });
    },

    // «Usar la primera fila como encabezado»: otra lectura de la pestaña, otras columnas.
    toggleHeaders() {
      if (this.mode === 'edit') this.loadSavedColumns();
      else this.loadColumns();
    },

    // «Generar nombres con IA»: analiza los nombres originales y sugiere nombres limpios/cortos en «Nombre a mostrar».
    async generateColumnLabels() {
      if (this.generatingLabels || !this.columns.length) return;
      this.generatingLabels = true;
      this.generateLabelsError = '';
      try {
        const columns = this.columns.filter(c => !c.readonly).map(c => c.name);
        const data = await this._send('POST', '/api/sources/suggest-column-labels/', { columns });
        const labels = (data && data.labels) || {};
        for (const col of this.columns) {
          if (col.readonly) continue;
          if (labels[col.name] !== undefined) {
            col.label = labels[col.name] !== col.name ? labels[col.name] : '';
          }
        }
      } catch (e) {
        this.generateLabelsError = e.message || 'Error al generar nombres';
      } finally {
        this.generatingLabels = false;
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
    },

    // --------------------------------------------- Google: login y selector nativo de Drive
    // Conectar la cuenta de Google: al terminar, el callback redirige de vuelta aquí.
    connectGoogle() {
      const next = window.location.pathname + window.location.search;
      window.location.href = apiUrl(`/auth/google/login/?next=${encodeURIComponent(next)}`);
    },

    // Abre el Google Picker con el token del usuario; al elegir una hoja se sigue con sus pestañas.
    async openGooglePicker() {
      this.googleError = '';
      if (!this.google.configured) {
        this.googleError = 'Google no está configurado en el servidor.';
        return;
      }
      this.loadingSheets = true;
      try {
        const status = await this._get('/auth/google/status/');
        if (!status.access_token) {
          this.connectGoogle();
          return;
        }
        this.google = { ...this.google, connected: true, email: status.email || this.google.email };
        await this._loadPickerSdk();
        const picker = new window.google.picker.PickerBuilder()
          .setOAuthToken(status.access_token)
          .setDeveloperKey(this.google.picker_api_key)
          .setAppId(this.google.picker_app_id)
          .setOrigin(window.location.protocol + '//' + window.location.host)
          .addView(window.google.picker.ViewId.SPREADSHEETS)
          .setCallback((data) => this.onGooglePicked(data))
          .build();
        picker.setVisible(true);
      } catch (e) {
        this.googleError = e.message || 'No se pudo abrir Google Drive.';
      } finally {
        this.loadingSheets = false;
      }
    },

    // El Picker devuelve el documento elegido: se trata igual que una hoja del listado.
    onGooglePicked(data) {
      const action = (window.google && window.google.picker && window.google.picker.Action) || {};
      if (!data || data.action !== action.PICKED) return;
      const doc = (data.docs || [])[0];
      if (!doc) return;
      this.selectSpreadsheet({
        id: doc.id,
        name: doc.name || '',
        modified: doc.lastEditedUtc ? new Date(doc.lastEditedUtc).toISOString() : null,
      });
    },

    // Carga la librería del Picker una sola vez (apis.google.com/js/api.js + gapi.load('picker')).
    _loadPickerSdk() {
      if (window.google && window.google.picker) return Promise.resolve();
      if (!this._pickerSdk) {
        this._pickerSdk = new Promise((resolve, reject) => {
          const script = document.createElement('script');
          script.src = 'https://apis.google.com/js/api.js';
          script.onload = resolve;
          script.onerror = () => reject(new Error('No se pudo cargar el selector de Google.'));
          document.head.appendChild(script);
        }).then(() => new Promise((resolve, reject) => {
          window.gapi.load('picker', { callback: resolve, onerror: reject });
        }));
      }
      return this._pickerSdk;
    },

    // Olvida la cuenta conectada (el servidor borra los tokens guardados).
    async disconnectGoogle() {
      try {
        await fetch(apiUrl('/auth/google/disconnect/'), { method: 'POST' });
      } catch (e) { /* sin conexión: igual se limpia el estado local */ }
      this.google = { ...this.google, connected: false, email: '' };
    },

    async selectSpreadsheet(s) {
      if (this.spreadsheet && this.spreadsheet.id === s.id) return;
      const request = ++this._tabsRequest;
      // Otra hoja: se vuelve a preguntar si proviene de un Google Forms.
      this.formQuestion = null;
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
      // Antes de procesar la hoja: ¿proviene de un Google Forms? Se
      // pregunta una vez por selección («Actualizar datos» y los
      // encabezados no vuelven a preguntar).
      if (this.formQuestion === null && !refresh) {
        this.formPopup = true;
        return;
      }
      const request = ++this._columnsRequest;
      this.loadingColumns = true;
      this.columnsError = '';
      try {
        const id = encodeURIComponent(this.spreadsheet.id);
        const gid = encodeURIComponent(this.tab.gid);
        const data = await this._get(`/api/sources/google/spreadsheets/${id}/tabs/${gid}/columns/`
          + `?headers=${this.headers ? 1 : 0}${this.formQuestion ? '&form=1' : ''}${refresh ? '&refresh=1' : ''}`);
        if (request !== this._columnsRequest) return;
        // Al actualizar se conserva lo que ya está en la tabla; al cambiar de hoja, lo que había
        // en la edición (las columnas de igual encabezado).
        if (refresh) this.columns = this._keepEdits(data.columns || []);
        else if (this.mode === 'replace') this.columns = this._keepEdits(data.columns || [], this._editSnapshot.columns);
        // Todas llegan incluidas salvo que el servidor diga lo contrario.
        else this.columns = (data.columns || []).map((c, i) => ({ ...c, alias: c.alias || `col_${i + 1}`, include: c.include ?? true, label: '', format: '' }));
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

    // Popup «¿Proviene de un Google Forms?»: con «sí» la fuente se marca
    // como respuestas de formulario (transformación tidy en el servidor);
    // con «no» se lee como hoja normal, sin transformación.
    answerFormOrigin(isForm) {
      this.formQuestion = isForm;
      this.formPopup = false;
      this.loadColumns();
    },

    formatDate(iso) {
      return iso ? new Date(iso).toLocaleDateString('es', { day: 'numeric', month: 'short', year: 'numeric' }) : '';
    },

    // ---- campos calculados
    // Nombres que una fórmula puede usar: las columnas incluidas (con su nombre a mostrar). Las
    // virtuales de una cuadrícula no: solo existen al desdoblarla, en un widget.
    get formulaColumns() {
      return this.columns.filter(c => c.include && !c.virtual).map(c => (c.label || '').trim() || c.name);
    },

    get selectedField() {
      return this.calculated.find(f => f.id === this.selectedId) || null;
    },

    // Las columnas del panel del constructor: las de la hoja y los campos por fila anteriores.
    get builderColumns() {
      const field = this.selectedField;
      const index = field ? this.calculated.indexOf(field) : 0;
      const earlier = this.calculated.slice(0, index)
        .filter(f => f._kind === 'row' && f.name.trim()).map(f => f.name.trim());
      return [...this.formulaColumns, ...earlier];
    },

    get filteredBuilderColumns() {
      const q = this.columnQuery.trim().toLowerCase();
      return q ? this.builderColumns.filter(c => c.toLowerCase().includes(q)) : this.builderColumns;
    },

    columnPiece(name) {
      return FormulaBlocks.pieces.column(name);
    },

    // La ficha «Vacío» del subpanel «Valores»: deja la celda en blanco (resultado de un SI).
    EMPTY_PIECE: { ...FormulaBlocks.emptyInfo, piece: FormulaBlocks.pieces.empty() },

    // La ficha del subpanel «Valores», o null si lo escrito no vale.
    get valuePiece() {
      const raw = this.valueInput;
      if (this.valueType === 'text') {
        return raw.includes('"') && raw.includes("'") ? null : FormulaBlocks.pieces.text(raw);
      }
      const number = raw.trim().replace(',', '.');
      return /^-?\d+(\.\d+)?$/.test(number) ? FormulaBlocks.pieces.number(Number(number)) : null;
    },

    get valueError() {
      if (this.valueType === 'text') return this.valuePiece ? '' : 'Un texto no puede llevar comillas dobles y simples a la vez.';
      return this.valueInput.trim() && !this.valuePiece ? 'Escribe un número, ej. 100 o 1.5' : '';
    },

    addCalculated() {
      const id = `cf_${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
      this.calculated.push({ id, name: '', formula: '', format: 'number', _tree: null, _unreadable: false, _rev: 0,
                             _aiPrompt: '', _aiLoading: false, _aiError: '',
                             _kind: null, _values: null, _error: '', _checking: false });
      this.selectField(id);
    },

    removeCalculated(index) {
      const [removed] = this.calculated.splice(index, 1);
      if (removed && removed.id === this.selectedId) {
        const next = this.calculated[Math.min(index, this.calculated.length - 1)];
        this.selectField(next ? next.id : null);
      }
    },

    selectField(id) {
      this.selectedId = id;
      this.fbSelected = null;
    },

    togglePaletteSection(section) {
      this.activePaletteSection = this.activePaletteSection === section ? '' : section;
    },

    // «Campos calculados»: antes de mostrar los bloques, siguen a las columnas renombradas.
    openCalculated() {
      this.columnsTab = 'calculated';
      this.syncColumnNames();
    },

    _displayNames() {
      return Object.fromEntries(this.columns.map(c => [c.name, (c.label || '').trim() || c.name]));
    },

    syncColumnNames() {
      const now = this._displayNames();
      const mapping = {};
      Object.entries(this._columnNames).forEach(([name, before]) => {
        if (now[name] && now[name] !== before) mapping[before] = now[name];
      });
      this._columnNames = now;
      if (Object.keys(mapping).length) this._renameInTrees(mapping);
    },

    // Un campo calculado renombrado: las fórmulas que lo usan lo siguen.
    renameField(field) {
      const before = (field._nameBefore || '').trim();
      const after = field.name.trim();
      if (before && after && before !== after) this._renameInTrees({ [before]: after });
    },

    _renameInTrees(mapping) {
      this.calculated.forEach(f => {
        if (!f._tree) return;
        FormulaBlocks.renameColumns(f._tree, mapping);
        this.treeChanged(f);
      });
    },

    // Dibuja los bloques del campo abierto (lo llama un x-effect: se repite al cambiar el árbol).
    renderCanvas(container) {
      const field = this.selectedField;
      if (!field) return;
      FormulaBlocks.render(container, field._tree, this.fbSelected, {
        select: path => { if (!FormulaBlocks.justDragged()) this.fbSelected = path; },
        remove: path => this._setTree(field, FormulaBlocks.setAt(field._tree, path, null), path),
        setOp: (path, op) => {
          FormulaBlocks.getAt(field._tree, path).value = op;
          this.treeChanged(field);
        },
      });
    },

    // Clic en una pieza del panel: va al lugar elegido (o a la raíz).
    clickPiece(piece) {
      const field = this.selectedField;
      if (!field || FormulaBlocks.justDragged() || !piece) return;
      this._placePiece(field, FormulaBlocks.clone(piece), this.fbSelected || []);
    },

    // Fin de un arrastre (evento `formula:drop` de formula-builder.js).
    onFormulaDrop({ source, target }) {
      const field = this.selectedField;
      if (!field) return;
      if (source.piece) {
        if (!target.trash) this._placePiece(field, source.piece, target.path);
        return;
      }
      // Un bloque del canvas: al panel se quita; no cae dentro de sí mismo.
      if (target.trash) {
        this._setTree(field, FormulaBlocks.setAt(field._tree, source.path, null), source.path);
        return;
      }
      if (FormulaBlocks.isInside(target.path, source.path)) return;
      const moved = FormulaBlocks.clone(FormulaBlocks.getAt(field._tree, source.path));
      const root = FormulaBlocks.setAt(field._tree, source.path, null);
      this._placePiece(field, moved, target.path, root);
    },

    _placePiece(field, piece, path, root = field._tree) {
      const placed = FormulaBlocks.place(root, path, piece);
      this._setTree(field, placed.root, placed.path);
    },

    // Árbol nuevo: queda elegido el siguiente hueco para seguir armando con clics.
    _setTree(field, root, path = []) {
      field._tree = root;
      field._unreadable = false;
      this.fbSelected = root == null ? null : FormulaBlocks.nextHole(root, path);
      this.treeChanged(field);
    },

    // Cambió el árbol: su vista previa (que trae el texto que escribe el servidor).
    treeChanged(field) {
      field._rev += 1;
      this.previewCalculated(field);
    },

    isIncomplete(field) {
      return !!field._tree && FormulaBlocks.hasHoles(field._tree);
    },

    // Lo que se manda de un campo: su árbol o, si es una fórmula guardada ilegible que no se tocó,
    // su texto. Null si no hay nada que calcular (vacío o con huecos).
    _formulaBody(field) {
      if (field._unreadable) return { formula: field.formula };
      return FormulaBlocks.hasHoles(field._tree) ? null : { tree: field._tree };
    },

    // Vista previa (con pausa mientras se arma): tipo del campo, primeros valores o error.
    previewCalculated(field, { now = false } = {}) {
      clearTimeout(this._previewTimers[field.id]);
      if (!this._formulaBody(field)) {
        Object.assign(field, { _kind: null, _values: null, _error: '', _checking: false });
        return;
      }
      field._checking = true;
      this._previewTimers[field.id] = setTimeout(() => this._runPreview(field), now ? 0 : 400);
    },

    // La hoja como está en el editor, sin guardar, para un campo: encabezados, columnas y los
    // campos calculados anteriores a él (vista previa y «Generar con IA»).
    _draftBody(field) {
      const index = this.calculated.indexOf(field);
      const previous = this.calculated.slice(0, Math.max(index, 0))
        .filter(f => f.name.trim() && this._formulaBody(f))
        .map(f => ({ id: f.id, name: f.name.trim(), format: f.format, ...this._formulaBody(f) }));
      return {
        first_row_headers: this.headers, calculated_fields: previous,
        columns: this.columns.map(({ name, type, include, label, alias, ...c }) => ({ name, type, include, alias: alias || '', label: (label || '').trim(), ...this._gridKeys(c) })),
      };
    },

    // «Generar con IA»: la fórmula que arma la IA reemplaza lo que haya en el lienzo; si no
    // pudo, queda su motivo y el lienzo no cambia.
    async generateWithAI(field) {
      const prompt = field._aiPrompt.trim();
      if (!prompt || field._aiLoading) return;
      Object.assign(field, { _aiLoading: true, _aiError: '' });
      try {
        const data = await this._send('POST', `/api/sources/${this.editing.id}/formula/ai/`,
          { prompt, ...this._draftBody(field) });
        if (data.error) {
          field._aiError = data.error;
          return;
        }
        if (!field.name.trim() && data.name) field.name = data.name;
        field._aiPrompt = '';
        this._setTree(field, data.tree);
      } catch (e) {
        field._aiError = e.message === 'Failed to fetch' ? 'No se pudo conectar con el servidor.' : e.message;
      } finally {
        field._aiLoading = false;
      }
    },

    async _runPreview(field) {
      const rev = field._rev;
      const body = this._formulaBody(field);
      if (!body) return;
      try {
        const data = await this._send('POST', `/api/sources/${this.editing.id}/formula/`,
          { ...body, ...this._draftBody(field) });
        if (field._rev !== rev) return;   // se siguió armando: manda la próxima
        if (data.formula) field.formula = data.formula;
        Object.assign(field, { _kind: data.kind || null, _values: data.values || null, _error: data.error || '' });
      } catch (e) {
        if (field._rev === rev) field._error = e.message;
      } finally {
        if (field._rev === rev) field._checking = false;
      }
    },

    previewText(field) {
      const show = (v, options = {}) => (typeof v === 'number' ? formatNumber(v, options) : (v == null ? '—' : String(v)));
      if (!field._values) return '';
      if (field._kind === 'aggregated') {
        return `Toda la hoja: ${show(field._values[0], { percent: field.format === 'percent' })}`;
      }
      return `Primeras filas: ${field._values.map(show).join(' · ')}`;
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

    // Una virtual de cuadrícula guarda su pregunta y su rol (Aspecto o Respuesta).
    _gridKeys(c) {
      return c.virtual ? { grid: c.grid, role: c.role } : {};
    },

    _payload() {
      // La escala de medida (y el orden de una ordinal) se edita desde la matriz de
      // correlación: aquí solo se conserva.
      const columns = this.columns.map(({ name, type, include, label, format, alias, scale, order, ...c }) =>
        ({ name, type, include, alias: alias || '', label: (label || '').trim(), format: type === 'number' ? (format || '') : '',
           ...(scale ? { scale, ...(scale === 'ordinal' && order && order.length ? { order } : {}) } : {}),
           ...this._gridKeys(c) }));
      const body = { columns, first_row_headers: this.headers };
      // ¿Respuestas de Google Forms? (null en edición directa no debería
      // pasar: arranca con el valor guardado de la fuente).
      if (this.formQuestion !== null) body.is_form_response = this.formQuestion;
      if (this.mode !== 'edit') {
        Object.assign(body, {
          sheet_id: this.spreadsheet.id,
          sheet_gid: this.tab.gid,
          sheet_name: this.spreadsheet.name.trim(),
          tab_name: this.tab.title,
        });
      }
      if (this.mode === 'edit' || this.mode === 'replace') {
        body.name = this.sourceName.trim();
        // Va el árbol (el servidor escribe la fórmula); una fórmula ilegible sin tocar, su texto.
        body.calculated_fields = this.calculated
          .filter(f => f.name.trim() || f._tree || f._unreadable)
          .map(f => ({
            id: f.id, name: f.name.trim(), format: f.format || 'number',
            ...(f._unreadable ? { formula: f.formula.trim() } : { tree: f._tree }),
          }));
      }
      return body;
    },

    // `confirmed`: el usuario ya vio qué widgets se rompen y eligió «Guardar igual».
    async submit({ confirmed = false } = {}) {
      if (!this.canSubmit || this.saving) return;
      const incomplete = this.calculated.find(f => this.isIncomplete(f));
      if (incomplete) {
        this.createError = `Completa los huecos de «${incomplete.name.trim() || 'Campo sin nombre'}».`;
        this.columnsTab = 'calculated';
        this.selectField(incomplete.id);
        return;
      }
      this.syncColumnNames();
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
          const created = await this._send('POST', `/api/dashboard/${window.DASHBOARD_ID}/sources/`, body);
          // Widgets de una fuente eliminada que valen en esta: el servidor los reconectó.
          const n = (created && created.reattached) || 0;
          if (n && typeof window.showToast === 'function') {
            window.showToast(n === 1 ? 'Se reconectó 1 widget a esta fuente.' : `Se reconectaron ${n} widgets a esta fuente.`);
          }
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
