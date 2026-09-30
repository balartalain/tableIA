(function () {
    const formattersMap = {
      "text": { hozAlign: "left", formatter: "plaintext" },
      // Formato por defecto de las columnas de valores: a la derecha, con separador de miles.
      "number": {
          hozAlign: "right",
          formatter: (cell) => {
            const value = cell.getValue();
            if (value == null || value === '') return "-";
            return typeof value === 'number' ? value.toLocaleString(undefined, { maximumFractionDigits: 2 }) : value;
          }
      },
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
    static pivotLabel = 'Columnas';
    static dimensionLabel = 'Filas';
    static maxDimensions = 3;
    static maxPivots = 2;
    // Cada métrica es una columna con cabecera: se puede renombrar.
    static supportsLabels = true;
    // "Mostrar totales" por cada nivel de filas/columnas, como en las tablas dinámicas de Sheets.
    static supportsTotals = true;
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
          { header: 'Unidades', field: 'Unidades' },
          { header: 'Ventas', field: 'Ventas' },
        ],
        rows: [
          { Producto: 'Producto A', Unidades: 42, Ventas: 14200 },
          { Producto: 'Producto B', Unidades: 57, Ventas: 19800 },
          { Producto: 'Producto C', Unidades: 23, Ventas: 8500 },
        ],
        rowFields: ['Producto'],
      };
    }

    constructor(raw) {
      super(raw);
      this.pageSize = raw.pageSize ?? 10;
      this.showPagination = raw.showPagination ?? true;
      this.boldLastRow = raw.boldLastRow ?? false;
      // Totales por nivel, como en Sheets (visibles por defecto): rowTotals[0] es la fila
      // "Total general" y rowTotals[k] los subtotales "Total <valor>" del nivel k-1;
      // columnTotals igual con los pivotes. Se migran los flags anteriores (showRowTotals,
      // showColumnTotals, showSubtotals). Si la tabla ya resaltaba su última fila (hojas que
      // traen su propia fila de total), no se agrega otra salvo que se active.
      const sub = raw.showSubtotals ?? true;
      this.rowTotals = raw.rowTotals ?? [raw.showRowTotals ?? !this.boldLastRow, sub, sub];
      this.columnTotals = raw.columnTotals ?? [raw.showColumnTotals ?? true, sub];
      this.repeatRowLabels = raw.repeatRowLabels ?? false;
      this.columnOrder = raw.columnOrder ?? null;
      this.formattersMap = raw.formattersMap ?? {};
    }

    getProperties() {
      return { ...super.getProperties(), pageSize: this.pageSize, showPagination: this.showPagination,
        boldLastRow: this.boldLastRow, rowTotals: this.rowTotals, columnTotals: this.columnTotals,
        repeatRowLabels: this.repeatRowLabels,
        columnOrder: this.columnOrder, formattersMap: this.formattersMap };
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
          if (col.field === fieldName) {
            const updated = { ...col, ...config };
            // La celda de la fila de totales usa el mismo formato que la columna.
            if (col.bottomCalc) Object.assign(updated, TableWidget.calcFormatter(config));
            return updated;
          }
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
    static calcFormatter(config) {
      return { bottomCalcFormatter: config.formatter, bottomCalcFormatterParams: config.formatterParams };
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
      // El contenedor viene con flex-1 (ocupa todo el alto de la tarjeta). La tabla no debe
      // crecer más que su contenido, para que la fila de totales quede justo después de la
      // última fila; sí puede encogerse (min-h-0) y entonces hace scroll (ver maxHeight).
      container.style.flex = '0 1 auto';
      container.classList.remove('tb-pivot');
      // "Total general" viene marcada con total: true (nivel 0 de columnas) y los "Total <valor>"
      // del pivote anidado con subtotal: true (nivel 1, hay como mucho dos pivotes); se quitan
      // (con sus hijos) si ese nivel tiene los totales apagados.
      const keep = (c) => !(c.total && !TableWidget.totalsOn(this.columnTotals, 0))
        && !(c.subtotal && !TableWidget.totalsOn(this.columnTotals, 1));
      const prune = (cols) => cols.filter(keep)
        .map(c => (c.children ? { ...c, children: prune(c.children) } : c))
        .filter(c => !c.children || c.children.length);
      let columns = prune(payload.columns || []);
      // Columnas de etiquetas de fila; el resto son valores. Payloads anteriores no traen
      // rowFields: la primera columna es la dimensión.
      const rowFields = payload.rowFields || (columns[0] && columns[0].field ? [columns[0].field] : []);
      const rowFieldSet = new Set(rowFields);
      // Con varios niveles de filas el orden es el de la jerarquía: ordenar por cabecera
      // mezclaría los grupos con sus subtotales.
      const hierarchical = rowFields.length > 1;
      const rows = this._displayRows(payload.rows || [], rowFields);
      // La fila de totales se muestra como fila de pie (bottomCalc): no se ordena ni pagina.
      const totals = TableWidget.totalsOn(this.rowTotals, 0) ? payload.totals : null;
      const hasGroups = columns.some(c => c.children);
      // Tabla dinámica (columnas anidadas o varios niveles de filas): separadores de grupos,
      // columnas de filas fijas al hacer scroll horizontal, etc. (estilos en .tb-pivot).
      const pivotMode = hasGroups || hierarchical;
      if (pivotMode) container.classList.add('tb-pivot');
      if (!hasGroups && this.columnOrder && this.columnOrder.length) {
        const byField = new Map(columns.map(c => [c.field, c]));
        const ordered = this.columnOrder.map(f => byField.get(f)).filter(Boolean);
        const remaining = columns.filter(c => !this.columnOrder.includes(c.field));
        columns = [...ordered, ...remaining];
      }
      // Las métricas pct_* se muestran como porcentaje salvo que el usuario elija otro formato.
      const percentFields = new Set(payload.percent || []);
      // {header, field} / {header, children} (formato de compile_view) -> columnas de Tabulator.
      // `inherited` son las clases de un grupo de total/subtotal, que se pasan a sus hijos.
      const toTabulator = (col, inherited = []) => {
        const classes = [...inherited];
        if (col.subtotal) classes.push('tb-subtotal-col');
        if (col.total) classes.push('tb-total-col');
        if (col.children) {
          const children = col.children.map(child => toTabulator(child, classes));
          // Separador a la izquierda de cada grupo, para ver dónde empieza.
          if (children.length) children[0].cssClass = [children[0].cssClass, 'tb-group-start'].filter(Boolean).join(' ');
          return { title: col.header, columns: children, headerHozAlign: 'center', cssClass: classes.join(' ') || undefined };
        }
        const isDimension = rowFieldSet.has(col.field);
        const defaultFormat = isDimension ? "text" : (percentFields.has(col.field) ? "percent" : "number");
        const format = this.formattersMap[col.field] || defaultFormat;
        const formatterConfig = formattersMap[format] || formattersMap[defaultFormat];
        const result = { title: col.header, field: col.field, ...formatterConfig };
        classes.push(isDimension ? 'tb-dim' : 'tb-num');
        // "Total general" suelto (una métrica): marca el inicio de su bloque.
        if (col.total) classes.push('tb-group-start');
        result.cssClass = classes.join(' ');
        if (!isDimension) result.headerHozAlign = 'right';
        if (isDimension && pivotMode) result.frozen = true;
        if (hierarchical) result.headerSort = false;
        if (totals) {
          result.bottomCalc = () => totals[col.field] ?? null;
          Object.assign(result, TableWidget.calcFormatter(formatterConfig));
        }
        if (!this._readOnly) result.headerMenu = this.menuFormatter;
        return result;
      };
      columns = columns.map(col => toTabulator(col));
      this._table = new Tabulator(container, {
        data: rows,
        // Los campos del pivote ("__pivots.Ene.total_ventas") son claves planas, no rutas.
        nestedFieldSeparator: false,
        movableColumns: !hasGroups,
        columnCalcs: "table",
        columns,
        // Tabla dinámica: cada columna a su contenido (estirar "Total general" no aporta).
        // Tabla plana: el ancho se reparte entre todas las columnas; estirar solo la última
        // dejaba sus números (alineados a la derecha) lejos del resto.
        layout: pivotMode ? 'fitData' : 'fitColumns',
        // Encabezados de distinta profundidad (ej. "Total general" junto a "2026 › Ene")
        // alineados abajo, junto a los datos.
        columnHeaderVertAlign: 'bottom',
        pagination: this.showPagination,
        paginationSize: this.pageSize,
        // maxHeight y no height: con altura fija Tabulator dibuja la fila de totales (bottomCalc)
        // al fondo de la tarjeta, lejos de la última fila. Así la tabla crece con su contenido
        // (totales justo debajo) y solo al llenar la tarjeta hace scroll con los totales al pie.
        maxHeight: '100%',
        rowFormatter: (row)=> {
          // Quitamos la clase por defecto para evitar residuos al alternar el checkbox
          row.getElement().classList.remove("tabulator-row-bold");
          row.getElement().classList.toggle("tabulator-row-subtotal", !!row.getData().__subtotal);

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

    static totalsOn(levels, level) {
      return (levels || [])[level] !== false;
    }

    // Filas a mostrar: sin los subtotales de los niveles apagados y, salvo "Repetir etiquetas
    // de fila", con la etiqueta de un nivel en blanco cuando repite la de la fila anterior
    // (como Sheets). Un subtotal "Total <valor>" del nivel k-1 deja en blanco los niveles
    // desde k, así que su primera etiqueta vacía dice qué checkbox lo controla.
    _displayRows(rows, rowFields) {
      rows = rows.filter(r => !r.__subtotal
        || TableWidget.totalsOn(this.rowTotals, rowFields.findIndex(f => r[f] == null)));
      if (this.repeatRowLabels || rowFields.length < 2) return rows;
      let previous = null;
      return rows.map(row => {
        if (row.__subtotal) {
          previous = null;
          return row;
        }
        const shown = { ...row };
        for (let i = 0; previous && i < rowFields.length - 1; i++) {
          if (!rowFields.slice(0, i + 1).every(f => row[f] === previous[f])) break;
          shown[rowFields[i]] = '';
        }
        previous = row;
        return shown;
      });
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
