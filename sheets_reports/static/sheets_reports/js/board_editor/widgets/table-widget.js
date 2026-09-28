(function () {
    const formattersMap = {
      "text": { hozAlign: "left", formatter: "plaintext" },
      "currency": { hozAlign: "right", formatter: "money", formatterParams: { precision: 2, thousand: "," } },
      "percent": {
          hozAlign: "right",
          formatter: (cell) => cell.getValue() != null ? Number(cell.getValue()) + "%" : "-"
      },
      "progress": {
          formatter: "progress",
          hozAlign: "left",
          formatterParams: {
            min: 0, max: 100,
            color: ["#ef4444", "#f59e0b", "#10b981"],
            legend: function(value) { return Number(value) + "%"; }
          }
      }
  };
  class TableWidget extends BaseWidget {
    static type = 'table';
    static palette = {
      icon: '<svg viewBox="0 0 20 20" width="1.25rem" height="1.25rem" class="inline-block" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="2.5" y="3.5" width="15" height="13" rx="1.5"/><line x1="2.5" y1="8" x2="17.5" y2="8"/><line x1="2.5" y1="12.5" x2="17.5" y2="12.5"/><line x1="8.5" y1="3.5" x2="8.5" y2="16.5"/></svg>',
      label: 'Tabla',
      description: 'Filas y columnas de datos',
      chipClass: 'bg-amber-50/60 border border-amber-200 hover:bg-amber-100/80',
      titleClass: 'text-amber-950',
      descClass: 'text-amber-700/80',
    };
    static defaults = { title: 'Tabla', width: 'md:col-span-6', height: 300 };
    static pivotLabel = 'Agregar columnas por';
    static help = 'Muestra datos en filas y columnas, como una hoja de cálculo (ej. listado de ' +
      'participantes con sus notas, detalle de transacciones). Útil cuando el detalle fila por ' +
      'fila importa más que una comparación visual, y permite descargar los datos como CSV.';

    static FIELD_PAGE_SIZE = { key: 'pageSize', label: 'Filas por página', type: 'number', min: 5, step: 5 };
    static FIELD_SHOW_PAGINATION = { key: 'showPagination', label: 'Mostrar paginación', type: 'checkbox' };

    static get drawerFields() {
      return [...super.drawerFields,
        this.FIELD_PAGE_SIZE,
        this.FIELD_SHOW_PAGINATION,
        { key: 'boldLastRow', label: 'Resaltar última fila', type: 'checkbox' }
      ];
    }

    static mockData() {
      return {
        columns: [
          { header: 'Producto', field: 'Producto' },
          { header: 'Vendedor', field: 'Vendedor' },
          { header: 'Ventas', field: 'Ventas' },
        ],
        rows: [
          { Producto: 'Producto A', Vendedor: 'Cajero 1', Ventas: 14200 },
          { Producto: 'Producto B', Vendedor: 'Cajero 2', Ventas: 19800 },
          { Producto: 'Producto C', Vendedor: 'Cajero 3', Ventas: 8500 },
        ],
      };
    }

    constructor(raw) {
      super(raw);
      this.pageSize = raw.pageSize ?? 10;
      this.showPagination = raw.showPagination ?? true;
      this.boldLastRow = raw.boldLastRow ?? false;
      this.columnOrder = raw.columnOrder ?? null;
      this.formattersMap = raw.formattersMap ?? {};
    }

    getProperties() {
      return { ...super.getProperties(), pageSize: this.pageSize, showPagination: this.showPagination,
        boldLastRow: this.boldLastRow, columnOrder: this.columnOrder, formattersMap: this.formattersMap };
    }

    buildElement() {
      return this.buildStandardCardElement();
    }

    buildReadOnlyElement() {
      this._readOnly = true;
      const el = super.buildReadOnlyElement();
      el.querySelector('.actions-slot').innerHTML = this.downloadButtonHTML('Descargar CSV');
      return el;
    }
    applyFormatter(fieldName, tipoFormato) {
      const config = formattersMap[tipoFormato] || formattersMap["text"];
      const update = (cols) => cols.map(col => {
          if (col.columns) return { ...col, columns: update(col.columns) }; // grupo de pivote
          if (col.field === fieldName) return { ...col, ...config };
          return col;
      });
      const cols = update(this._table.getColumnDefinitions());
      this.formattersMap[fieldName] = tipoFormato; // Guarda el formato aplicado para persistencia
      this._table.setColumns(cols); // Re-renderiza las columnas instantáneamente
      this._dirty = true;
      const store = window.Alpine && Alpine.store('dashboard');
      if (store && typeof store._saveWidget === 'function') {
        store._saveWidget(this);
      }
    }
    menuFormatter = [
      { label: "📄 Texto", action: (e, column) => this.applyFormatter(column.getField(), "text") },
      { label: "💲 Moneda / Número", action: (e, column) => this.applyFormatter(column.getField(), "currency") },
      { label: "📊 Porcentaje (%)", action: (e, column) => this.applyFormatter(column.getField(), "percent") },
      { label: "🔋 Barra de Progreso", action: (e, column) => this.applyFormatter(column.getField(), "progress") }
    ];
    renderContent(container, data) {
      const payload = data || this.constructor.mockData();
      if (this._table) {
        this._table.destroy();
        this._table = null;
      }
      container.innerHTML = '';
      container.style.backgroundColor = '#fff';
      let columns = payload.columns || [];
      const hasGroups = columns.some(c => c.children);
      if (!hasGroups && this.columnOrder && this.columnOrder.length) {
        const byField = new Map(columns.map(c => [c.field, c]));
        const ordered = this.columnOrder.map(f => byField.get(f)).filter(Boolean);
        const remaining = columns.filter(c => !this.columnOrder.includes(c.field));
        columns = [...ordered, ...remaining];
      }
      // {header, field} / {header, children} (formato de compile_view) -> columnas de Tabulator.
      const toTabulator = (col) => {
        if (col.children) return { title: col.header, columns: col.children.map(toTabulator) };
        const formatterConfig = formattersMap[this.formattersMap[col.field]] || formattersMap["text"];
        const result = { title: col.header, field: col.field, ...formatterConfig };
        if (!this._readOnly) result.headerMenu = this.menuFormatter;
        return result;
      };
      columns = columns.map(toTabulator);
      this._table = new Tabulator(container, {
        data: payload.rows || [],
        // Los campos del pivote ("__pivots.Ene.total_ventas") son claves planas, no rutas.
        nestedFieldSeparator: false,
        movableColumns: !hasGroups,
        columns,
        layout: 'fitDataStretch',
        pagination: this.showPagination,
        paginationSize: this.pageSize,
        height: '100%',
        rowFormatter: (row)=> {
          // Quitamos la clase por defecto para evitar residuos al alternar el checkbox
          row.getElement().classList.remove("tabulator-row-bold");

          // Si la opción está activa y es la última fila del set de datos actual
          if (this.boldLastRow) {
            const todasLasFilas = row.getTable().getRows("active"); // Obtiene las filas activas/filtradas
            const ultimaFila = todasLasFilas[todasLasFilas.length - 1];

            // Si la fila actual que se está dibujando es idéntica a la última fila
            if (ultimaFila && row.getPosition() === ultimaFila.getPosition()) {
              row.getElement().classList.add("tabulator-row-bold");
            }
          }
        }
      });
      this._table.on("columnMoved", (_column, columns) => {
        this.columnOrder = columns.map(col => col.getField());
        this._dirty = true;
        const store = window.Alpine && Alpine.store('dashboard');
        if (store && typeof store._saveWidget === 'function') {
          store._saveWidget(this);
        }
      });
      const downloadBtn = this.el && this.el.querySelector('.download-csv-btn');
      if (downloadBtn) {
        downloadBtn.onclick = () => {
          this._table.download('csv', `${this._filenameSlug('tabla')}.csv`);
        };
      }
    }

    destroy() {
      if (this._table) {
        this._table.destroy();
        this._table = null;
      }
      super.destroy();
    }
  }

  WidgetRegistry.register(TableWidget);
})();
