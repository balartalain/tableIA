(function () {
    const formattersMap = {
      "text": { hozAlign: "left", formatter: "plaintext" },
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

    class DynamicTableWidget extends BaseWidget {
      static type = 'dynamic_table';
      static palette = {
        icon: 'ti-table-options',
        category: 'data',
        label: 'Tabla dinámica',
        description: 'Agrupa filas y columnas, con totales',
      };
      static defaults = { title: 'Tabla dinámica', width: 'md:col-span-6', height: 300 };
      static formats = formattersMap;

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
      }

      getProperties() {
        return { ...super.getProperties() };
      }

      buildElement() {
        return this.buildStandardCardElement();
      }

      buildReadOnlyElement() {
        const el = super.buildReadOnlyElement();
        el.querySelector('.actions-slot').innerHTML = this.downloadButtonHTML('Descargar CSV');
        return el;
      }

      applyFormatter(fieldName, tipoFormato) {
        const config = formattersMap[tipoFormato] || formattersMap["text"];
        const update = (cols) => cols.map(col => {
            if (col.columns) return { ...col, columns: update(col.columns) };
            if (col.field === fieldName) {
              const updated = { ...col, ...config };
              if (col.bottomCalc) Object.assign(updated, DynamicTableWidget.calcFormatter(config));
              return updated;
            }
            return col;
        });
        const cols = update(this._table.getColumnDefinitions());
        this.style.formattersMap = { ...this.style.formattersMap, [fieldName]: tipoFormato };
        this._table.setColumns(cols);
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

      draw(data, style, title) {
        this.style = { ...this.style, ...style };
        if (title) this.title = title;

        const payload = data || this.constructor.mockData();
        this._lastData = payload;

        if (this._table) {
          this._table.destroy();
          this._table = null;
        }
        const container = this.getContentContainer();
        container.innerHTML = '';
        container.style.backgroundColor = '#fff';
        container.style.flex = '0 1 auto';
        container.classList.remove('tb-pivot');

        // «Mostrar totales» por nivel (estilo del widget): la columna «Total general» y las
        // «Total <valor>» de cada pivote se quitan (con sus hijos) si ese nivel está apagado.
        let columns = this._visibleColumns(payload.columns);
        const rowFields = payload.rowFields || (columns[0] && columns[0].field ? [columns[0].field] : []);
        const rowFieldSet = new Set(rowFields);
        const hierarchical = rowFields.length > 1;
        const rows = this._displayRows(payload.rows || [], rowFields);
        const totals = this.style.showTotals !== false ? payload.totals : null;
        const hasGroups = columns.some(c => c.children);
        const pivotMode = hasGroups || hierarchical;
        if (pivotMode) container.classList.add('tb-pivot');
        if (!hasGroups) columns = this._orderedColumns(columns);
        const percentFields = new Set(payload.percent || []);

        const toTabulator = (col, inherited = []) => {
          const classes = [...inherited];
          if (col.subtotal) classes.push('tb-subtotal-col');
          if (col.total) classes.push('tb-total-col');
          if (col.children) {
            const children = col.children.map(child => toTabulator(child, classes));
            if (children.length) children[0].cssClass = [children[0].cssClass, 'tb-group-start'].filter(Boolean).join(' ');
            return { title: col.header, columns: children, headerHozAlign: 'center', cssClass: classes.join(' ') || undefined };
          }
          const isDimension = rowFieldSet.has(col.field);
          const defaultFormat = isDimension ? "text" : (percentFields.has(col.field) ? "percent" : "number");
          const format = this.style.formattersMap?.[col.field] || defaultFormat;
          const formatterConfig = formattersMap[format] || formattersMap[defaultFormat];
          const result = { title: col.header, field: col.field, ...formatterConfig };
          classes.push(isDimension ? 'tb-dim' : 'tb-num');
          if (col.total) classes.push('tb-group-start');
          result.cssClass = classes.join(' ');
          if (!isDimension) result.headerHozAlign = 'right';
          if (isDimension && pivotMode) result.frozen = true;
          if (hierarchical) result.headerSort = false;
          if (totals) {
            result.bottomCalc = () => totals[col.field] ?? null;
            Object.assign(result, DynamicTableWidget.calcFormatter(formatterConfig));
          }
          if (!this._readOnly) result.headerMenu = this.menuFormatter;
          return result;
        };

        columns = columns.map(col => toTabulator(col));

        this._table = new Tabulator(container, {
          data: rows,
          nestedFieldSeparator: false,
          movableColumns: !hasGroups,
          columnCalcs: "table",
          columns,
          layout: pivotMode ? 'fitData' : 'fitColumns',
          columnHeaderVertAlign: 'bottom',
          pagination: this.style.showPagination ?? true,
          paginationSize: this.style.pageSize ?? 10,
          maxHeight: '100%',
          rowFormatter: (row) => this._formatRow(row),
        });
        this._wireTableEvents();
      }

      _formatRow(row) {
        row.getElement().classList.remove("tabulator-row-bold");
        row.getElement().classList.toggle("tabulator-row-subtotal", !!row.getData().__subtotal);

        if (this.style.boldLastRow) {
          const todasLasFilas = row.getTable().getRows("active");
          const ultimaFila = todasLasFilas[todasLasFilas.length - 1];
          if (ultimaFila && row.getPosition() === ultimaFila.getPosition()) {
            row.getElement().classList.add("tabulator-row-bold");
          }
        }
      }

      _wireTableEvents() {
        this._table.on("columnMoved", (_column, columns) => {
          this.style.columnOrder = columns.map(col => col.getField());
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

      _orderedColumns(columns) {
        if (!this.style.columnOrder || !this.style.columnOrder.length) return columns;
        const byField = new Map(columns.map(c => [c.field, c]));
        const ordered = this.style.columnOrder.map(f => byField.get(f)).filter(Boolean);
        const remaining = columns.filter(c => !this.style.columnOrder.includes(c.field));
        return [...ordered, ...remaining];
      }

      // Columnas visibles: se cae la «Total general» (showColumnTotals) y cada columna
      // «Total <valor>» (columnSubtotal1) si su checkbox está apagado, y sus hijos con ella.
      _visibleColumns(columns) {
        const keep = (c) => !(c.total && this.style.showColumnTotals !== true)
          && !(c.subtotal && this.style.columnSubtotal1 !== true);
        const prune = (cols) => cols.filter(keep)
          .map(c => (c.children ? { ...c, children: prune(c.children) } : c))
          .filter(c => !c.children || c.children.length);
        return prune(columns || []);
      }

      _displayRows(rows, rowFields) {
        // Nivel de una fila «Total <valor>»: el primer campo de fila nulo (0 = total
        // general, 1 = subtotal de la 1ª dimensión, 2 = de la 2ª) y se dibuja solo si su
        // checkbox de «Mostrar totales» está activo.
        const visible = (level) => level <= 0
          ? this.style.showTotals !== false
          : (level === 1 ? this.style.rowSubtotal1 === true : this.style.rowSubtotal2 === true);
        const levelOf = (row) => rowFields.findIndex(f => row[f] == null);
        rows = rows.filter(r => !r.__subtotal || visible(levelOf(r)));
        if (this.style.repeatRowLabels || rowFields.length < 2) return rows;
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

    WidgetRegistry.register(DynamicTableWidget);
})();