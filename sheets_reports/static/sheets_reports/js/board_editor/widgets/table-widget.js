(function () {
  // Tabla de datos: las filas de la hoja tal cual, con las columnas elegidas (TableWidget del
  // backend). No agrupa ni lleva métricas: reutiliza de la tabla dinámica el menú de formatos,
  // la paginación, el orden de columnas arrastradas y la descarga CSV.
  const DynamicTableWidget = WidgetRegistry.get('dynamic_table');

  class TableWidget extends DynamicTableWidget {
    static type = 'table';
    static palette = {
      icon: 'ti-table',
      category: 'data',
      label: 'Tabla',
      description: 'Las filas de la hoja, con las columnas que elijas',
    };
    static defaults = { title: 'Tabla', width: 'md:col-span-6', height: 300 };
    static supportsLabels = false;
    static supportsTotals = false;

    static get drawerFields() {
      // Sin "Consulta con la IA" (es de la tabla dinámica).
      return super.drawerFields.filter(f => f.key !== 'prompt');
    }

    static mockData() {
      return {
        columns: [
          { header: 'Producto', field: 'Producto', numeric: false },
          { header: 'Vendedor', field: 'Vendedor', numeric: false },
          { header: 'Ventas', field: 'Ventas', numeric: true },
        ],
        rows: [
          { Producto: 'Producto A', Vendedor: 'Ana', Ventas: 14200 },
          { Producto: 'Producto B', Vendedor: 'Luis', Ventas: 19800 },
          { Producto: 'Producto C', Vendedor: 'Eva', Ventas: 8500 },
        ],
      };
    }

    renderContent(container, data) {
      const payload = data || this.constructor.mockData();
      if (this._table) {
        this._table.destroy();
        this._table = null;
      }
      container.innerHTML = '';
      container.style.backgroundColor = '#fff';
      container.style.flex = '0 1 auto';
      container.classList.remove('tb-pivot');

      const formats = this.constructor.formats;
      const columns = this._orderedColumns(payload.columns || []).map(col => {
        const defaultFormat = col.numeric ? 'number' : 'text';
        const format = this.formattersMap[col.field] || defaultFormat;
        const result = {
          title: col.header,
          field: col.field,
          ...(formats[format] || formats[defaultFormat]),
          cssClass: col.numeric ? 'tb-num' : 'tb-dim',
        };
        if (col.numeric) result.headerHozAlign = 'right';
        if (!this._readOnly) result.headerMenu = this.menuFormatter;
        return result;
      });
      const rows = payload.rows || [];
      this._table = new Tabulator(container, {
        data: rows,
        // Los nombres de columna de la hoja pueden tener puntos: son claves planas.
        nestedFieldSeparator: false,
        movableColumns: true,
        columns,
        layout: 'fitColumns',
        columnHeaderVertAlign: 'bottom',
        pagination: this.showPagination,
        paginationSize: this.pageSize,
        maxHeight: '100%',
        // Hojas muy grandes: el backend envía hasta un tope de filas y avisa cuántas había.
        footerElement: payload.truncated
          ? `<span class="text-[11px] text-ink/50 px-2">Mostrando ${rows.length.toLocaleString()} de ${Number(payload.total_rows).toLocaleString()} filas; usa filtros para acotar.</span>`
          : undefined,
        rowFormatter: (row) => this._formatRow(row),
      });
      this._wireTableEvents();
    }
  }

  WidgetRegistry.register(TableWidget);
})();
