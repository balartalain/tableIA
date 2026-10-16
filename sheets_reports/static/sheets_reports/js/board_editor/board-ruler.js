// Regla de columnas (1..12) y guías visuales del editor de tableros.
// Proporciona el componente Alpine `boardRuler()` para modularizar la regla,
// dibujar graduaciones sutiles tipo diseño/CAD, y resaltar las columnas
// activas durante el hover, arrastre de módulos o redimensión de widgets.

function boardRuler() {
  return {
    columns: Array.from({ length: 12 }, (_, i) => i + 1),
    activeCols: [],
    isHoveringRuler: false,
    hoveredCol: null,
    isDragging: false,
    isResizing: false,

    get showGuides() {
      return this.isHoveringRuler || this.isDragging || this.isResizing || this.activeCols.length > 0;
    },

    isColActive(col) {
      if (this.activeCols.includes(col)) return true;
      if (this.hoveredCol === col) return true;
      return false;
    },

    init() {
      // Escuchar eventos globales de resaltado desde drag/resize
      window.addEventListener('board:ruler:highlight', (e) => {
        this.handleHighlight(e.detail);
      });

      window.addEventListener('board:ruler:clear', () => {
        this.clearHighlight();
      });

      // Seguimiento en tiempo real del cursor durante el arrastre de widgets
      window.addEventListener('pointermove', (e) => {
        if (!window._activeDragSpan) return;
        const ghost = document.querySelector('.grid-ghost-preview');
        if (ghost) {
          this.highlightFromElement(ghost);
        } else {
          this.highlightFromPoint(e.clientX, window._activeDragSpan);
        }
      }, { passive: true });

      // Seguridad: al soltar cualquier arrastre o puntero fuera de rango
      window.addEventListener('pointerup', () => {
        if (window._activeDragSpan) {
          window._activeDragSpan = null;
          this.clearHighlight();
        }
      }, { passive: true });
    },

    handleHighlight(detail) {
      if (!detail) return;
      if (detail.isDragging !== undefined) this.isDragging = !!detail.isDragging;
      if (detail.isResizing !== undefined) this.isResizing = !!detail.isResizing;

      if (detail.cols && Array.isArray(detail.cols)) {
        this.activeCols = detail.cols.map(c => parseInt(c, 10)).filter(c => c >= 1 && c <= 12);
        return;
      }

      if (typeof detail.start === 'number' && typeof detail.end === 'number') {
        const start = Math.max(1, Math.min(12, detail.start));
        const end = Math.max(start, Math.min(12, detail.end));
        const cols = [];
        for (let i = start; i <= end; i++) cols.push(i);
        this.activeCols = cols;
        return;
      }

      if (detail.element) {
        this.highlightFromElement(detail.element, detail.span);
      }
    },

    highlightFromElement(el, forcedSpan = null) {
      if (!el) return;
      const rulerEl = document.getElementById('board-ruler');
      if (!rulerEl) return;

      const colEls = rulerEl.querySelectorAll('[data-col]');
      if (!colEls.length) return;

      const targetRect = el.getBoundingClientRect();
      const active = [];

      colEls.forEach(colEl => {
        const colNum = parseInt(colEl.getAttribute('data-col'), 10);
        const colRect = colEl.getBoundingClientRect();
        // Superposición horizontal entre el widget/ghost y la pista de la columna
        const overlap = Math.max(0, Math.min(targetRect.right, colRect.right) - Math.max(targetRect.left, colRect.left));
        if (overlap > 8 || (colRect.left >= targetRect.left - 4 && colRect.right <= targetRect.right + 4)) {
          active.push(colNum);
        }
      });

      if (forcedSpan && active.length) {
        const start = active[0];
        const end = Math.min(12, start + forcedSpan - 1);
        const filled = [];
        for (let i = start; i <= end; i++) filled.push(i);
        this.activeCols = filled;
      } else if (active.length) {
        this.activeCols = active;
      }
    },

    highlightFromPoint(clientX, span = 6) {
      const rulerEl = document.getElementById('board-ruler');
      if (!rulerEl) return;
      const colEls = rulerEl.querySelectorAll('[data-col]');
      if (!colEls.length) return;

      let hoveredIndex = -1;
      colEls.forEach((colEl, idx) => {
        const rect = colEl.getBoundingClientRect();
        if (clientX >= rect.left && clientX <= rect.right) {
          hoveredIndex = idx + 1;
        }
      });

      if (hoveredIndex > 0) {
        const maxStart = 12 - span + 1;
        const start = Math.max(1, Math.min(maxStart, hoveredIndex));
        const end = Math.min(12, start + span - 1);
        const cols = [];
        for (let i = start; i <= end; i++) cols.push(i);
        this.activeCols = cols;
        this.isDragging = true;
      }
    },

    clearHighlight() {
      this.activeCols = [];
      this.isDragging = false;
      this.isResizing = false;
    },

    onMouseEnterRuler() {
      this.isHoveringRuler = true;
    },

    onMouseLeaveRuler() {
      this.isHoveringRuler = false;
      this.hoveredCol = null;
    },

    onMouseEnterCol(col) {
      this.hoveredCol = col;
    },

    onMouseLeaveCol() {
      this.hoveredCol = null;
    },
  };
}
