// Tabla HTML de los widgets «table» y «dynamic_table». Las celdas ya vienen calculadas del
// motor (subtotales y totales incluidos): aquí solo se arma la <table>.
// - `columns`: árbol {header, field, children?, subtotal?, total?}; un grupo abarca (colspan)
//   sus hojas y una hoja baja (rowspan) hasta la última fila de la cabecera.
// - `rowFields`: las dimensiones; quedan fijas al hacer scroll horizontal y, salvo
//   `repeatRowLabels`, un mismo valor se dibuja una vez para todo su grupo (rowspan). Las filas
//   `__subtotal` («Total <valor>») abarcan con su etiqueta el resto de las dimensiones.
// - `totals`: la fila «Total general», en el <tfoot> pegado al fondo.
// - `formatOf(field)`: 'text' | 'number' | 'currency' | 'percent' | 'progress'.
// - `sortable`: clic en el encabezado ordena (asc → desc → original); solo en tablas planas.
// - `footerNote`: aviso a la izquierda del paginador.
// - `onMove(id, dir)`: una columna con `move: {id, left, right}` lleva flechas ‹ › a los lados
//   de su encabezado; el clic llama a `onMove` con -1 (izquierda) o 1 (derecha).
class DataTable {
  // Relleno de la barra por tercio, de menos a más (clases de tablero-theme.css).
  static PROGRESS = { min: 0, max: 100, fills: ['tb-fill-bad', 'tb-fill-warn', 'tb-fill-good'] };
  static NUMBER_OPTIONS = { number: {}, currency: { currency: true }, percent: { percent: true } };

  constructor(host, {
    columns = [], rows = [], rowFields = [], totals = null, formatOf = () => 'number',
    repeatRowLabels = false, paginate = true, pageSize = 10, sortable = false, footerNote = '',
    onMove = null,
  } = {}) {
    this.host = host;
    this.onMove = onMove;
    this.rows = rows;
    this.totals = totals;
    this.repeatRowLabels = repeatRowLabels;
    this.paginate = paginate;
    this.pageSize = Math.max(1, Number(pageSize) || 10);
    this.page = 0;
    this.sort = null;

    this._buildHeader(columns, formatOf, rowFields);
    this.sortable = sortable && this.headerRows.length === 1 && this.dims.length <= 1;
    this._mount(footerNote);
    this._render();
    if (typeof ResizeObserver !== 'undefined') {
      this._observer = new ResizeObserver(() => this._stick());
      this._observer.observe(this.table);
    }
  }

  destroy() {
    if (this._observer) this._observer.disconnect();
    this._observer = null;
    this.host.innerHTML = '';
  }

  // Encabezados (con la ruta de grupos: «Suma Ventas / 2026») y valores, en el orden visible.
  csv() {
    const rows = [...this._sortedRows(), ...(this.totals ? [this.totals] : [])];
    return {
      headers: this.leaves.map(l => l.path.join(' / ')),
      rows: rows.map(row => this.leaves.map(l => row[l.field])),
    };
  }

  // --- Armado -----------------------------------------------------------------
  _buildHeader(columns, formatOf, rowFields) {
    const rowFieldSet = new Set(rowFields);
    const depthOf = (cols) => Math.max(1, ...cols.map(c => (c.children ? 1 + depthOf(c.children) : 1)));
    const depth = depthOf(columns);
    this.headerRows = Array.from({ length: depth }, () => []);
    this.leaves = [];
    // Las clases de un grupo pasan a sus hojas; `tb-first` marca el borde izquierdo de la tabla.
    const walk = (cols, level, inherited, path) => cols.forEach(col => {
      const classes = [...inherited];
      if (col.subtotal) classes.push('tb-subtotal-col');
      if (col.total) classes.push('tb-total-col');
      const first = this.leaves.length === 0 ? ['tb-first'] : [];
      if (col.children) {
        const cell = { header: col.header, classes: [...classes, ...first, 'tb-group', 'tb-group-start'], colspan: 0, rowspan: 1, move: col.move };
        this.headerRows[level].push(cell);
        const start = this.leaves.length;
        walk(col.children, level + 1, classes, [...path, col.header]);
        cell.colspan = this.leaves.length - start;
        if (cell.colspan) this.leaves[start].classes.push('tb-group-start');
        return;
      }
      const format = formatOf(col.field);
      classes.push(...first, format === 'text' ? 'tb-dim' : 'tb-num');
      if (col.total) classes.push('tb-group-start');
      const leaf = {
        field: col.field, header: col.header, path: [...path, col.header], classes, format,
        isDim: rowFieldSet.has(col.field), index: this.leaves.length,
      };
      this.leaves.push(leaf);
      this.headerRows[level].push({ header: col.header, classes, colspan: 1, rowspan: depth - level, leaf, move: col.move });
    });
    walk(columns, 0, [], []);

    // Las dimensiones van primero (así las arma el motor).
    let dimCount = this.leaves.findIndex(l => !l.isDim);
    if (dimCount < 0) dimCount = this.leaves.length;
    this.dims = this.leaves.slice(0, dimCount).map(l => l.field);
    this.values = this.leaves.slice(dimCount);
  }

  _mount(footerNote) {
    const esc = BaseWidget.escapeHTML;
    const attrs = (classes, extra = '') => {
      const all = classes.filter(Boolean);
      return (all.length ? ` class="${all.join(' ')}"` : '') + extra;
    };
    // Los ids de las columnas que se mueven, por índice (el id puede llevar comillas).
    this.moveIds = [];
    const thead = this.headerRows.map(cells => `<tr>${cells.map(c => {
      const leaf = c.leaf;
      const dim = leaf && leaf.isDim ? ` data-dim="${this.dims.indexOf(leaf.field)}"` : '';
      const span = (c.colspan > 1 ? ` colspan="${c.colspan}"` : '') + (c.rowspan > 1 ? ` rowspan="${c.rowspan}"` : '');
      const sort = leaf && this.sortable ? ` data-sort="${leaf.index}"` : '';
      const move = c.move && this.onMove ? c.move : null;
      const moveIndex = move ? this.moveIds.push(move.id) - 1 : -1;
      const arrow = (dir, shown, label, symbol) => shown
        ? `<button type="button" class="tb-move" data-move="${dir}" data-col="${moveIndex}" aria-label="${label}" title="${label}">${symbol}</button>`
        : '';
      const header = move
        ? `<span class="tb-move-wrap">${arrow(-1, move.left, 'Mover a la izquierda', '‹')}<span>${esc(c.header || '')}</span>${arrow(1, move.right, 'Mover a la derecha', '›')}</span>`
        : esc(c.header || '');
      return `<th${attrs(c.classes, span + dim + sort)}>${header}</th>`;
    }).join('')}</tr>`).join('');

    let tfoot = '';
    if (this.totals) {
      const label = this.dims.length
        ? `<td class="tb-dim"${this.dims.length > 1 ? ` colspan="${this.dims.length}"` : ''} data-dim="0">${this._cell('text', this.totals[this.dims[0]])}</td>`
        : '';
      tfoot = `<tfoot><tr>${label}${this._valueCells(this.totals)}</tr></tfoot>`;
    }

    this.pages = this.paginate ? Math.max(1, Math.ceil(this.rows.length / this.pageSize)) : 1;
    const buttons = this.pages > 1
      ? '<button type="button" data-step="-1" aria-label="Página anterior">‹</button><span class="tb-pager-pos"></span><button type="button" data-step="1" aria-label="Página siguiente">›</button>'
      : '';
    const pager = buttons || footerNote
      ? `<div class="tb-pager">${footerNote ? `<span class="tb-pager-note">${esc(footerNote)}</span>` : ''}${buttons}</div>`
      : '';

    this.host.classList.add('tb-table-host');
    this.host.innerHTML = `<div class="tb-table-scroll"><table class="tb-table"><thead>${thead}</thead><tbody></tbody>${tfoot}</table></div>${pager}`;
    this.table = this.host.querySelector('table');
    this.tbody = this.table.querySelector('tbody');
    this.pager = this.host.querySelector('.tb-pager');

    if (this.pages > 1) this.pager.onclick = (e) => {
      const button = e.target.closest('button[data-step]');
      if (!button) return;
      this.page = Math.max(0, Math.min(this.pages - 1, this.page + Number(button.dataset.step)));
      this._render();
    };
    if (this.onMove) this.table.querySelector('thead').addEventListener('click', (e) => {
      const button = e.target.closest('button[data-move]');
      if (button) this.onMove(this.moveIds[Number(button.dataset.col)], Number(button.dataset.move));
    });
    if (this.sortable) this.table.querySelector('thead').onclick = (e) => {
      const th = e.target.closest('th[data-sort]');
      if (th) this._toggleSort(this.leaves[Number(th.dataset.sort)].field);
    };
  }

  // --- Celdas -----------------------------------------------------------------
  _cell(format, value) {
    const esc = BaseWidget.escapeHTML;
    if (format === 'text') return esc(value == null ? '' : String(value));
    if (format === 'progress') {
      if (value == null || value === '') return '-';
      const { min, max, fills } = DataTable.PROGRESS;
      const pct = Math.max(0, Math.min(100, ((Number(value) - min) / (max - min)) * 100)) || 0;
      const fill = fills[Math.min(fills.length - 1, Math.floor(pct / (100 / fills.length)))];
      return `<span class="tb-progress"><span class="${fill}" style="width:${pct}%"></span></span>`
        + `<span class="tb-progress-legend">${esc(formatNumber(value, { percent: true }))}</span>`;
    }
    return esc(formatNumber(value, DataTable.NUMBER_OPTIONS[format] || {}));
  }

  _valueCells(row) {
    return this.values.map(l => `<td class="${l.classes.join(' ')}">${this._cell(l.format, row[l.field])}</td>`).join('');
  }

  // Nivel de una fila de subtotal: el primer campo de fila vacío; la etiqueta «Total <valor>»
  // está justo antes.
  _levelOf(row) {
    const level = this.dims.findIndex(f => row[f] == null);
    return level < 0 ? this.dims.length : level;
  }

  _bodyHTML(rows) {
    const dims = this.dims;
    // Hasta qué dimensión una fila puede compartir celda (rowspan) con la de arriba.
    const mergeLimit = (row) => (row.__subtotal ? this._levelOf(row) - 2 : dims.length - 2);
    const spans = rows.map(() => dims.map(() => 1));
    if (!this.repeatRowLabels) {
      for (let j = 0; j < dims.length - 1; j++) {
        for (let i = 0; i < rows.length;) {
          if (mergeLimit(rows[i]) < j) { i++; continue; }
          let k = i + 1;
          while (k < rows.length && mergeLimit(rows[k]) >= j
            && dims.slice(0, j + 1).every(f => rows[k][f] === rows[i][f])) {
            spans[k][j] = 0;
            k++;
          }
          spans[i][j] = k - i;
          i = k;
        }
      }
    }
    return rows.map((row, i) => {
      const last = row.__subtotal ? Math.max(0, this._levelOf(row) - 1) : dims.length;
      let html = '';
      for (let j = 0; j < last; j++) {
        if (!spans[i][j]) continue;
        const span = spans[i][j] > 1 ? ` rowspan="${spans[i][j]}"` : '';
        html += `<td class="tb-dim"${span} data-dim="${j}">${this._cell('text', row[dims[j]])}</td>`;
      }
      if (row.__subtotal) {
        const span = dims.length - last > 1 ? ` colspan="${dims.length - last}"` : '';
        html += `<td class="tb-dim"${span} data-dim="${last}">${this._cell('text', row[dims[last]])}</td>`;
      }
      return `<tr${row.__subtotal ? ' class="tb-subtotal-row"' : ''}>${html}${this._valueCells(row)}</tr>`;
    }).join('');
  }

  // --- Orden y página ---------------------------------------------------------
  _toggleSort(field) {
    const dir = this.sort && this.sort.field === field ? this.sort.dir : null;
    const next = dir === null ? 'asc' : (dir === 'asc' ? 'desc' : null);
    this.sort = next ? { field, dir: next } : null;
    this.table.querySelectorAll('th[data-sort]').forEach(th => {
      const own = this.sort && this.leaves[Number(th.dataset.sort)].field === this.sort.field;
      if (own) th.setAttribute('aria-sort', this.sort.dir === 'asc' ? 'ascending' : 'descending');
      else th.removeAttribute('aria-sort');
    });
    this.page = 0;
    this._render();
  }

  // Números como números y textos con el orden del idioma; los vacíos siempre al final.
  _sortedRows() {
    if (!this.sort) return this.rows;
    const { field, dir } = this.sort;
    const sign = dir === 'asc' ? 1 : -1;
    const empty = (v) => v == null || v === '';
    return [...this.rows].sort((a, b) => {
      const x = a[field], y = b[field];
      if (empty(x) || empty(y)) return empty(x) - empty(y);
      if (typeof x === 'number' && typeof y === 'number') return (x - y) * sign;
      return String(x).localeCompare(String(y), undefined, { numeric: true }) * sign;
    });
  }

  _render() {
    const rows = this._sortedRows();
    this.tbody.innerHTML = this._bodyHTML(this.paginate
      ? rows.slice(this.page * this.pageSize, (this.page + 1) * this.pageSize)
      : rows);
    if (this.pages > 1) {
      this.pager.querySelector('.tb-pager-pos').textContent = `${this.page + 1} / ${this.pages}`;
      this.pager.querySelector('[data-step="-1"]').disabled = this.page === 0;
      this.pager.querySelector('[data-step="1"]').disabled = this.page === this.pages - 1;
    }
    this._stick();
  }

  // Posición de lo fijo: cada dimensión a la izquierda de la suma de las anteriores y cada
  // fila de la cabecera bajo las de arriba (con varias, `top: 0` las encimaría).
  _stick() {
    const lefts = [];
    let left = 0;
    this.table.querySelectorAll('thead th[data-dim]').forEach(th => {
      lefts[Number(th.dataset.dim)] = left;
      left += th.getBoundingClientRect().width;
    });
    this.table.querySelectorAll('[data-dim]').forEach(cell => {
      cell.style.left = `${lefts[Number(cell.dataset.dim)] || 0}px`;
    });
    let top = 0;
    this.table.querySelectorAll('thead tr').forEach(tr => {
      tr.querySelectorAll('th').forEach(th => { th.style.top = `${top}px`; });
      top += tr.getBoundingClientRect().height;
    });
  }
}
