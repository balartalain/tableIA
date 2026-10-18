(function () {
  class CustomTable {
    constructor(hostEl, options = {}) {
      this.element = typeof hostEl === 'string' ? document.querySelector(hostEl) : hostEl;
      this.options = {
        data: [],
        columns: [],
        pagination: true,
        paginationSize: 10,
        movableColumns: true,
        resizableColumns: true,
        rowFormatter: null,
        footerElement: null,
        nestedFieldSeparator: false,
        ...options,
      };

      this._events = {};
      this._sortState = { field: null, dir: null };
      this._currentPage = 1;
      this._pageSize = this.options.paginationSize || 10;
      this._columnWidths = {};
      this._rawColumns = this.options.columns || [];
      this._data = this.options.data || [];

      this._init();
    }

    on(event, handler) {
      if (!this._events[event]) this._events[event] = [];
      this._events[event].push(handler);
      if (event === 'tableBuilt' || event === 'renderComplete') {
        setTimeout(() => handler(), 0);
      }
    }

    _emit(event, ...args) {
      if (this._events[event]) {
        this._events[event].forEach(fn => fn(...args));
      }
    }

    destroy() {
      if (this.element) {
        this.element.innerHTML = '';
      }
      this._events = {};
    }

    getColumns() {
      const leaves = this._getLeafColumns(this._rawColumns);
      return leaves.map(col => ({
        getField: () => col.field,
        getWidth: () => this._getColWidth(col),
        setWidth: (w) => { this._columnWidths[col.field] = w; this.render(); },
        isVisible: () => true,
      }));
    }

    _getColWidth(col) {
      if (this._columnWidths[col.field]) return this._columnWidths[col.field];
      if (col.width) return col.width;
      if (col.frozen) return 120;
      if (col.numeric || col.hozAlign === 'right') return 100;
      return 130;
    }

    _getLeafColumns(columns) {
      let leaves = [];
      (columns || []).forEach(col => {
        const children = col.children || col.columns;
        if (children && children.length) {
          leaves = leaves.concat(this._getLeafColumns(children));
        } else {
          leaves.push(col);
        }
      });
      return leaves;
    }

    _getTreeDepth(columns) {
      let max = 1;
      (columns || []).forEach(col => {
        const children = col.children || col.columns;
        if (children && children.length) {
          max = Math.max(max, 1 + this._getTreeDepth(children));
        }
      });
      return max;
    }

    _countLeafs(col) {
      const children = col.children || col.columns;
      if (!children || !children.length) return 1;
      return children.reduce((sum, child) => sum + this._countLeafs(child), 0);
    }

    _buildHeaderRows(columns) {
      const depth = this._getTreeDepth(columns);
      const rows = Array.from({ length: depth }, () => []);

      const traverse = (cols, currentLevel) => {
        cols.forEach(col => {
          const children = col.children || col.columns;
          if (children && children.length) {
            const colspan = this._countLeafs(col);
            rows[currentLevel].push({
              title: col.title || col.header || '',
              colspan,
              rowspan: 1,
              cssClass: col.cssClass || '',
              isGroup: true,
              total: col.total,
              subtotal: col.subtotal,
            });
            traverse(children, currentLevel + 1);
          } else {
            const rowspan = depth - currentLevel;
            rows[currentLevel].push({
              colDef: col,
              title: col.title || col.header || '',
              field: col.field,
              colspan: 1,
              rowspan,
              cssClass: col.cssClass || '',
              isLeaf: true,
              frozen: col.frozen,
              sortable: col.sortable !== false,
            });
          }
        });
      };

      traverse(columns, 0);
      return rows;
    }

    _getProcessedRows() {
      let rows = [...this._data];

      if (this._sortState.field && this._sortState.dir) {
        const f = this._sortState.field;
        const dir = this._sortState.dir === 'asc' ? 1 : -1;

        rows.sort((a, b) => {
          if (a.__subtotal || b.__subtotal) return 0;
          const valA = a[f];
          const valB = b[f];
          if (valA === valB) return 0;
          if (valA === null || valA === undefined) return 1;
          if (valB === null || valB === undefined) return -1;
          if (typeof valA === 'number' && typeof valB === 'number') {
            return (valA - valB) * dir;
          }
          return String(valA).localeCompare(String(valB), undefined, { numeric: true }) * dir;
        });
      }

      return rows;
    }

    _init() {
      if (!this.element) return;
      this.element.classList.add('tabulator', 'custom-table-root', 'h-full', 'w-full', 'min-h-0', 'flex', 'flex-col', 'overflow-hidden');
      this.element.style.height = '100%';
      this.element.style.minHeight = '0';
      this.element.style.display = 'flex';
      this.element.style.flexDirection = 'column';
      this.element.style.overflow = 'hidden';
      this.render();
      this._emit('tableBuilt');
    }

    _updateFrozenOffsets() {
      const leafColumns = this._getLeafColumns(this._rawColumns);
      let cumulativeLeft = 0;
      leafColumns.forEach(col => {
        const w = this._getColWidth(col);
        if (col.frozen) {
          const leftPos = cumulativeLeft;
          cumulativeLeft += w;
          const cells = this.element.querySelectorAll(`[data-field="${col.field}"]`);
          cells.forEach(cell => {
            cell.style.left = `${leftPos}px`;
          });
        }
      });
    }

    render() {
      if (!this.element) return;
      this.element.innerHTML = '';

      const container = document.createElement('div');
      container.className = 'custom-table-container flex flex-col h-full w-full min-h-0 relative overflow-hidden bg-white text-xs font-sans text-ink';
      container.style.height = '100%';
      container.style.minHeight = '0';
      container.style.display = 'flex';
      container.style.flexDirection = 'column';
      container.style.overflow = 'hidden';

      const scrollWrapper = document.createElement('div');
      scrollWrapper.className = 'custom-table-scroll tabulator-tableholder flex-1 min-h-0 overflow-auto w-full relative';
      scrollWrapper.style.flex = '1 1 0%';
      scrollWrapper.style.minHeight = '0';
      scrollWrapper.style.overflow = 'auto';

      const tableEl = document.createElement('table');
      tableEl.className = 'w-full min-w-full border-collapse text-left border-spacing-0 table-fixed';

      const leafColumns = this._getLeafColumns(this._rawColumns);

      // Compute frozen left offsets
      let cumulativeLeft = 0;
      const frozenOffsets = new Map();
      leafColumns.forEach(col => {
        const w = this._getColWidth(col);
        if (col.frozen) {
          frozenOffsets.set(col.field, cumulativeLeft);
          cumulativeLeft += w;
        }
      });

      // 0. COLGROUP for exact pixel alignment
      const colgroup = document.createElement('colgroup');
      leafColumns.forEach(col => {
        const colEl = document.createElement('col');
        const w = this._getColWidth(col);
        colEl.setAttribute('data-field', col.field);
        colEl.style.width = `${w}px`;
        colEl.style.minWidth = `${w}px`;
        colgroup.appendChild(colEl);
      });
      tableEl.appendChild(colgroup);

      // 1. HEADER (thead)
      const thead = document.createElement('thead');
      thead.className = 'tabulator-header sticky top-0 z-20 bg-paper border-b border-line shadow-2xs';

      const headerMatrix = this._buildHeaderRows(this._rawColumns);
      headerMatrix.forEach((rowCells) => {
        const tr = document.createElement('tr');
        tr.className = 'tabulator-headers';

        rowCells.forEach(cell => {
          const th = document.createElement('th');
          let classes = ['tabulator-col', 'p-2', 'font-semibold', 'align-bottom', 'border-r', 'border-line/60', 'bg-paper', 'text-ink/80', 'select-none', 'box-border'];
          if (cell.cssClass) classes.push(cell.cssClass);

          if (cell.colspan > 1) th.colSpan = cell.colspan;
          if (cell.rowspan > 1) th.rowSpan = cell.rowspan;

          if (cell.isGroup) {
            classes.push('tabulator-col-group', 'text-center', 'border-b');
          }

          if (cell.isLeaf) {
            const colDef = cell.colDef;
            th.setAttribute('data-field', cell.field);

            if (colDef.numeric || colDef.hozAlign === 'right') classes.push('text-right', 'tb-num');
            if (colDef.frozen) {
              classes.push('tabulator-frozen', 'sticky', 'z-25');
              const leftPos = frozenOffsets.get(cell.field) || 0;
              th.style.position = 'sticky';
              th.style.left = `${leftPos}px`;
            }

            const contentDiv = document.createElement('div');
            contentDiv.className = 'tabulator-col-content flex items-center justify-between gap-1 w-full';

            const titleSpan = document.createElement('span');
            titleSpan.className = 'tabulator-col-title truncate';
            titleSpan.textContent = cell.title;
            contentDiv.appendChild(titleSpan);

            if (cell.sortable) {
              classes.push('cursor-pointer', 'hover:bg-paper/80');
              const sorterSpan = document.createElement('span');
              sorterSpan.className = 'tabulator-col-sorter shrink-0 text-[10px] text-ink/40 hover:text-ink';
              if (this._sortState.field === cell.field) {
                if (this._sortState.dir === 'asc') {
                  sorterSpan.innerHTML = '<i class="ti ti-arrow-up text-moss-600 font-bold"></i>';
                } else if (this._sortState.dir === 'desc') {
                  sorterSpan.innerHTML = '<i class="ti ti-arrow-down text-moss-600 font-bold"></i>';
                }
              } else {
                sorterSpan.innerHTML = '<i class="ti ti-arrows-sort opacity-40"></i>';
              }
              contentDiv.appendChild(sorterSpan);

              th.onclick = (e) => {
                if (e.target.classList.contains('col-resizer')) return;
                if (this._sortState.field === cell.field) {
                  if (this._sortState.dir === 'asc') this._sortState.dir = 'desc';
                  else if (this._sortState.dir === 'desc') this._sortState.dir = null;
                  else this._sortState.dir = 'asc';
                } else {
                  this._sortState = { field: cell.field, dir: 'asc' };
                }
                this.render();
                this._emit('sortChanged', this._sortState.field, this._sortState.dir);
              };
            }

            if (this.options.resizableColumns) {
              const resizer = document.createElement('div');
              resizer.className = 'col-resizer absolute right-0 top-0 bottom-0 w-2 cursor-col-resize hover:bg-moss-500/40 z-30';
              resizer.onmousedown = (e) => this._startResize(e, cell.field, th);
              th.appendChild(resizer);
              th.classList.add('relative');
            }

            if (this.options.movableColumns && !cell.frozen) {
              th.draggable = true;
              th.ondragstart = (e) => {
                e.dataTransfer.setData('text/plain', cell.field);
                th.classList.add('opacity-50');
              };
              th.ondragend = () => th.classList.remove('opacity-50');
              th.ondragover = (e) => e.preventDefault();
              th.ondrop = (e) => {
                e.preventDefault();
                const srcField = e.dataTransfer.getData('text/plain');
                if (srcField && srcField !== cell.field) {
                  this._reorderLeafColumns(srcField, cell.field);
                }
              };
            }

            th.appendChild(contentDiv);
          } else {
            const titleSpan = document.createElement('span');
            titleSpan.className = 'tabulator-col-title truncate block py-0.5';
            titleSpan.textContent = cell.title;
            th.appendChild(titleSpan);
          }

          th.className = classes.join(' ');
          tr.appendChild(th);
        });
        thead.appendChild(tr);
      });
      tableEl.appendChild(thead);

      // 2. BODY (tbody)
      const tbody = document.createElement('tbody');
      tbody.className = 'tabulator-table';

      const processedRows = this._getProcessedRows();
      const totalRowsCount = processedRows.length;

      let pageRows = processedRows;
      if (this.options.pagination && this._pageSize > 0) {
        const totalPages = Math.max(1, Math.ceil(totalRowsCount / this._pageSize));
        if (this._currentPage > totalPages) this._currentPage = totalPages;
        const start = (this._currentPage - 1) * this._pageSize;
        pageRows = processedRows.slice(start, start + this._pageSize);
      }

      if (!pageRows.length) {
        const emptyTr = document.createElement('tr');
        const emptyTd = document.createElement('td');
        emptyTd.colSpan = leafColumns.length;
        emptyTd.className = 'text-center py-8 text-ink/40 text-xs italic';
        emptyTd.textContent = 'Sin datos disponibles';
        emptyTr.appendChild(emptyTd);
        tbody.appendChild(emptyTr);
      } else {
        pageRows.forEach((row, rIdx) => {
          const tr = document.createElement('tr');
          let trClasses = ['tabulator-row', 'border-b', 'border-line/40', 'hover:bg-paper/60', 'transition-colors'];
          if (rIdx % 2 === 1) trClasses.push('tabulator-row-even', 'bg-paper/20');
          if (row.__subtotal) trClasses.push('tabulator-row-subtotal', 'bg-moss-50/70', 'font-semibold', 'text-moss-900');

          tr.className = trClasses.join(' ');

          leafColumns.forEach(col => {
            const td = document.createElement('td');
            td.setAttribute('data-field', col.field);

            let tdClasses = ['tabulator-cell', 'p-2', 'align-middle', 'border-r', 'border-line/30', 'truncate', 'box-border'];
            if (col.cssClass) tdClasses.push(col.cssClass);

            if (col.numeric || col.hozAlign === 'right') tdClasses.push('text-right', 'tb-num');
            if (col.frozen) {
              tdClasses.push('tabulator-frozen', 'sticky', 'bg-white', 'z-10');
              if (row.__subtotal) tdClasses.push('!bg-moss-50');
              const leftPos = frozenOffsets.get(col.field) || 0;
              td.style.position = 'sticky';
              td.style.left = `${leftPos}px`;
            }

            const rawVal = row[col.field];
            let formattedVal = rawVal;

            if (typeof col.formatter === 'function') {
              formattedVal = col.formatter({
                getValue: () => rawVal,
                getRow: () => ({ getData: () => row }),
              });
            } else if (rawVal === null || rawVal === undefined) {
              formattedVal = '';
            } else if (typeof rawVal === 'number' && typeof formatNumber === 'function') {
              formattedVal = formatNumber(rawVal);
            }

            if (typeof formattedVal === 'string' && formattedVal.startsWith('<')) {
              td.innerHTML = formattedVal;
            } else {
              td.textContent = formattedVal !== null && formattedVal !== undefined ? String(formattedVal) : '';
            }

            td.className = tdClasses.join(' ');
            tr.appendChild(td);
          });

          if (typeof this.options.rowFormatter === 'function') {
            this.options.rowFormatter({
              getElement: () => tr,
              getData: () => row,
            });
          }

          tbody.appendChild(tr);
        });
      }
      tableEl.appendChild(tbody);

      // 3. BOTTOM CALCS / TOTALS (tfoot)
      if (this.options.columnCalcs || leafColumns.some(c => c.bottomCalc)) {
        const tfoot = document.createElement('tfoot');
        tfoot.className = 'sticky bottom-0 z-20 bg-paper border-t-2 border-line/80 font-bold shadow-xs';

        const tr = document.createElement('tr');
        tr.className = 'tabulator-row tabulator-calcs bg-paper';

        leafColumns.forEach(col => {
          const td = document.createElement('td');
          td.setAttribute('data-field', col.field);

          let tdClasses = ['tabulator-cell', 'p-2', 'align-middle', 'border-r', 'border-line/40', 'box-border'];
          if (col.cssClass) tdClasses.push(col.cssClass);

          if (col.numeric || col.hozAlign === 'right') tdClasses.push('text-right', 'tb-num');
          if (col.frozen) {
            tdClasses.push('tabulator-frozen', 'sticky', 'bg-paper', 'z-25');
            const leftPos = frozenOffsets.get(col.field) || 0;
            td.style.position = 'sticky';
            td.style.left = `${leftPos}px`;
          }

          let calcVal = null;
          if (typeof col.bottomCalc === 'function') {
            calcVal = col.bottomCalc();
          }

          let formattedVal = calcVal;
          if (typeof col.bottomCalcFormatter === 'function') {
            formattedVal = col.bottomCalcFormatter({ getValue: () => calcVal });
          } else if (calcVal !== null && calcVal !== undefined && typeof formatNumber === 'function' && typeof calcVal === 'number') {
            formattedVal = formatNumber(calcVal);
          }

          td.textContent = formattedVal !== null && formattedVal !== undefined ? String(formattedVal) : '';
          td.className = tdClasses.join(' ');

          tr.appendChild(td);
        });
        tfoot.appendChild(tr);
        tableEl.appendChild(tfoot);
      }

      scrollWrapper.appendChild(tableEl);
      container.appendChild(scrollWrapper);

      // 4. FOOTER & PAGINATION BAR
      if (this.options.pagination || this.options.footerElement) {
        const footerBar = document.createElement('div');
        footerBar.className = 'custom-table-footer flex items-center justify-between gap-3 px-3 py-2 border-t border-line bg-paper text-xs text-ink/70 shrink-0 select-none z-30 shadow-2xs';

        const infoDiv = document.createElement('div');
        infoDiv.className = 'footer-info flex items-center gap-3 min-w-0';

        if (this.options.footerElement) {
          infoDiv.innerHTML = typeof this.options.footerElement === 'string' ? this.options.footerElement : '';
        } else if (this.options.pagination) {
          const start = totalRowsCount === 0 ? 0 : (this._currentPage - 1) * this._pageSize + 1;
          const end = Math.min(totalRowsCount, this._currentPage * this._pageSize);
          infoDiv.innerHTML = `Mostrando <strong class="text-ink font-semibold">${start}–${end}</strong> de <strong class="text-ink font-semibold">${totalRowsCount}</strong> filas`;
        }

        if (this.options.pagination) {
          const pageSizeLabel = document.createElement('label');
          pageSizeLabel.className = 'flex items-center gap-1.5 text-[11px] text-ink/50 border-l border-line/80 pl-3 shrink-0';
          pageSizeLabel.innerHTML = `<span>Filas:</span>`;

          const pageSizeSelect = document.createElement('select');
          pageSizeSelect.className = 'page-size-select text-[11px] font-medium bg-white border border-line rounded px-1.5 py-0.5 text-ink cursor-pointer focus:outline-none focus:border-moss-500';
          [10, 25, 50, 100].forEach(size => {
            const opt = document.createElement('option');
            opt.value = size;
            opt.textContent = size;
            if (size === this._pageSize) opt.selected = true;
            pageSizeSelect.appendChild(opt);
          });

          pageSizeSelect.onchange = (e) => {
            this._pageSize = Number(e.target.value);
            this._currentPage = 1;
            this.render();
          };

          pageSizeLabel.appendChild(pageSizeSelect);
          infoDiv.appendChild(pageSizeLabel);
        }

        footerBar.appendChild(infoDiv);

        if (this.options.pagination) {
          const totalPages = Math.max(1, Math.ceil(totalRowsCount / this._pageSize));

          const paginator = document.createElement('div');
          paginator.className = 'tabulator-paginator flex items-center gap-1 shrink-0';

          const createBtn = (html, label, disabled, onClick) => {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.innerHTML = html;
            btn.title = label;
            btn.disabled = disabled;
            btn.className = 'w-7 h-7 rounded-md border border-line bg-white hover:bg-paper hover:border-moss-500 disabled:opacity-30 disabled:hover:bg-white disabled:hover:border-line disabled:cursor-not-allowed transition flex items-center justify-center text-xs font-medium cursor-pointer shadow-2xs';
            btn.onclick = onClick;
            return btn;
          };

          paginator.appendChild(createBtn('<i class="ti ti-chevron-left text-xs"></i>', 'Página anterior', this._currentPage <= 1, () => {
            if (this._currentPage > 1) {
              this._currentPage--;
              this.render();
            }
          }));

          const pageIndicator = document.createElement('span');
          pageIndicator.className = 'px-2 text-[11px] font-mono font-semibold text-ink';
          pageIndicator.textContent = `${this._currentPage} / ${totalPages}`;
          paginator.appendChild(pageIndicator);

          paginator.appendChild(createBtn('<i class="ti ti-chevron-right text-xs"></i>', 'Página siguiente', this._currentPage >= totalPages, () => {
            if (this._currentPage < totalPages) {
              this._currentPage++;
              this.render();
            }
          }));

          footerBar.appendChild(paginator);
        }

        container.appendChild(footerBar);
      }

      this.element.appendChild(container);
      this._emit('renderComplete');
    }

    _startResize(e, field, thEl) {
      e.preventDefault();
      e.stopPropagation();

      const startX = e.clientX;
      const startWidth = thEl.offsetWidth || this._getColWidth({ field });

      const onMouseMove = (moveEv) => {
        const delta = moveEv.clientX - startX;
        const newW = Math.max(40, startWidth + delta);
        this._columnWidths[field] = newW;

        const colEl = this.element.querySelector(`col[data-field="${field}"]`);
        if (colEl) {
          colEl.style.width = `${newW}px`;
          colEl.style.minWidth = `${newW}px`;
        }

        this._updateFrozenOffsets();
      };

      const onMouseUp = () => {
        window.removeEventListener('mousemove', onMouseMove);
        window.removeEventListener('mouseup', onMouseUp);
        this._emit('columnResized', field, this._columnWidths[field]);
      };

      window.addEventListener('mousemove', onMouseMove);
      window.addEventListener('mouseup', onMouseUp);
    }

    _reorderLeafColumns(srcField, targetField) {
      const leaves = this._getLeafColumns(this._rawColumns);
      const srcIdx = leaves.findIndex(c => c.field === srcField);
      const targetIdx = leaves.findIndex(c => c.field === targetField);

      if (srcIdx < 0 || targetIdx < 0) return;

      const item = leaves.splice(srcIdx, 1)[0];
      leaves.splice(targetIdx, 0, item);

      this._rawColumns = leaves;
      this.render();
      this._emit('columnMoved', item, leaves);
    }

    download(format = 'csv', filename = 'exportacion.csv') {
      if (format !== 'csv') return;
      const leaves = this._getLeafColumns(this._rawColumns);
      const headers = leaves.map(c => `"${(c.title || c.header || c.field || '').replace(/"/g, '""')}"`).join(',');
      const processed = this._getProcessedRows();

      const rows = processed.map(row => {
        return leaves.map(c => {
          let val = row[c.field];
          if (val === null || val === undefined) val = '';
          return `"${String(val).replace(/"/g, '""')}"`;
        }).join(',');
      });

      const csvContent = [headers, ...rows].join('\n');
      const blob = new Blob(['\ufeff' + csvContent], { type: 'text/csv;charset=utf-8;' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    }
  }

  window.CustomTable = CustomTable;
})();
