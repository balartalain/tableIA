// Panel inferior (bottom sheet) del editor. Será el contenedor de la vista previa de la hoja
// y del ajuste de tipos de columnas; por ahora solo abre, cierra y se redimensiona.
const BOTTOM_SHEET_DEFAULT_PCT = 80;
// Por debajo de esta altura (% del área disponible) soltar el asa cierra el panel.
const BOTTOM_SHEET_MIN_PCT = 15;

document.addEventListener('alpine:init', () => {
  Alpine.store('bottomSheet', {
    isOpen: false,
    heightPct: BOTTOM_SHEET_DEFAULT_PCT,

    open() {
      this.heightPct = BOTTOM_SHEET_DEFAULT_PCT;
      this.isOpen = true;
    },
    close() { this.isOpen = false; },
    toggle() { this.isOpen ? this.close() : this.open(); },

    // Arrastre del asa: la altura se mide contra #board-body (el área bajo el header).
    startDrag(e) {
      const handle = e.currentTarget;
      const area = document.getElementById('board-body').getBoundingClientRect();
      let pct = this.heightPct;
      handle.setPointerCapture(e.pointerId);

      const onMove = (ev) => {
        pct = ((area.bottom - ev.clientY) / area.height) * 100;
        this.heightPct = Math.min(100, Math.max(BOTTOM_SHEET_MIN_PCT, pct));
      };
      const onUp = () => {
        handle.removeEventListener('pointermove', onMove);
        handle.removeEventListener('pointerup', onUp);
        handle.removeEventListener('pointercancel', onUp);
        if (pct < BOTTOM_SHEET_MIN_PCT) this.close();
      };
      handle.addEventListener('pointermove', onMove);
      handle.addEventListener('pointerup', onUp);
      handle.addEventListener('pointercancel', onUp);
    },
  });

  document.addEventListener('keydown', (e) => {
    const sheet = Alpine.store('bottomSheet');
    if (e.key === 'Escape' && sheet.isOpen) sheet.close();
  });
});

// Comando temporal para DevTools mientras no haya botón en la UI:
// bottomSheet.open() / bottomSheet.close() / bottomSheet.toggle()
window.bottomSheet = {
  open: () => Alpine.store('bottomSheet').open(),
  close: () => Alpine.store('bottomSheet').close(),
  toggle: () => Alpine.store('bottomSheet').toggle(),
};
