(function () {
    class DynamicTableWidget extends BaseWidget {
      static type = 'dynamic_table';
      static palette = {
        icon: 'ti-table-options',
        category: 'data',
        label: 'Tabla dinámica',
        description: 'Agrupa filas y columnas, con totales',
      };
      static defaults = { title: 'Tabla dinámica', width: 'md:col-span-6', height: 300 };

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

      // Miniatura: cabecera + filas esquemáticas (la hereda también el widget "table").
      thumbnailContent(ctx) {
        const { x, y, w, h, primary } = ctx;
        const top = y + 14;
        const lines = Math.max(2, Math.min(4, Math.floor((h - 20) / 7)));
        let rows = '';
        for (let i = 0; i < lines; i++) {
          const ly = top + 9 + i * 7;
          rows += `<line x1="${x + 6}" y1="${ly}" x2="${x + w - 6}" y2="${ly}" stroke="#E3E5DE" stroke-width="1.5"/>`;
        }
        return `<rect x="${x + 6}" y="${top}" width="${w - 12}" height="3" rx="1" fill="${primary}" opacity="0.35"/>${rows}`;
      }

      buildReadOnlyElement() {
        const el = super.buildReadOnlyElement();
        el.querySelector('.actions-slot').innerHTML = this.downloadButtonHTML('Descargar CSV');
        return el;
      }

      draw(data, style, title) {
        this.style = { ...this.style, ...style };
        if (title) this.title = title;

        const payload = data || this.constructor.mockData();
        this._lastData = payload;

        // «Mostrar totales» por nivel (estilo del widget): la columna «Total general» y las
        // «Total <valor>» de cada pivote se quitan (con sus hijos) si ese nivel está apagado.
        let columns = this._visibleColumns(payload.columns);
        const rowFields = payload.rowFields || (columns[0] && columns[0].field ? [columns[0].field] : []);
        const rowFieldSet = new Set(rowFields);
        const flat = !columns.some(c => c.children) && rowFields.length < 2;
        if (flat) columns = this._orderedColumns(columns);
        const percentFields = new Set(payload.percent || []);
        // Formato de cada campo según su métrica (elegido en ella o heredado de la fuente).
        const payloadFormats = payload.formats || {};

        this._mountTable({
          columns,
          rows: this._visibleRows(payload.rows || [], rowFields),
          rowFields,
          totals: this.style.showTotals !== false ? payload.totals : null,
          formatOf: (field) => rowFieldSet.has(field) ? 'text'
            : (payloadFormats[field] || (percentFields.has(field) ? 'percent' : 'number')),
          repeatRowLabels: !!this.style.repeatRowLabels,
          sortable: flat,
        });
      }

      // Monta la tabla (utils/data-table.js) en el contenido de la tarjeta; la comparte el
      // widget «table».
      _mountTable(options) {
        if (this._table) {
          this._table.destroy();
          this._table = null;
        }
        const container = this.getContentContainer();
        container.innerHTML = '';
        container.style.backgroundColor = '#fff';
        // Ocupa todo el alto bajo el título: la tabla arranca arriba aunque tenga pocas filas.
        container.style.flex = '1 1 0%';
        // La tabla va en un hijo con alto máximo = el del contenedor: mide lo que sus filas (el
        // «Total general» queda pegado a la última) y solo con muchas llega al fondo y hace scroll.
        const host = document.createElement('div');
        host.style.width = '100%';
        container.appendChild(host);

        this._table = new DataTable(host, {
          paginate: this.style.showPagination ?? true,
          pageSize: this.style.pageSize ?? 10,
          ...options,
        });
        this._wireDownload(() => {
          const { headers, rows } = this._table.csv();
          this.downloadRowsAsCSV(headers, rows, `${this._filenameSlug('tabla')}.csv`);
        });
      }

      _wireDownload(download) {
        const downloadBtn = this.el && this.el.querySelector('.download-csv-btn');
        if (downloadBtn) downloadBtn.onclick = download;
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

      _visibleRows(rows, rowFields) {
        // Nivel de una fila «Total <valor>»: el primer campo de fila nulo (0 = total
        // general, 1 = subtotal de la 1ª dimensión, 2 = de la 2ª) y se dibuja solo si su
        // checkbox de «Mostrar totales» está activo.
        const visible = (level) => level <= 0
          ? this.style.showTotals !== false
          : (level === 1 ? this.style.rowSubtotal1 === true : this.style.rowSubtotal2 === true);
        const levelOf = (row) => rowFields.findIndex(f => row[f] == null);
        return rows.filter(r => !r.__subtotal || visible(levelOf(r)));
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