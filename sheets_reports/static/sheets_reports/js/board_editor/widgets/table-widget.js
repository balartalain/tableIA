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

      const columns = payload.columns || [];
      const numeric = new Set(columns.filter(c => c.numeric).map(c => c.field));
      const rows = payload.rows || [];
      this._mountTable({
        columns: this._orderedColumns(columns),
        rows,
        // El de la columna en la fuente (moneda, %).
        formatOf: (field) => numeric.has(field) ? ((payload.formats || {})[field] || 'number') : 'text',
        sortable: true,
        footerNote: payload.truncated
          ? `Mostrando ${rows.length.toLocaleString()} de ${Number(payload.total_rows).toLocaleString()} filas; usa filtros para acotar.`
          : '',
      });
    }
  }

  WidgetRegistry.register(TableWidget);
})();