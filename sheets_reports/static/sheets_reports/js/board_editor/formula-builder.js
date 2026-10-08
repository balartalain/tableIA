// Constructor de fórmulas por bloques (pestaña «Campos calculados» del selector de fuentes).
// La fórmula es un árbol {kind, value, args} — el mismo que arma el parser del servidor
// (`formula_tree` en engine/formulas.py) — donde un hueco vacío es `null`. Cada bloque trae sus
// huecos (IF tres, SUM uno, «+» dos…), así que la fórmula siempre es sintácticamente válida;
// `FormulaBlocks.text` la escribe como texto para guardarla y para la vista previa.
//
// Reglas al soltar una pieza sobre un lugar del árbol (`place`):
// - en un hueco: se coloca;
// - sobre un bloque lleno, una pieza con huecos lo envuelve (el bloque pasa a su primer hueco:
//   [ventas] + «+» → [ventas] + [ ]); una pieza sin huecos (columna, valor) lo reemplaza.
//
// El arrastre usa interact.js con selectores delegados (sirve para los bloques que se vuelven
// a dibujar) y busca el destino bajo el puntero (`[data-fb-drop]`, el más interno). Al soltar
// avisa con el evento `formula:drop` ({source, target}); el selector de fuentes aplica el cambio.
const FormulaBlocks = (() => {
  const AGGREGATES = {
    SUM: 'Suma', AVG: 'Promedio', COUNT: 'Conteo (valores no vacíos)',
    COUNT_DISTINCT: 'Valores distintos', MIN: 'Mínimo', MAX: 'Máximo',
  };
  const COMPARE = ['=', '!=', '>', '>=', '<', '<='];
  const ARITHMETIC = ['+', '-', '*', '/'];
  const LOGIC = ['AND', 'OR'];
  const SYMBOL = { '!=': '≠', '>=': '≥', '<=': '≤', '-': '−', '*': '×', '/': '÷', AND: 'Y', OR: 'O' };

  // ---------------------------------------------------------------- piezas
  const node = (kind, value = null, args = []) => ({ kind, value, args });
  const pieces = {
    column: name => node('col', name),
    number: n => (n < 0 ? node('neg', null, [node('num', -n)]) : node('num', n)),
    text: s => node('str', s),
    compare: () => node('bin', '=', [null, null]),
    logic: op => node('bin', op, [null, null]),
    not: () => node('not', null, [null]),
    arithmetic: op => node('bin', op, [null, null]),
    func: name => node('func', name, name === 'IF' ? [null, null, null] : [null]),
  };

  // ---------------------------------------------------------------- texto
  // Precedencia del parser (_Parser): OR < AND < NOT < comparación < + - < * / < unario.
  function precedence(n) {
    if (n.kind === 'bin') {
      if (n.value === 'OR') return 1;
      if (n.value === 'AND') return 2;
      if (COMPARE.includes(n.value)) return 4;
      return n.value === '+' || n.value === '-' ? 5 : 6;
    }
    return { not: 3, neg: 7 }[n.kind] || 8;
  }

  function formatNumber(value) {
    return Number.isInteger(value) ? String(value) : String(Number(value));
  }

  // La fórmula como texto; null si queda algún hueco.
  function text(n) {
    if (n == null) return null;
    const sub = (child, wrap) => {
      const t = text(child);
      return t == null ? null : (wrap ? `(${t})` : t);
    };
    const parts = [];
    switch (n.kind) {
      case 'num': return formatNumber(n.value);
      case 'str': return String(n.value).includes('"') ? `'${n.value}'` : `"${n.value}"`;
      case 'col': return `[${n.value}]`;
      case 'neg': {
        const t = sub(n.args[0], n.args[0] && precedence(n.args[0]) < 7);
        return t == null ? null : `-${t}`;
      }
      case 'not': {
        const t = sub(n.args[0], n.args[0] && precedence(n.args[0]) < 3);
        return t == null ? null : `NOT ${t}`;
      }
      case 'func':
        for (const arg of n.args) {
          const t = text(arg);
          if (t == null) return null;
          parts.push(t);
        }
        return `${n.value}(${parts.join(', ')})`;
      case 'bin': {
        const p = precedence(n);
        const [left, right] = n.args;
        // La comparación no se encadena; a la derecha, la misma precedencia va entre paréntesis
        // para que el árbol vuelva igual.
        const l = sub(left, left && (precedence(left) < p || (p === 4 && precedence(left) === 4)));
        const r = sub(right, right && precedence(right) <= p);
        return l == null || r == null ? null : `${l} ${n.value} ${r}`;
      }
      default: return null;
    }
  }

  // ---------------------------------------------------------------- árbol
  const parsePath = s => (s ? s.split('.').map(Number) : []);
  const pathKey = path => path.join('.');
  const isInside = (path, ancestor) => ancestor.length <= path.length && ancestor.every((v, i) => path[i] === v);

  function getAt(root, path) {
    let n = root;
    for (const i of path) n = n ? n.args[i] : null;
    return n ?? null;
  }

  // Pone `value` en `path` (vacía = la raíz); devuelve la raíz nueva.
  function setAt(root, path, value) {
    if (!path.length) return value;
    getAt(root, path.slice(0, -1)).args[path[path.length - 1]] = value;
    return root;
  }

  const clone = n => (n == null ? null : JSON.parse(JSON.stringify(n)));

  // Coloca `piece` en `path` con las reglas de arriba; devuelve {root, path del bloque colocado}.
  function place(root, path, piece) {
    const target = getAt(root, path);
    const hole = piece.args.indexOf(null);
    if (target != null && hole >= 0) piece.args[hole] = target;
    return { root: setAt(root, path, piece), path };
  }

  // El primer hueco desde `path` (en orden de lectura) o, si no hay, el primero de todo el árbol.
  function nextHole(root, path = []) {
    const find = (n, at) => {
      if (n == null) return at;
      for (let i = 0; i < n.args.length; i++) {
        const found = find(n.args[i], [...at, i]);
        if (found) return found;
      }
      return null;
    };
    return find(getAt(root, path), path) || find(root, []);
  }

  function renameColumns(n, mapping) {
    if (!n) return;
    if (n.kind === 'col' && mapping[n.value]) n.value = mapping[n.value];
    n.args.forEach(arg => renameColumns(arg, mapping));
  }

  // ---------------------------------------------------------------- dibujo
  const el = (tag, className, textContent) => {
    const e = document.createElement(tag);
    if (className) e.className = className;
    if (textContent != null) e.textContent = textContent;
    return e;
  };

  // Dibuja `root` en `container`. `selected`: la ruta elegida con clic (o null).
  // `actions`: {select(path), remove(path), setOp(path, op)}.
  function render(container, root, selected, actions) {
    container.replaceChildren(draw(root, [], selected, actions));
  }

  function draw(n, path, selected, actions) {
    const key = pathKey(path);
    const isSelected = selected != null && pathKey(selected) === key;
    if (n == null) {
      const slot = el('span', 'fb-slot inline-flex items-center justify-center min-w-[5.5rem] h-7 px-2 rounded-md border border-dashed text-[11px] cursor-pointer select-none '
        + (isSelected ? 'border-moss-500 bg-moss-50 text-moss-700' : 'border-ink/25 text-ink/35 hover:border-moss-400'),
        'suelta aquí');
      slot.dataset.fbDrop = key;
      slot.addEventListener('click', e => { e.stopPropagation(); actions.select(path); });
      return slot;
    }

    const leaf = !n.args.length || (n.kind === 'neg' && n.args[0] && n.args[0].kind === 'num');
    const ring = isSelected ? ' fb-selected ring-2 ring-moss-500 ring-offset-1' : '';
    let block;
    if (leaf) {
      const styles = {
        col: 'bg-moss-50 border-moss-200 text-moss-800 font-medium',
        str: 'bg-amber-50 border-amber-200 text-amber-900 font-mono',
      };
      block = el('span', `fb-block inline-flex items-center gap-1 h-7 pl-2 pr-1 rounded-md border text-xs ${styles[n.kind] || 'bg-sky-50 border-sky-200 text-sky-900 font-mono'}${ring}`);
      block.append(el('span', 'truncate max-w-[14rem]', leafLabel(n)));
    } else if (n.kind === 'func' && n.value === 'IF') {
      block = el('div', `fb-block inline-flex flex-col gap-1.5 rounded-lg border border-violet-200 bg-violet-50/60 p-2 text-xs${ring}`);
      const head = el('div', 'fb-head flex items-center gap-1');
      head.append(el('span', 'font-semibold text-violet-700', 'SI'), removeButton(path, actions));
      block.append(head);
      ['condición', 'si se cumple', 'si no'].forEach((label, i) => {
        const row = el('div', 'flex items-start gap-2 pl-2');
        row.append(el('span', 'w-20 shrink-0 pt-1.5 text-[11px] text-ink/50', label),
                   draw(n.args[i], [...path, i], selected, actions));
        block.append(row);
      });
    } else {
      block = el('span', `fb-block inline-flex flex-wrap items-center gap-1 rounded-lg border px-1.5 py-1 text-xs ${n.kind === 'func' ? 'border-sky-200 bg-sky-50/60' : 'border-line bg-white'}${ring}`);
      if (n.kind === 'func') {
        const name = el('span', 'font-semibold text-sky-700 font-mono', n.value);
        name.title = AGGREGATES[n.value] || '';
        block.append(name, el('span', 'text-ink/40', '('), draw(n.args[0], [...path, 0], selected, actions),
                     el('span', 'text-ink/40', ')'));
      } else if (n.kind === 'not' || n.kind === 'neg') {
        block.append(el('span', 'font-semibold text-ink/70', n.kind === 'not' ? 'NO' : '−'),
                     draw(n.args[0], [...path, 0], selected, actions));
      } else {
        block.append(draw(n.args[0], [...path, 0], selected, actions), opSelect(n, path, actions),
                     draw(n.args[1], [...path, 1], selected, actions));
      }
      block.append(removeButton(path, actions));
    }
    if (leaf) block.append(removeButton(path, actions));
    block.dataset.fbDrop = key;
    block.dataset.fbDrag = key;
    block.style.touchAction = 'none';
    block.classList.add('cursor-grab');
    block.addEventListener('click', e => {
      if (e.target.closest('button, select')) return;
      e.stopPropagation();
      actions.select(path);
    });
    return block;
  }

  function leafLabel(n) {
    if (n.kind === 'col') return n.value;
    if (n.kind === 'str') return `"${n.value}"`;
    if (n.kind === 'neg') return `−${formatNumber(n.args[0].value)}`;
    return formatNumber(n.value);
  }

  // El operador de un bloque se cambia sin rearmarlo (solo dentro de su familia).
  function opSelect(n, path, actions) {
    const family = [COMPARE, ARITHMETIC, LOGIC].find(ops => ops.includes(n.value));
    const select = el('select', 'text-xs font-semibold rounded border border-line bg-paper px-1 py-0.5 cursor-pointer focus:outline-none focus:border-moss-500');
    select.setAttribute('aria-label', 'Operador');
    family.forEach(op => {
      const option = el('option', '', SYMBOL[op] || op);
      option.value = op;
      option.selected = op === n.value;
      select.append(option);
    });
    select.addEventListener('change', () => actions.setOp(path, select.value));
    return select;
  }

  function removeButton(path, actions) {
    // Se ve al pasar sobre su bloque (o al elegirlo): junto al «×» de multiplicar confundiría.
    const button = el('button', 'fb-remove p-0.5 rounded text-ink/40 hover:text-red-600 hover:bg-red-50 cursor-pointer leading-none');
    button.type = 'button';
    button.title = 'Quitar';
    button.setAttribute('aria-label', 'Quitar');
    button.append(el('i', 'ti ti-x text-xs'));
    button.addEventListener('click', e => { e.stopPropagation(); actions.remove(path); });
    return button;
  }

  // ---------------------------------------------------------------- arrastre
  // Origen: una pieza del panel (`data-fb-piece`, JSON) o un bloque del canvas (`data-fb-drag`,
  // su ruta). Destino: el `[data-fb-drop]` más interno bajo el puntero, o el panel
  // (`[data-fb-trash]`, para quitar un bloque).
  let ghost = null;
  let highlighted = null;
  let lastDragEnd = 0;

  function targetAt(x, y) {
    const under = document.elementFromPoint(x, y);
    if (!under) return null;
    const trash = under.closest('[data-fb-trash]');
    if (trash) return { trash: true, element: trash };
    const drop = under.closest('[data-fb-drop]');
    return drop ? { path: parsePath(drop.dataset.fbDrop), element: drop } : null;
  }

  function highlight(element) {
    if (highlighted === element) return;
    if (highlighted) highlighted.classList.remove('fb-over');
    highlighted = element;
    if (highlighted) highlighted.classList.add('fb-over');
  }

  function sourceOf(element) {
    if (element.dataset.fbPiece) return { piece: JSON.parse(element.dataset.fbPiece) };
    return { path: parsePath(element.dataset.fbDrag) };
  }

  function setupDrag() {
    if (typeof interact === 'undefined' || setupDrag.done) return;
    setupDrag.done = true;
    interact('[data-fb-piece], [data-fb-drag]').draggable({
      listeners: {
        start(event) {
          const label = (event.target.querySelector('.truncate') || event.target).textContent.trim();
          ghost = el('div', 'fixed z-[100] pointer-events-none rounded-md border border-moss-300 bg-white/95 px-2 py-1 text-xs font-medium text-ink shadow-lg max-w-[16rem] truncate',
                     label.slice(0, 60));
          document.body.append(ghost);
          event.target.classList.add('opacity-40');
        },
        move(event) {
          ghost.style.left = `${event.client.x + 12}px`;
          ghost.style.top = `${event.client.y + 12}px`;
          const target = targetAt(event.client.x, event.client.y);
          highlight(target ? target.element : null);
        },
        end(event) {
          event.target.classList.remove('opacity-40');
          if (ghost) ghost.remove();
          ghost = null;
          highlight(null);
          lastDragEnd = Date.now();
          const target = targetAt(event.client.x, event.client.y);
          if (!target) return;
          window.dispatchEvent(new CustomEvent('formula:drop', {
            detail: { source: sourceOf(event.target), target: target.trash ? { trash: true } : { path: target.path } },
          }));
        },
      },
    });
  }

  // Un clic que llega justo al terminar un arrastre no cuenta como clic.
  const justDragged = () => Date.now() - lastDragEnd < 300;

  return {
    AGGREGATES, COMPARE, ARITHMETIC, SYMBOL, pieces, text, getAt, setAt, place, nextHole,
    isInside, clone, renameColumns, render, setupDrag, justDragged,
  };
})();
