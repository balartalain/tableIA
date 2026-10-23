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
          rows += `<line x1="${x + 6}" y1="${ly}" x2="${x + w - 6}" y2="${ly}" stroke="${ctx.theme.border}" stroke-width="1.5"/>`;
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
        let columns = this._orderPivots(this._visibleColumns(payload.columns), payload.pivots);
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
          onMove: (id, dir) => this._movePivot(id, dir),
        });
      }

      // El orden elegido de los valores de los pivotes (`style.pivotOrder`): por grupo (la ruta
      // del padre en JSON), sus valores. Vale solo con los mismos pivotes con que se guardó.
      _pivotGroups(pivots) {
        const saved = this.style.pivotOrder;
        const same = saved && Array.isArray(saved.pivots)
          && JSON.stringify(saved.pivots) === JSON.stringify(pivots || []);
        return same && saved.groups ? saved.groups : {};
      }

      // Reordena las columnas de los pivotes dentro de su grupo: cada valor se mueve junto con
      // su «Total <valor>» (misma ruta `pivot`); los valores sin orden guardado quedan después,
      // en el orden de la hoja, y las columnas sin `pivot` (métricas, «Total general») no se
      // mueven. En el editor marca cada valor con `move` para que la tabla dibuje sus flechas.
      _orderPivots(columns, pivots) {
        const groups = this._pivotGroups(pivots);
        const editable = !this._readOnly;
        this._pivotSiblings = {};
        const walk = (cols) => {
          const units = new Map();
          cols.forEach(c => {
            if (!c.pivot) return;
            const id = JSON.stringify(c.pivot);
            if (!units.has(id)) units.set(id, { value: c.pivot[c.pivot.length - 1], path: c.pivot, nodes: [] });
            units.get(id).nodes.push(c);
          });
          const out = cols.map(c => (c.children ? { ...c, children: walk(c.children) } : c));
          if (!units.size) return out;
          const first = [...units.values()][0];
          const parent = JSON.stringify(first.path.slice(0, -1));
          const order = groups[parent] || [];
          const rank = (u) => { const i = order.indexOf(u.value); return i < 0 ? order.length : i; };
          const sorted = [...units.values()]
            .map((u, i) => ({ ...u, i }))
            .sort((a, b) => rank(a) - rank(b) || a.i - b.i);
          this._pivotSiblings[parent] = sorted.map(u => u.value);
          const byNode = new Map(cols.map((c, i) => [c, out[i]]));
          const moved = sorted.flatMap((u, i) => u.nodes.map((n, k) => {
            const node = byNode.get(n);
            if (k > 0 || !editable || sorted.length < 2) return node;
            return { ...node, move: { id: JSON.stringify(u.path), left: i > 0, right: i < sorted.length - 1 } };
          }));
          // Los valores ocupan los lugares que tenían; lo demás queda donde estaba.
          let next = 0;
          return cols.map((c, i) => (c.pivot ? moved[next++] : out[i]));
        };
        return walk(columns || []);
      }

      // Una flecha: el valor cambia de lugar con su vecino en el grupo, y el orden se guarda
      // en el widget (queda para la vista del tablero) y se redibuja sin pedir los datos.
      _movePivot(id, dir) {
        const path = JSON.parse(id);
        const parent = JSON.stringify(path.slice(0, -1));
        const values = [...(this._pivotSiblings[parent] || [])];
        const from = values.indexOf(path[path.length - 1]);
        const to = from + dir;
        if (from < 0 || to < 0 || to >= values.length) return;
        [values[from], values[to]] = [values[to], values[from]];
        const pivots = (this._lastData && this._lastData.pivots) || [];
        this.style.pivotOrder = { pivots, groups: { ...this._pivotGroups(pivots), [parent]: values } };
        this._dirty = true;
        const store = window.Alpine && Alpine.store('dashboard');
        // Con el panel de este widget abierto, su borrador también lleva el orden: si no,
        // «Guardar» lo pisaría con la copia que tomó al abrirse.
        if (store && store.editingId === this.id && store.drawerDraft && store.drawerDraft.style) {
          store.drawerDraft.style.pivotOrder = JSON.parse(JSON.stringify(this.style.pivotOrder));
        }
        if (store && typeof store._saveWidget === 'function') store._saveWidget(this);
        this.draw(this._lastData, this.style, this.title);
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