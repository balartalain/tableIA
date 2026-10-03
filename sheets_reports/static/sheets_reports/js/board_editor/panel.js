// Panel de datos del editor, armado desde manifest.parts (WidgetType.manifest en el backend):
// cada pieza del data_spec declara su `ui` y partials/panel/part.html monta el componente de
// esa ui. Aquí vive lo que no depende de ninguna pieza en particular: las secciones del panel,
// el estado genérico de las piezas en el builder y las fuentes de opciones que salen de lo que
// el usuario ya eligió.
//
// Estado genérico (piezas sin adaptador propio en dashboard-store.js): el valor de la pieza
// en el builder es el mismo del data_spec, con la forma de su ui. Una pieza nueva de ui
// column-list, column-picker o field-group no necesita JS: su manifest basta.

// Piezas con adaptador escrito a mano en builderFromSpec/builderToPayload (alias de métricas,
// ids locales, conversiones de valores). El resto usa el genérico de su ui.
const CUSTOM_STATE_PARTS = new Set(['dimensions', 'pivots', 'columns', 'filters', 'metrics', 'having', 'sort']);

// Opciones que dependen de lo elegido en el panel (`options_from` de un campo). Reciben el
// store.
const PANEL_SOURCES = {
  sort_targets: (store) => store.sortOptions,
};

// Avisos de una pieza (`notes[].when`) que dependen de lo elegido en el panel.
const PANEL_CHECKS = {
  limit_without_metric_sort: (store) => store.limitNeedsMetricSort,
};

function panelParts(widgetClass) {
  return (widgetClass.manifest && widgetClass.manifest.parts) || [];
}

function panelPart(widgetClass, key) {
  return panelParts(widgetClass).find(p => p.key === key) || null;
}

// Secciones del panel, en el orden de las piezas: las seguidas con el mismo `group` van juntas
// bajo el título del grupo (en columnas); el resto, una por sección con el título de la pieza
// (vacío = sin título, ej. el orden y el Top N).
function panelSections(parts) {
  const sections = [];
  for (const part of parts) {
    const last = sections[sections.length - 1];
    if (part.group && last && last.grouped && last.key === part.group.key) {
      last.parts.push(part);
      continue;
    }
    sections.push(part.group
      ? { key: part.group.key, label: part.group.label, hint: part.group.hint || '', grouped: true, parts: [part] }
      : { key: part.key, label: part.label, hint: part.hint || '', grouped: false, parts: [part] });
  }
  return sections;
}

// --- Estado genérico por ui ----------------------------------------------------------------

function fieldIsEmpty(field, value) {
  if (value == null || String(value).trim() === '') return true;
  if (field.ui !== 'number') return false;
  const n = Number(value);
  return Number.isNaN(n) || (field.min != null && n < field.min);
}

function fieldGroupFromSpec(part, value) {
  const state = {};
  for (const f of part.item_fields || []) {
    const v = value ? value[f.key] : null;
    if (v == null) state[f.key] = f.ui === 'checkbox' ? false : (f.default ?? '');
    else state[f.key] = f.ui === 'number' ? String(v) : v;
  }
  return state;
}

// null si falta el campo `required` (la pieza queda sin valor, como un Top N sin número).
function fieldGroupToPayload(part, state) {
  if (!state) return null;
  const fields = part.item_fields || [];
  const required = fields.find(f => f.key === part.required);
  if (required && fieldIsEmpty(required, state[required.key])) return null;
  const out = {};
  for (const f of fields) {
    const v = state[f.key];
    if (f.ui === 'checkbox') out[f.key] = !!v;
    else if (f.ui === 'number') out[f.key] = fieldIsEmpty(f, v) ? null : Number(v);
    else out[f.key] = v === '' || v == null ? null : v;
  }
  return out;
}

function partStateFromSpec(part, value) {
  switch (part.ui) {
    case 'column-list': return (value || []).length ? [...value] : [''];
    case 'column-picker': return value || '';
    case 'field-group': return fieldGroupFromSpec(part, value);
    default: return value == null ? null : JSON.parse(JSON.stringify(value));
  }
}

function partStateToPayload(part, state, b) {
  switch (part.ui) {
    case 'column-list': return builderList(b, part.key);
    case 'column-picker': return state || null;
    case 'field-group': return fieldGroupToPayload(part, state);
    default: return state ?? null;
  }
}

// Columnas elegidas en la lista `key`, sin vacíos ni repetidos y sin las que ya tomó una lista
// anterior de sus `excludes` (una columna usada como fila no puede ser además columna).
// [] si el widget no tiene esa lista.
function builderList(b, key) {
  const parts = panelParts(builderClass(b));
  const i = parts.findIndex(p => p.key === key);
  if (i < 0) return [];
  const excludes = parts[i].excludes || [];
  const taken = new Set(parts.slice(0, i).filter(p => excludes.includes(p.key)).flatMap(p => chosen(b[p.key])));
  return chosen(b[key]).filter(v => !taken.has(v));
}
