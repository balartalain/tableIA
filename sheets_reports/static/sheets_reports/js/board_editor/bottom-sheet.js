// Panel inferior (bottom sheet) del editor: contenedor genérico que abre, cierra y se
// redimensiona. En un tablero nuevo aloja el selector de la fuente de datos.
const BOTTOM_SHEET_DEFAULT_PCT = 95;
// Por debajo de esta altura (% del área disponible) soltar el asa cierra el panel.
const BOTTOM_SHEET_MIN_PCT = 15;

document.addEventListener('alpine:init', () => {
  Alpine.store('bottomSheet', {
    isOpen: false,
    heightPct: BOTTOM_SHEET_DEFAULT_PCT,
    title: '',
    // Sin él (tablero nuevo, sin fuente) no se cierra: la ✕ vuelve al inicio.
    dismissible: true,
    // Qué contenido muestra: 'sources' (fuentes de datos) | 'settings' (configuración) | 'email' (envío de correos).
    view: 'sources',
    // Sub-pestaña cuando view === 'settings': 'board' (configuración del tablero) | 'email' (Envío de correos).
    settingsTab: 'board',

    open({ title = '', dismissible = true, view = 'sources', settingsTab = 'board' } = {}) {
      this.heightPct = BOTTOM_SHEET_DEFAULT_PCT;
      this.title = title;
      this.view = view;
      this.settingsTab = settingsTab;
      this.dismissible = dismissible;
      this.isOpen = true;
      if (this.view === 'settings' && this.settingsTab === 'email') {
        window.dispatchEvent(new CustomEvent('email-campaign:open'));
      }
    },
    openSettings(tab = 'board') {
      this.open({ title: 'Configuración', view: 'settings', settingsTab: tab });
    },
    openSources() {
      this.open({ title: 'Gestionar fuente de datos', view: 'sources' });
      window.dispatchEvent(new CustomEvent('sources:open'));
    },
    switchSettingsTab(tab) {
      this.settingsTab = tab;
      if (tab === 'email') {
        window.dispatchEvent(new CustomEvent('email-campaign:open'));
      }
    },
    _dragCleanup: null,

    close() {
      if (this._dragCleanup) {
        this._dragCleanup();
      }
      if (!this.dismissible) {
        window.location.href = window.HOME_URL || '/';
        return;
      }
      this.isOpen = false;
    },
    toggle() { this.isOpen ? this.close() : this.open(); },

    // Arrastre del asa: la altura se mide contra #board-body (el área bajo el header).
    startDrag(e) {
      if (this._dragCleanup) {
        this._dragCleanup();
      }

      const handle = e.currentTarget;
      const pointerId = e.pointerId;
      const boardBody = document.getElementById('board-body');
      const area = boardBody ? boardBody.getBoundingClientRect() : { bottom: window.innerHeight, height: window.innerHeight };
      let pct = this.heightPct;
      let rafId = null;

      try {
        if (handle.setPointerCapture) {
          handle.setPointerCapture(pointerId);
        }
      } catch (err) {}

      const onMove = (ev) => {
        if (rafId !== null) return;
        rafId = requestAnimationFrame(() => {
          rafId = null;
          pct = ((area.bottom - ev.clientY) / area.height) * 100;
          this.heightPct = Math.min(100, Math.max(BOTTOM_SHEET_MIN_PCT, pct));
        });
      };

      const cleanup = (shouldFinish = true) => {
        if (rafId !== null) {
          cancelAnimationFrame(rafId);
          rafId = null;
        }
        window.removeEventListener('pointermove', onMove);
        window.removeEventListener('pointerup', onUp);
        window.removeEventListener('pointercancel', onUp);
        handle.removeEventListener('lostpointercapture', onUp);

        try {
          if (handle.hasPointerCapture && handle.hasPointerCapture(pointerId)) {
            handle.releasePointerCapture(pointerId);
          }
        } catch (err) {}

        this._dragCleanup = null;

        if (shouldFinish) {
          if (pct < BOTTOM_SHEET_MIN_PCT && this.dismissible) {
            this.close();
          } else {
            window.dispatchEvent(new CustomEvent('bottom-sheet:resized'));
          }
        }
      };

      const onUp = () => cleanup(true);
      this._dragCleanup = () => cleanup(false);

      window.addEventListener('pointermove', onMove);
      window.addEventListener('pointerup', onUp);
      window.addEventListener('pointercancel', onUp);
      handle.addEventListener('lostpointercapture', onUp);
    },
  });

  document.addEventListener('keydown', (e) => {
    const sheet = Alpine.store('bottomSheet');
    if (e.key === 'Escape' && sheet.isOpen && sheet.dismissible) sheet.close();
  });
});

// Comando temporal para DevTools mientras no haya botón en la UI:
// bottomSheet.open() / bottomSheet.close() / bottomSheet.toggle()
window.bottomSheet = {
  open: (options) => Alpine.store('bottomSheet').open(options),
  close: () => Alpine.store('bottomSheet').close(),
  toggle: () => Alpine.store('bottomSheet').toggle(),
};
