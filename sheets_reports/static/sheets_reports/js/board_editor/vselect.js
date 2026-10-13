// Directiva `x-vselect`: viste un <select> nativo del editor con un VirtualSelect con buscador.
// El select se queda en el DOM (oculto) como fuente de verdad: Alpine sigue manejando sus
// opciones (x-for), su valor (x-model / x-bind:value), `disabled` y `x-show`, y esta directiva
// los refleja en el VirtualSelect. Al elegir, pone el valor en el select y despacha `input` y
// `change`, así que los x-model y x-on:change existentes no cambian.
//
//   <select x-vselect x-model="c.type">…</select>
//   <select x-vselect="metric.format || ''" x-bind:value="metric.format || ''" x-on:change="…">…</select>
//
// Con x-bind:value (sin x-model) la directiva lleva la misma expresión: es lo que le avisa de
// que el valor cambió.
const VSELECT_TEXTS = {
  searchPlaceholderText: 'Buscar…',
  noOptionsText: 'Sin opciones',
  noSearchResultsText: 'Sin resultados',
};
// VirtualSelect trata el valor vacío como «nada elegido»: la opción de valor '' viaja con este.
const VSELECT_EMPTY = '__vselect_empty__';
// Por encima del panel lateral y del bottom sheet.
const VSELECT_Z_INDEX = 70;
// Clases del select que deciden su tamaño: pasan al VirtualSelect.
const VSELECT_LAYOUT_CLASS = /^(w-|min-w-|max-w-|flex-|shrink|grow|basis-|col-span-|text-(xs|sm|base)$)/;

document.addEventListener('alpine:init', () => {
  Alpine.directive('vselect', (el, { expression }, { effect, evaluateLater, cleanup }) => {
    if (el.tagName !== 'SELECT' || !window.VirtualSelect) return;
    const toVs = (value) => (value === '' ? VSELECT_EMPTY : value);
    const fromVs = (value) => (value === VSELECT_EMPTY ? '' : value);
    const option = (o) => ({ label: o.textContent.trim(), value: toVs(o.value) });
    // Las opciones del select, con sus <optgroup> como grupos.
    const readOptions = () => Array.from(el.children).flatMap(child => {
      if (child.tagName === 'OPTGROUP') return [{ label: child.label, options: Array.from(child.children).map(option) }];
      return child.tagName === 'OPTION' ? [option(child)] : [];
    });

    const mount = document.createElement('div');
    mount.className = ['vselect', ...Array.from(el.classList).filter(c => VSELECT_LAYOUT_CLASS.test(c))].join(' ');
    el.classList.add('vselect-native');
    el.tabIndex = -1;
    el.after(mount);
    // Dentro de un <label>, el clic no debe pasar al select nativo oculto.
    mount.addEventListener('click', (e) => e.preventDefault());

    let ready = false;
    let syncing = false;
    let signature = '';
    let frame = null;

    // Lleva al VirtualSelect lo que tenga ahora el select: opciones, valor, disabled y x-show.
    const refresh = () => {
      frame = null;
      if (!ready) return;
      syncing = true;
      try {
        const options = readOptions();
        const next = JSON.stringify(options);
        if (next !== signature) {
          signature = next;
          mount.setOptions(options, true);
        }
        const value = el.selectedIndex < 0 ? '' : toVs(el.value);
        if (mount.value !== value) mount.setValue(value, true);
        if (el.disabled) mount.disable(); else mount.enable();
        mount.style.display = el.style.display === 'none' ? 'none' : '';
      } finally {
        syncing = false;
      }
    };
    const schedule = () => {
      if (frame === null) frame = requestAnimationFrame(refresh);
    };

    // Las opciones las pinta x-for después de esta directiva: se monta en el siguiente ciclo.
    Alpine.nextTick(() => {
      if (!el.isConnected) return;
      VirtualSelect.init({
        ele: mount,
        options: [],
        search: true,
        multiple: false,
        hideClearButton: true,
        maxWidth: '100%',
        dropboxWrapper: 'body',
        zIndex: VSELECT_Z_INDEX,
        placeholder: '',
        ariaLabelText: el.getAttribute('aria-label') || '',
        ...VSELECT_TEXTS,
      });
      mount.addEventListener('change', () => {
        if (syncing) return;
        const value = fromVs(mount.value || '');
        if (el.value === value) return;
        el.value = value;
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
        schedule();
      });
      // Por si el valor cambió sin que nada avisara.
      mount.addEventListener('beforeOpen', refresh);
      ready = true;
      refresh();
    });

    // Avisos de cambio: el DOM del select (opciones, selected, disabled, style)…
    const observer = new MutationObserver(schedule);
    observer.observe(el, { childList: true, subtree: true, characterData: true, attributes: true });
    // …y el valor reactivo: el de x-model, o la expresión de la directiva.
    const read = expression ? evaluateLater(expression) : null;
    effect(() => {
      if (read) read(() => {});
      else if (el._x_model) el._x_model.get();
      schedule();
    });

    cleanup(() => {
      observer.disconnect();
      if (frame !== null) cancelAnimationFrame(frame);
      if (ready) mount.destroy();
      mount.remove();
    });
  });
});
