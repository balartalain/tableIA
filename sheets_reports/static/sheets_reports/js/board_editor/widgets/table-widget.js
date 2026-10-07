(function () {
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

    static mockData() {
      return {
        columns: [
          { header: 'Producto', field: 'Producto' },
          { header: 'Vendedor', field: 'Vendedor' },
          { header: 'Ventas', field: 'Ventas' },
        ],
        rows: [
          { Producto: 'Producto A', Vendedor: 'Ana', Ventas: 14200 },
          { Producto: 'Producto B', Vendedor: 'Luis', Ventas: 19800 },
          { Producto: 'Producto C', Vendedor: 'Eva', Ventas: 8500 },
        ],
      };
    }

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
      // Ocupa todo el alto bajo el título: la tabla arranca arriba aunque tenga pocas filas.
      container.style.flex = '1 1 0%';
      container.classList.remove('tb-pivot');
      // La tabla va en un hijo con alto máximo = el del contenedor: mide lo que sus filas (el
      // pie queda pegado a la última) y solo con muchas llega al fondo y hace scroll.
      const host = document.createElement('div');
      container.appendChild(host);

      const formats = this.constructor.formats;
      const columns = this._orderedColumns(payload.columns || []).map(col => {
        const defaultFormat = col.numeric ? 'number' : 'text';
        const format = this.style.formattersMap?.[col.field] || defaultFormat;
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
      this._table = new Tabulator(host, {
        data: rows,
        nestedFieldSeparator: false,
        movableColumns: true,
        columns,
        layout: 'fitColumns',
        columnHeaderVertAlign: 'bottom',
        pagination: this.style.showPagination ?? true,
        paginationSize: this.style.pageSize ?? 10,
        maxHeight: '100%',
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