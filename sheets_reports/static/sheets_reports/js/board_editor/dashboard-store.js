const AI_FETCH_TIMEOUT_MS = 120000;
const RENDER_FETCH_TIMEOUT_MS = 60000;

// Aborta si tarda demasiado y nunca truena por JSON inválido (p. ej. una página HTML de error
// devuelta por un timeout de gateway/proxy) — deja que quien llama decida el mensaje de error.
async function fetchJsonSafe(url, options = {}, timeoutMs = AI_FETCH_TIMEOUT_MS) {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const r = await fetch(url, { ...options, signal: controller.signal });
    const data = await r.json().catch(() => null);
    return { r, data };
  } finally {
    clearTimeout(timeoutId);
  }
}

// "Resumir por" (como en las tablas dinámicas de Sheets).
const AGG_OPTIONS = [
  { value: 'sum', label: 'Suma' },
  { value: 'count', label: 'Conteo' },
  { value: 'count_distinct', label: 'Contar únicos' },
  { value: 'avg', label: 'Promedio' },
  { value: 'min', label: 'Mínimo' },
  { value: 'max', label: 'Máximo' },
  { value: 'median', label: 'Mediana' },
];

// "Mostrar como". Qué opciones tienen sentido depende de si hay dimensión y pivote.
const SHOW_AS_LABELS = {
  value: 'Valor',
  pct_row: '% de la fila',
  pct_column: '% de la columna',
  pct_total: '% del total general',
};

// Tipos de métrica (spec_validation.METRIC_TYPES). "Por grupo" es solo del KPI.
const METRIC_TYPE_OPTIONS = [
  { value: 'agg', label: 'Resumir una columna' },
  { value: 'calc', label: 'Cálculo entre métricas' },
  { value: 'grouped', label: 'Por grupo (condición / ranking)' },
];

// Operaciones de una métrica calculada (spec_validation.CALC_OPS).
const CALC_OP_OPTIONS = [
  { value: 'sub', label: '− menos', word: 'dif' },
  { value: 'add', label: '+ más', word: 'suma' },
  { value: 'mul', label: '× por', word: 'prod' },
  { value: 'div', label: '÷ entre', word: 'div' },
  { value: 'ratio_pct', label: 'como % de', word: 'pct' },
  { value: 'diff_pct', label: 'variación % vs', word: 'var' },
];

// Resultado de una métrica por grupo (spec_validation.GROUP_RESULTS).
const GROUP_RESULT_OPTIONS = [
  { value: 'count', label: 'Cuántos grupos cumplen', word: 'grupos' },
  { value: 'pct_groups', label: '% de grupos que cumplen', word: 'pct_grupos' },
  { value: 'top', label: 'El grupo con el mayor…', word: 'top' },
  { value: 'bottom', label: 'El grupo con el menor…', word: 'menor' },
  { value: 'sum', label: 'Suma de…', word: 'suma' },
  { value: 'avg', label: 'Promedio de…', word: 'promedio' },
  { value: 'min', label: 'Mínimo de…', word: 'minimo' },
  { value: 'max', label: 'Máximo de…', word: 'maximo' },
];
const COUNT_RESULTS = ['count', 'pct_groups'];
const RANKING_RESULTS = ['top', 'bottom'];

// Operadores de las condiciones (spec_validation.FILTER_OPS). `numeric`: solo columnas numéricas.
const FILTER_OP_OPTIONS = [
  { value: 'eq', label: 'es igual a', short: '=' },
  { value: 'ne', label: 'es distinto de', short: '≠' },
  { value: 'gt', label: 'mayor que', short: '>', numeric: true },
  { value: 'gte', label: 'mayor o igual que', short: '≥', numeric: true },
  { value: 'lt', label: 'menor que', short: '<', numeric: true },
  { value: 'lte', label: 'menor o igual que', short: '≤', numeric: true },
  { value: 'between', label: 'está entre', short: 'entre', numeric: true },
  { value: 'in', label: 'es uno de', short: 'es uno de' },
  { value: 'not_in', label: 'no es ninguno de', short: 'no es ninguno de' },
  { value: 'contains', label: 'contiene', short: 'contiene' },
  { value: 'is_empty', label: 'está vacío', short: 'está vacío' },
  { value: 'not_empty', label: 'no está vacío', short: 'no está vacío' },
];
const LIST_OPS = ['in', 'not_in'];
const EMPTY_OPS = ['is_empty', 'not_empty'];
// Operadores que aceptan un valor relativo (spec_validation: eq, ne y comparaciones).
const RELATIVE_OPS = ['eq', 'ne', 'gt', 'gte', 'lt', 'lte'];

// De dónde sale el valor de una condición: fijo, o relativo (spec_validation.RELATIVE_VALUES).
const VALUE_MODE_OPTIONS = [
  { value: 'value', label: 'Valor fijo…' },
  { value: 'current_year', label: 'Año actual' },
  { value: 'previous_year', label: 'Año anterior' },
  { value: 'current_month', label: 'Mes actual (1-12)' },
  { value: 'max', label: 'Último valor de la columna' },
  { value: 'second_max', label: 'Penúltimo valor' },
  { value: 'min', label: 'Primer valor' },
];

// Comparaciones de las condiciones sobre grupos (having).
const COMPARE_OP_OPTIONS = FILTER_OP_OPTIONS.filter(o => RELATIVE_OPS.includes(o.value));

const OP_SHORT = Object.fromEntries(FILTER_OP_OPTIONS.map(o => [o.value, o.short]));

// Totales por nivel de filas/columnas (rowTotals/columnTotals) ajustados a `n` niveles;
// los que faltan quedan visibles.
function totalsLevels(list, n) {
  return Array.from({ length: n }, (_, i) => (list || [])[i] !== false);
}

// Filas y columnas elegidas en el builder, sin vacíos ni repetidos (una columna usada como
// fila no puede ser además columna de pivote).
function chosen(list) {
  return [...new Set((list || []).filter(Boolean))];
}

function builderDims(b) {
  return b.widget === 'kpi' ? [] : chosen(b.dimensions);
}

function builderPivots(b) {
  const dims = new Set(builderDims(b));
  return chosen(b.pivots).filter(p => !dims.has(p));
}

// count cuenta filas y no usa campo.
function isCountAgg(agg) {
  return agg === 'count';
}

// Identificador local y estable de cada métrica del builder: los cálculos, las condiciones
// sobre grupos, el orden y los roles del KPI se refieren a métricas por este id (su alias
// "as" cambia al cambiar la métrica); builderToPayload lo traduce al alias.
let _metricSeq = 0;
function newId() {
  _metricSeq += 1;
  return `m${_metricSeq}`;
}

function slug(text) {
  return String(text)
    .normalize('NFD').replace(/[̀-ͯ]/g, '')
    .toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '');
}

function asAlias(text) {
  const s = slug(text);
  return (/^[a-z]/.test(s) ? s : `m_${s}`).slice(0, 63);
}

// Nombre de columna resultante ("as") para una métrica agg: snake_case ASCII, como exige el
// schema (^[a-z][a-z0-9_]{0,62}$). count no depende del campo: siempre "cantidad". Las
// condiciones propias de la métrica se agregan al final (ej. total_ventas_2026).
function metricAlias(agg, field, showAs, suffix = '') {
  const pct = showAs && showAs !== 'value' ? 'pct_' : '';
  const prefix = {
    avg: 'promedio', min: 'minimo', max: 'maximo', median: 'mediana', count_distinct: 'unicos',
  }[agg] || 'total';
  const base = agg === 'count' ? `${pct}cantidad` : `${pct}${prefix}_${field}`;
  return asAlias(suffix ? `${base}_${suffix}` : base);
}

// Cabecera por defecto de una columna: el mismo humanize() del backend (spec_validation), para
// que el placeholder del campo "Nombre a mostrar" sea el título que se ve si no se personaliza.
function defaultColumnName(name) {
  const text = String(name || '').replace(/_/g, ' ').trim();
  return text ? text[0].toUpperCase() + text.slice(1) : '';
}

// --- Condiciones -----------------------------------------------------------------------

// `_k`: clave estable para los x-for del panel (no se envía).
function newCondition(field = '') {
  return { _k: newId(), field, op: 'eq', mode: 'value', value: '', value2: '' };
}

function conditionFromSpec(f) {
  const c = newCondition(f.field);
  c.op = f.op;
  if (f.relative) c.mode = f.relative;
  if (f.op === 'between' && Array.isArray(f.value)) {
    [c.value, c.value2] = f.value.map(String);
  } else if (Array.isArray(f.value)) {
    c.value = f.value.join(', ');
  } else if (f.value !== undefined && f.value !== null) {
    c.value = String(f.value);
  }
  return c;
}

// Valor tipado según la columna: en una numérica, "2026" se envía como 2026.
function typedValue(text, numeric) {
  const v = String(text ?? '').trim();
  if (numeric && v !== '' && !Number.isNaN(Number(v))) return Number(v);
  return v;
}

// Condición del builder -> condición del spec; null si está incompleta (no se envía).
function conditionToSpec(c, numericFields) {
  if (!c.field || !c.op) return null;
  const numeric = numericFields.has(c.field);
  if (EMPTY_OPS.includes(c.op)) return { field: c.field, op: c.op };
  if (RELATIVE_OPS.includes(c.op) && c.mode && c.mode !== 'value') {
    return { field: c.field, op: c.op, relative: c.mode };
  }
  if (LIST_OPS.includes(c.op)) {
    const values = String(c.value || '').split(',').map(v => typedValue(v, numeric)).filter(v => v !== '');
    return values.length ? { field: c.field, op: c.op, value: values } : null;
  }
  if (c.op === 'between') {
    const range = [c.value, c.value2].map(v => (String(v ?? '').trim() === '' ? NaN : Number(v)));
    return range.some(Number.isNaN) ? null : { field: c.field, op: c.op, value: range };
  }
  const value = typedValue(c.value, numeric);
  return value === '' ? null : { field: c.field, op: c.op, value };
}

function conditionsToSpec(list, numericFields) {
  return (list || []).map(c => conditionToSpec(c, numericFields)).filter(Boolean);
}

function describeCondition(f) {
  if (EMPTY_OPS.includes(f.op)) return `${f.field} ${OP_SHORT[f.op]}`;
  const value = f.relative
    ? (VALUE_MODE_OPTIONS.find(o => o.value === f.relative) || { label: f.relative }).label.toLowerCase()
    : Array.isArray(f.value) ? f.value.join(f.op === 'between' ? ' y ' : ', ') : f.value;
  return `${f.field} ${OP_SHORT[f.op] || f.op} ${value}`;
}

// --- Condiciones sobre grupos (having) -------------------------------------------------

function newGroupCondition(left = '') {
  return { _k: newId(), left, op: 'lt', rightKind: 'metric', right: '' };
}

// `idOf`: alias -> id local de la métrica.
function groupConditionFromSpec(h, idOf) {
  const numeric = typeof h.right === 'number';
  return {
    _k: newId(),
    left: idOf[h.left] || '',
    op: h.op,
    rightKind: numeric ? 'number' : 'metric',
    right: numeric ? String(h.right) : (idOf[h.right] || ''),
  };
}

// `aliasOf`: id local -> alias; null si está incompleta.
function groupConditionToSpec(h, aliasOf) {
  const left = aliasOf[h.left];
  if (!left) return null;
  if (h.rightKind === 'number') {
    const right = Number(h.right);
    return String(h.right ?? '').trim() === '' || Number.isNaN(right) ? null : { left, op: h.op, right };
  }
  const right = aliasOf[h.right];
  return right ? { left, op: h.op, right } : null;
}

// --- Métricas --------------------------------------------------------------------------

// Métrica del builder con los campos de todos los tipos: cambiar de tipo no pierde nada.
function newMetric(type = 'agg', numericFields = []) {
  const firstNumeric = numericFields[0] || '';
  return {
    _id: newId(), type, label: '', _origAs: '', _origSig: '',
    // agg
    agg: firstNumeric ? 'sum' : 'count', field: firstNumeric, show_as: 'value', filters: [], filtersOpen: false,
    // calc
    op: 'sub', left: '', rightKind: 'metric', right: '',
    // grouped
    group_by: '', inner: [newInnerMetric(numericFields)], having: [], result: 'count', value: '',
  };
}

function newInnerMetric(numericFields = []) {
  const firstNumeric = numericFields[0] || '';
  return { _id: newId(), agg: firstNumeric ? 'sum' : 'count', field: firstNumeric };
}

// Firma del contenido de una métrica: si no cambió, conserva su alias (y con él sus etiquetas).
const withoutKeys = (list) => JSON.stringify((list || []).map(({ _k, _id, ...rest }) => rest));

function metricSignature(m, b) {
  const pick = {
    agg: () => [m.agg, isCountAgg(m.agg) ? '' : m.field, effectiveShowAs(b, m.show_as), withoutKeys(m.filters)],
    calc: () => [m.op, m.left, m.rightKind, m.right],
    grouped: () => [m.group_by, JSON.stringify(m.inner), JSON.stringify(m.having), m.result, m.value, withoutKeys(m.filters)],
  }[m.type];
  return JSON.stringify([m.type, ...pick()]);
}

function metricFromSpec(raw, b, idOf) {
  const m = newMetric(raw.type);
  m._origAs = raw.as;
  if (raw.type === 'agg') {
    Object.assign(m, { agg: raw.agg, field: raw.field || '', show_as: raw.show_as || 'value' });
    m.filters = (raw.filters || []).map(conditionFromSpec);
    m.filtersOpen = m.filters.length > 0;
  } else if (raw.type === 'calc') {
    const numeric = typeof raw.right === 'number';
    Object.assign(m, {
      op: raw.op, left: idOf[raw.left] || '',
      rightKind: numeric ? 'number' : 'metric', right: numeric ? String(raw.right) : (idOf[raw.right] || ''),
    });
  } else {
    const innerIds = {};
    m.inner = (raw.inner || []).map(x => {
      const inner = { _id: newId(), agg: x.agg, field: x.field || '' };
      innerIds[x.as] = inner._id;
      return inner;
    });
    Object.assign(m, {
      group_by: raw.group_by, result: raw.result, value: innerIds[raw.value] || '',
      having: (raw.having || []).map(h => groupConditionFromSpec(h, innerIds)),
    });
    m.filters = (raw.filters || []).map(conditionFromSpec);
    m.filtersOpen = m.filters.length > 0;
  }
  idOf[raw.as] = m._id;
  return m;
}

// "Mostrar como" disponibles: en un KPI el % es contra los datos sin las condiciones;
// sin pivote, % de la fila siempre sería 100 y % de la columna = % del total.
function showAsOptions(b) {
  const hasDim = builderDims(b).length > 0;
  const hasPivot = builderPivots(b).length > 0;
  let values = ['value', 'pct_row', 'pct_column', 'pct_total'];
  if (!hasDim) values = ['value', 'pct_total'];
  else if (!hasPivot) values = ['value', 'pct_column'];
  return values.map(value => ({
    value,
    label: !hasDim && value === 'pct_total' ? '% sobre los datos sin las condiciones'
      : !hasPivot && value === 'pct_column' ? '% del total' : SHOW_AS_LABELS[value],
  }));
}

// Lleva `show_as` a una opción válida para la forma actual (ej. se quitó el pivote).
function effectiveShowAs(b, showAs) {
  if (!showAs || showAs === 'value') return 'value';
  if (!builderDims(b).length) return 'pct_total';
  if (!builderPivots(b).length) return 'pct_column';
  return showAs;
}

// Métricas que el builder puede enviar: el KPI es el único con métricas por grupo.
function activeMetrics(b) {
  return b.metrics.filter(m => m.type !== 'grouped' || b.widget === 'kpi');
}

function isRankingMetric(m) {
  return m.type === 'grouped' && RANKING_RESULTS.includes(m.result);
}

// Alias ("as") final de cada métrica del builder, en el mismo orden, y el mapa id -> alias.
// Una métrica aún incompleta (sin columna, cálculo sin operandos...) no tiene alias.
// Separado de builderToPayload para que la UI pueda mostrar el nombre por defecto de una
// métrica concreta, que depende también del orden (desempate de alias).
function metricAliases(b) {
  const used = new Set();
  const aliasOf = {};
  const unique = (alias) => {
    let candidate = alias;
    for (let i = 2; used.has(candidate); i++) candidate = `${alias}_${i}`.slice(0, 63);
    used.add(candidate);
    return candidate;
  };
  const list = b.metrics.map(m => {
    if (m.type === 'grouped' && b.widget !== 'kpi') return null;
    let alias = null;
    if (m.type === 'agg') {
      if (!m.agg || (!isCountAgg(m.agg) && !m.field)) return null;
      const suffix = (m.filters || []).map(c => (c.mode !== 'value' && RELATIVE_OPS.includes(c.op) ? c.mode : c.value))
        .filter(Boolean).join('_');
      alias = metricAlias(m.agg, m.field, effectiveShowAs(b, m.show_as), suffix);
    } else if (m.type === 'calc') {
      const left = aliasOf[m.left];
      const right = m.rightKind === 'number' ? String(m.right ?? '').trim() : aliasOf[m.right];
      if (!left || !right || (m.rightKind === 'number' && Number.isNaN(Number(right)))) return null;
      const word = (CALC_OP_OPTIONS.find(o => o.value === m.op) || { word: m.op }).word;
      alias = asAlias(`${word}_${left}_${right}`);
    } else {
      if (!m.group_by || !m.inner.some(x => x.agg && (isCountAgg(x.agg) || x.field))) return null;
      if (!COUNT_RESULTS.includes(m.result) && !m.inner.some(x => x._id === m.value)) return null;
      const word = (GROUP_RESULT_OPTIONS.find(o => o.value === m.result) || { word: m.result }).word;
      alias = asAlias(`${word}_${m.group_by}`);
    }
    const unchanged = m._origAs && m._origSig === metricSignature(m, b);
    const as = unique(unchanged ? m._origAs : alias);
    aliasOf[m._id] = as;
    return { as, show_as: m.type === 'agg' ? effectiveShowAs(b, m.show_as) : 'value' };
  });
  return { list, aliasOf };
}

function metricToSpec(m, alias, aliasOf, numericFields) {
  if (m.type === 'agg') {
    const metric = { type: 'agg', as: alias.as, agg: m.agg };
    if (!isCountAgg(m.agg)) metric.field = m.field;
    if (alias.show_as !== 'value') metric.show_as = alias.show_as;
    const filters = conditionsToSpec(m.filters, numericFields);
    if (filters.length) metric.filters = filters;
    return metric;
  }
  if (m.type === 'calc') {
    return {
      type: 'calc', as: alias.as, op: m.op, left: aliasOf[m.left],
      right: m.rightKind === 'number' ? Number(m.right) : aliasOf[m.right],
    };
  }
  // Métricas internas: alias propios, únicos dentro del grupo.
  const used = new Set();
  const innerAlias = {};
  const inner = m.inner.filter(x => x.agg && (isCountAgg(x.agg) || x.field)).map(x => {
    let as = metricAlias(x.agg, x.field, 'value');
    for (let i = 2; used.has(as); i++) as = `${metricAlias(x.agg, x.field, 'value')}_${i}`;
    used.add(as);
    innerAlias[x._id] = as;
    const metric = { type: 'agg', as, agg: x.agg };
    if (!isCountAgg(x.agg)) metric.field = x.field;
    return metric;
  });
  const metric = {
    type: 'grouped', as: alias.as, group_by: m.group_by, inner,
    having: m.having.map(h => groupConditionToSpec(h, innerAlias)).filter(Boolean),
    result: m.result,
  };
  if (!COUNT_RESULTS.includes(m.result)) metric.value = innerAlias[m.value];
  const filters = conditionsToSpec(m.filters, numericFields);
  if (filters.length) metric.filters = filters;
  return metric;
}

// Roles del KPI (view_spec) <-> controles del builder, con ids locales.
function kpiFromView(view, idOf) {
  const target = view ? view.target : null;
  const status = view && view.status;
  return {
    primary: (view && idOf[view.primary]) || '',
    compare: (view && idOf[view.compare]) || '',
    compareMode: (view && view.compare_mode) || 'pct',
    targetKind: typeof target === 'number' ? 'number' : (target ? 'metric' : 'none'),
    targetMetric: typeof target === 'string' ? (idOf[target] || '') : '',
    targetValue: typeof target === 'number' ? String(target) : '',
    higherIsBetter: !view || view.higher_is_better !== false,
    statusBasis: status ? status.basis : 'none',
    good: status ? String(status.good) : '100',
    warn: status ? String(status.warn) : '80',
  };
}

function kpiToPayload(k, aliasOf) {
  const number = (v) => (String(v ?? '').trim() === '' || Number.isNaN(Number(v)) ? null : Number(v));
  const target = k.targetKind === 'number' ? number(k.targetValue)
    : k.targetKind === 'metric' ? (aliasOf[k.targetMetric] || null) : null;
  const status = k.statusBasis !== 'none' && number(k.good) !== null && number(k.warn) !== null
    ? { basis: k.statusBasis, good: number(k.good), warn: number(k.warn) } : null;
  return {
    primary: aliasOf[k.primary] || null,
    compare: aliasOf[k.compare] || null,
    compare_mode: k.compareMode,
    target,
    higher_is_better: !!k.higherIsBetter,
    status,
  };
}

// Estado del builder a partir de data_spec/view_spec: el MISMO spec que escribe la IA, así el
// panel siempre muestra lo que tiene el widget (no hay dos estados separados).
function builderFromSpec(spec, view, widget) {
  if (!spec) return null;
  const labels = (view && view.labels) || {};
  const idOf = {};
  const b = {
    widget,
    // Siempre al menos un select visible por lista ('' = sin elegir).
    dimensions: (spec.dimensions || []).length ? [...spec.dimensions] : [''],
    pivots: (spec.pivots || []).length ? [...spec.pivots] : [''],
    filters: (spec.filters || []).map(conditionFromSpec),
    metrics: [],
    having: [],
    // Las cabeceras de las filas/columnas no son del builder: se copian tal cual para no perderlas
    // (las puso la IA) al aplicar un cambio.
    labels: { ...labels },
    stacked: !!(view && view.stacked),
    sortBy: '',
    sortDir: spec.sort ? spec.sort.dir : 'desc',
    limitN: spec.limit ? String(spec.limit.n) : '',
    limitOthers: !!(spec.limit && spec.limit.others),
    trendBy: spec.trend_by || '',
    kpi: null,
  };
  b.metrics = (spec.metrics || []).map(raw => {
    const m = metricFromSpec(raw, b, idOf);
    m.label = labels[raw.as] || '';
    return m;
  });
  // La firma se calcula con el builder completo (show_as depende de filas y columnas).
  b.metrics.forEach(m => { m._origSig = metricSignature(m, b); });
  b.having = (spec.having || []).map(h => groupConditionFromSpec(h, idOf));
  if (spec.sort) b.sortBy = idOf[spec.sort.by] ? `#${idOf[spec.sort.by]}` : spec.sort.by;
  b.kpi = kpiFromView(widget === 'kpi' ? view : null, idOf);
  return b;
}

// Body de POST/PUT del builder: las claves del data_spec más las de presentación (labels,
// stacked, kpi). `labels` son las cabeceras de columna: viven en view_spec (no en la métrica,
// que el schema valida con additionalProperties: false).
function builderToPayload(b, numericFields = new Set()) {
  const dimensions = builderDims(b);
  const pivots = builderPivots(b);
  const { list: aliases, aliasOf } = metricAliases(b);
  const isKpi = b.widget === 'kpi';
  // Cabeceras de las filas/columnas: se conservan las que ya tenía el widget y siguen en uso.
  const kept = new Set([...dimensions, ...pivots]);
  const labels = {};
  for (const [name, label] of Object.entries(b.labels || {})) {
    if (kept.has(name) && (label || '').trim()) labels[name] = label.trim();
  }
  const metrics = [];
  b.metrics.forEach((m, i) => {
    const alias = aliases[i];
    if (!alias) return;
    metrics.push(metricToSpec(m, alias, aliasOf, numericFields));
    // "Nombre a mostrar" vacío = la cabecera por defecto (la del alias).
    const label = (m.label || '').trim();
    if (label) labels[alias.as] = label;
  });
  const sortBy = b.sortBy && b.sortBy.startsWith('#') ? aliasOf[b.sortBy.slice(1)] : b.sortBy;
  const sortable = new Set([...dimensions, ...metrics.map(m => m.as)]);
  const sort = !isKpi && sortBy && sortable.has(sortBy) ? { by: sortBy, dir: b.sortDir || 'desc' } : null;
  const limitN = parseInt(b.limitN, 10);
  const payload = {
    dimensions,
    pivots,
    filters: conditionsToSpec(b.filters, numericFields),
    metrics,
    having: isKpi ? [] : b.having.map(h => groupConditionToSpec(h, aliasOf)).filter(Boolean),
    sort,
    limit: !isKpi && limitN > 0 ? { n: limitN, others: !!b.limitOthers } : null,
    trend_by: isKpi && b.trendBy ? b.trendBy : null,
    labels,
    stacked: !!(b.stacked && pivots.length),
  };
  if (isKpi) payload.kpi = kpiToPayload(b.kpi, aliasOf);
  return payload;
}

// Texto de una métrica del builder para los pasos de "Consulta con la IA".
function describeMetric(m, b) {
  const aggLabel = (agg) => (AGG_OPTIONS.find(o => o.value === agg) || { label: agg }).label;
  const nameOf = (id) => {
    const i = b.metrics.findIndex(x => x._id === id);
    return i >= 0 ? `métrica ${i + 1}` : '—';
  };
  let text;
  if (m.type === 'agg') {
    text = isCountAgg(m.agg) ? `${aggLabel(m.agg)} de filas` : `${aggLabel(m.agg)} de ${m.field}`;
    if (m.show_as && m.show_as !== 'value') text += ` · Mostrar como ${SHOW_AS_LABELS[m.show_as]}`;
  } else if (m.type === 'calc') {
    const op = (CALC_OP_OPTIONS.find(o => o.value === m.op) || { label: m.op }).label;
    text = `Cálculo: ${nameOf(m.left)} ${op} ${m.rightKind === 'number' ? m.right : nameOf(m.right)}`;
  } else {
    const result = (GROUP_RESULT_OPTIONS.find(o => o.value === m.result) || { label: m.result }).label;
    text = `Por grupo de ${m.group_by}: ${result}`;
  }
  if (m.label) text += ` · Nombre: «${m.label}»`;
  return text;
}

// Incluye `current` aunque no esté en la lista (ej. la IA agrupó por una columna numérica de
// muchos valores), para que el select no pierda el valor que tiene el widget.
function withCurrent(options, current) {
  return current && !options.includes(current) ? [current, ...options] : options;
}

document.addEventListener('alpine:init', () => {
  Alpine.store('dashboard', {
    widgets: [],
    editingId: null,
    editingType: null,
    dashboardId: window.DASHBOARD_ID,
    drawerDraft: {},
    // "Consulta con la IA" (solo tablas): configuración sugerida {builder, filters, title}
    // que el panel muestra como pasos; no se aplica hasta pulsar "Aplicar pasos".
    // Bloque colapsado por defecto: ocupa mucho alto y empuja el constructor hacia abajo.
    drawerAskOpen: false,
    drawerAsking: false,
    drawerAskError: '',
    drawerAdvice: null,
    drawerSaving: false,
    drawerSaveError: '',
    // Pestaña activa del panel: 'data' (filas, columnas, métricas) o 'style' (personalizar).
    drawerTab: 'data',
    // Banner de advertencia del builder: mensaje de rechazo del backend (no se aplica nada).
    drawerSpecError: '',
    drawerApplying: false,
    // Copia de data_spec/view_spec del widget en edición, para el visor JSON.
    drawerSpecs: { data_spec: null, view_spec: null },
    schema: { all_fields: [], numeric_fields: [], dimension_fields: [], sample_values: {} },
    aggOptions: AGG_OPTIONS,
    metricTypeOptions: METRIC_TYPE_OPTIONS,
    calcOpOptions: CALC_OP_OPTIONS,
    groupResultOptions: GROUP_RESULT_OPTIONS,
    compareOpOptions: COMPARE_OP_OPTIONS,
    isCountAgg,
    _nextId: -1,

    schemaError: '',

    async loadSchema() {
      try {
        const r = await fetch(apiUrl(`/api/dashboard/${this.dashboardId}/schema/`));
        const data = await r.json().catch(() => null);
        if (r.ok && data) {
          this.schema = data;
          this.schemaError = '';
        } else {
          this.schemaError = (data && data.error) || 'No se pudieron leer las columnas de la hoja.';
        }
      } catch (e) {
        this.schemaError = 'No se pudo conectar con el servidor.';
      }
    },

    _renderUrl() {
      const url = apiUrl(`/api/dashboard/${this.dashboardId}/render/`);
      const qs = this.getFilterQueryString ? this.getFilterQueryString() : '';
      return qs ? `${url}?${qs}` : url;
    },

    // Carga widgets + datos ya calculados en un solo request. Retorna {id: entry}.
    async loadBoard() {
      const { r, data } = await fetchJsonSafe(this._renderUrl(), {}, RENDER_FETCH_TIMEOUT_MS);
      if (!r.ok || !data) throw new Error((data && data.error) || 'No se pudo cargar el tablero');
      this.widgets = data.widgets.map(w => BaseWidget.fromServer(w));
      this.widgets.sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
      return Object.fromEntries(data.widgets.map(w => [w.id, w]));
    },

    // Recalcula todos los widgets (sin IA): refresco periódico, filtros, reintentos.
    async refreshData() {
      const saved = this.widgets.filter(w => w.id > 0 && w.hasSpec);
      saved.forEach(w => w.setLoading(true));
      try {
        const { r, data } = await fetchJsonSafe(this._renderUrl(), {}, RENDER_FETCH_TIMEOUT_MS);
        if (!r.ok || !data) {
          const message = (data && data.error) || `Error ${r.status} al cargar los datos`;
          saved.forEach(w => { w.setLoading(false); w.renderError(message, { retryable: true }); });
          return;
        }
        const byId = Object.fromEntries(data.widgets.map(w => [w.id, w]));
        saved.forEach(w => w.applyRender(byId[w.id]));
      } catch (e) {
        saved.forEach(w => { w.setLoading(false); w.renderError('No se pudo conectar con el servidor', { retryable: true }); });
      }
    },

    addWidget(type) {
      const widget = WidgetRegistry.create(type, {
        id: this._nextId--,
        order: this.widgets.length,
        _dirty: true,
      });
      this.widgets.push(widget);
      return widget;
    },

    // Guarda solo la presentación (título, posición, preferencias visuales). Los widgets
    // nuevos no existen en el backend hasta que se generan con IA.
    async _saveWidget(w) {
      if (w.id < 0) return;
      await fetch(apiUrl(`/api/widget/${w.id}/`), {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(w.toPayload()),
      });
      w._dirty = false;
    },

    async removeWidget(id) {
      if (id > 0) {
        try { await fetch(apiUrl(`/api/widget/${id}/`), { method: 'DELETE' }); } catch (e) {}
      }
      this.widgets = this.widgets.filter(w => w.id !== id);
    },

    reorderWidgets() {
      const canvasEl = document.getElementById('dashboard-canvas');
      if (!canvasEl) return;
      const widgetEls = canvasEl.querySelectorAll('[data-widget-id]');
      widgetEls.forEach((el, i) => {
        const id = parseInt(el.dataset.widgetId);
        const w = this.widgets.find(w => w.id === id);
        if (w && w.order !== i) {
          w.order = i;
          w._dirty = true;
        }
      });
      this.widgets.sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
    },

    get editingWidget() {
      return this.widgets.find(w => w.id === this.editingId) || null;
    },

    get drawerFields() {
      // No debe colapsar a []: eso destruiría/recrearía los <select> del drawer
      // (y sus <option>) en cada apertura.
      const WidgetClass = this.editingType ? WidgetRegistry.get(this.editingType) : BaseWidget;
      return WidgetClass.drawerFields;
    },

    get drawerWidgetClass() {
      return this.editingType ? WidgetRegistry.get(this.editingType) : BaseWidget;
    },

    // Pasos de la sugerencia de la IA, en el orden del constructor.
    get drawerSteps() {
      const a = this.drawerAdvice;
      if (!a) return [];
      const b = a.builder;
      const dims = builderDims(b);
      const pivots = builderPivots(b);
      const steps = [
        { title: 'Filas', detail: dims.length ? `Agrega, en este orden: ${dims.join(' › ')}` : 'Sin filas' },
        { title: 'Columnas', detail: pivots.length ? `Agrega, en este orden: ${pivots.join(' › ')}` : 'Deja «Sin agrupar»' },
      ];
      if (a.filters.length) {
        steps.push({ title: 'Condiciones', details: a.filters.map(describeCondition) });
      }
      steps.push({ title: 'Valores', details: b.metrics.map(m => describeMetric(m, b)) });
      if (a.having.length) {
        steps.push({ title: 'Mostrar solo grupos donde', details: a.having.map(h => `${h.left} ${OP_SHORT[h.op]} ${h.right}`) });
      }
      if (b.sortBy) {
        const target = b.sortBy.startsWith('#') ? `la métrica ${b.metrics.findIndex(m => `#${m._id}` === b.sortBy) + 1}` : b.sortBy;
        steps.push({ title: 'Orden', detail: `Ordenar por ${target}, ${b.sortDir === 'asc' ? 'de menor a mayor' : 'de mayor a menor'}` });
      }
      if (b.limitN) {
        steps.push({ title: 'Top', detail: `Mostrar solo los primeros ${b.limitN}${b.limitOthers ? ' y agrupar el resto en «Otros»' : ''}` });
      }
      steps.push({ title: 'Listo', detail: 'Pulsa «Aplicar al widget» para ver la tabla.' });
      return steps;
    },

    get builder() {
      return this.drawerDraft.builder || null;
    },

    // Columnas agrupables para la fila `i`, sin las ya usadas en otras filas o en columnas
    // (el backend también lo valida).
    dimensionOptionsAt(i) {
      const b = this.builder;
      if (!b) return [];
      const current = b.dimensions[i];
      const used = new Set([...b.dimensions, ...b.pivots]);
      return withCurrent(this.schema.dimension_fields || [], current).filter(f => f === current || !used.has(f));
    },

    pivotOptionsAt(i) {
      const b = this.builder;
      if (!b) return [];
      const current = b.pivots[i];
      const used = new Set([...b.dimensions, ...b.pivots]);
      return withCurrent(this.schema.dimension_fields || [], current).filter(f => f === current || !used.has(f));
    },

    get dimensionLabel() {
      return this.drawerWidgetClass.dimensionLabel;
    },

    get maxDimensions() {
      return this.drawerWidgetClass.maxDimensions;
    },

    get maxPivots() {
      return this.drawerWidgetClass.maxPivots;
    },

    // Se agrega un nivel solo cuando el anterior ya tiene columna elegida.
    get canAddDimension() {
      const b = this.builder;
      return !!b && b.dimensions.length < this.maxDimensions && b.dimensions.every(Boolean);
    },

    get canAddPivot() {
      const b = this.builder;
      return !!b && b.pivots.length < this.maxPivots && b.pivots.every(Boolean);
    },

    // Los totales de cada nivel (drawerDraft.rowTotals/columnTotals) siguen a su fila o
    // columna al agregar o quitar niveles, como en Sheets.
    addDimension() {
      if (!this.canAddDimension) return;
      this.builder.dimensions.push('');
      this.drawerDraft.rowTotals?.push(true);
    },

    removeDimension(i) {
      const b = this.builder;
      if (!b || b.dimensions.length <= 1) return;
      b.dimensions.splice(i, 1);
      this.drawerDraft.rowTotals?.splice(i, 1);
    },

    addPivot() {
      if (!this.canAddPivot) return;
      this.builder.pivots.push('');
      this.drawerDraft.columnTotals?.push(true);
    },

    removePivot(i) {
      const b = this.builder;
      if (!b) return;
      if (b.pivots.length > 1) {
        b.pivots.splice(i, 1);
        this.drawerDraft.columnTotals?.splice(i, 1);
      } else {
        b.pivots[0] = '';
        if (this.drawerDraft.columnTotals) this.drawerDraft.columnTotals[0] = true;
      }
    },

    get pivotLabel() {
      return this.drawerWidgetClass.pivotLabel;
    },

    get maxMetrics() {
      return this.drawerWidgetClass.maxMetrics;
    },

    // El campo "Nombre a mostrar" solo donde la métrica se ve con nombre: hoy, la tabla.
    get drawerSupportsLabels() {
      return !!this.drawerWidgetClass.supportsLabels;
    },

    get drawerIsKpi() {
      return this.editingType === 'kpi';
    },

    // Cabecera por defecto de la métrica `i`: lo que se verá si no se escribe un nombre.
    metricLabelPlaceholder(i) {
      const b = this.builder;
      if (!b) return '';
      const alias = metricAliases(b).list[i];
      return alias ? defaultColumnName(alias.as) : '';
    },

    get _numericSet() {
      return new Set(this.schema.numeric_fields || []);
    },

    _payload(b) {
      return builderToPayload(b, this._numericSet);
    },

    // Tipos de métrica disponibles: "por grupo" solo en el KPI.
    get metricTypes() {
      return this.drawerIsKpi ? METRIC_TYPE_OPTIONS : METRIC_TYPE_OPTIONS.filter(o => o.value !== 'grouped');
    },

    // Nombre corto de la métrica `id` en los selects (su nombre a mostrar o el de por defecto).
    metricName(id) {
      const b = this.builder;
      if (!b) return '';
      const i = b.metrics.findIndex(m => m._id === id);
      if (i < 0) return '';
      return (b.metrics[i].label || '').trim() || this.metricLabelPlaceholder(i) || `Métrica ${i + 1} (incompleta)`;
    },

    // Métricas a las que puede referirse la métrica `i` en un cálculo: las anteriores que dan
    // un número. Sin `i`, todas (condiciones sobre grupos y roles del KPI).
    metricRefOptions(i = null, { numericOnly = true } = {}) {
      const b = this.builder;
      if (!b) return [];
      const list = i === null ? activeMetrics(b) : activeMetrics(b).filter(m => b.metrics.indexOf(m) < i);
      return list
        .filter(m => !numericOnly || !isRankingMetric(m))
        .map(m => ({ value: m._id, label: this.metricName(m._id) }));
    },

    // Métricas internas de una métrica por grupo, para su condición y su resultado.
    innerRefOptions(m) {
      const aggLabel = (agg) => (AGG_OPTIONS.find(o => o.value === agg) || { label: agg }).label;
      return m.inner
        .filter(x => x.agg && (isCountAgg(x.agg) || x.field))
        .map(x => ({ value: x._id, label: isCountAgg(x.agg) ? 'Conteo de filas' : `${aggLabel(x.agg)} de ${x.field}` }));
    },

    groupResultNeedsValue(m) {
      return !COUNT_RESULTS.includes(m.result);
    },

    addInnerMetric(m) {
      if (m.inner.length >= 3) return;
      m.inner.push(newInnerMetric(this.schema.numeric_fields || []));
    },

    removeInnerMetric(m, i) {
      if (m.inner.length <= 1) return;
      const [removed] = m.inner.splice(i, 1);
      if (m.value === removed._id) m.value = '';
    },

    // --- Condiciones ---
    addCondition(list) {
      list.push(newCondition((this.schema.all_fields || [])[0] || ''));
    },

    removeCondition(list, i) {
      list.splice(i, 1);
    },

    // Operadores según la columna: las comparaciones de orden solo en columnas numéricas.
    opOptionsFor(field) {
      const numeric = this._numericSet.has(field);
      return FILTER_OP_OPTIONS.filter(o => numeric || !o.numeric);
    },

    onConditionFieldChange(c) {
      if (!this.opOptionsFor(c.field).some(o => o.value === c.op)) c.op = 'eq';
    },

    conditionUsesMode(c) {
      return RELATIVE_OPS.includes(c.op);
    },

    conditionNeedsValue(c) {
      return !EMPTY_OPS.includes(c.op) && !(this.conditionUsesMode(c) && c.mode !== 'value');
    },

    conditionIsList(c) {
      return LIST_OPS.includes(c.op);
    },

    valueModeOptions: VALUE_MODE_OPTIONS,

    // id del <datalist> con los valores de ejemplo de la columna (autocompletar).
    sampleListId(field) {
      const fields = Object.keys(this.schema.sample_values || {});
      const i = fields.indexOf(field);
      return i >= 0 ? `samples-${i}` : null;
    },

    get sampleLists() {
      return Object.values(this.schema.sample_values || {}).map((values, i) => ({ id: `samples-${i}`, values }));
    },

    addGroupCondition(list, options) {
      list.push(newGroupCondition(options[0] ? options[0].value : ''));
    },

    // --- Top N ---
    get limitNeedsMetricSort() {
      const b = this.builder;
      return !!b && parseInt(b.limitN, 10) > 0 && !(b.sortBy || '').startsWith('#');
    },

    get showStacked() {
      return this.drawerWidgetClass.supportsStacked && !!(this.builder && builderPivots(this.builder).length);
    },

    // Campos según la función: contar únicos acepta cualquier columna; el resto de las
    // funciones que resumen una columna, solo numéricas (count no usa campo).
    fieldOptionsFor(agg, current) {
      const fields = agg === 'count_distinct' ? this.schema.all_fields : this.schema.numeric_fields;
      return withCurrent(fields || [], current);
    },

    get showAsOptions() {
      return this.builder ? showAsOptions(this.builder) : [];
    },

    showAsValue(m) {
      return this.builder ? effectiveShowAs(this.builder, m.show_as) : 'value';
    },

    get sortOptions() {
      const b = this.builder;
      if (!b) return [];
      const opts = builderDims(b).map(d => ({ value: d, label: d }));
      const { list } = metricAliases(b);
      b.metrics.forEach((m, i) => {
        if (list[i]) opts.push({ value: `#${m._id}`, label: this.metricName(m._id) });
      });
      return opts;
    },

    onDimensionChange() {
      // Una columna elegida como fila deja de ser columna de pivote.
      const b = this.builder;
      if (!b) return;
      b.pivots = b.pivots.map(p => (b.dimensions.includes(p) ? '' : p));
    },

    openDrawer(id) {
      const w = this.widgets.find(w => w.id === id);
      if (!w) return;
      this.editingId = id;
      this.editingType = w.chart_type;
      // Un widget nuevo empieza por sus datos.
      if (w.id < 0) this.drawerTab = 'data';
      const draft = {};
      for (const field of this.drawerFields) {
        if (field.key === 'builder' || field.key === 'prompt') continue;
        draft[field.key] = w[field.key];
      }
      draft.prompt = '';
      draft.builder = this._builderDraft(w) || this._defaultBuilder();
      // Totales por nivel: van junto a cada fila/columna del builder, pero son presentación
      // (se guardan con "Guardar" y no vuelven a pedir los datos).
      if (this.drawerWidgetClass.supportsTotals) {
        draft.rowTotals = totalsLevels(w.rowTotals, draft.builder.dimensions.length);
        draft.columnTotals = totalsLevels(w.columnTotals, draft.builder.pivots.length);
        // "Repetir etiquetas de fila" va, como en Sheets, bajo la primera fila.
        draft.repeatRowLabels = !!w.repeatRowLabels;
      }
      this.drawerDraft = draft;
      // Widget nuevo abierto antes de que llegaran las columnas: completar los valores por
      // defecto cuando lleguen.
      if (w.id < 0 && !(this.schema.all_fields || []).length) {
        this.loadSchema().then(() => {
          if (this.editingId === id) this.drawerDraft.builder = this._defaultBuilder();
        });
      }
      this.drawerAskError = '';
      this.drawerAdvice = null;
      this.drawerAskOpen = false;
      this.drawerSaveError = '';
      this.drawerSpecError = '';
      this._syncDrawerSpecs(w);
    },

    _builderDraft(w) {
      return builderFromSpec(w.data_spec, w.view_spec, w.chart_type);
    },

    // Punto de partida del builder para un widget nuevo: primera columna agrupable y una
    // métrica (suma de la primera columna numérica, o conteo si la hoja no tiene números).
    _defaultBuilder() {
      const dims = this.schema.dimension_fields || [];
      const b = builderFromSpec({ dimensions: [], pivots: [], metrics: [] }, null, this.editingType);
      b.dimensions = [this.drawerWidgetClass.supportsDimension ? (dims[0] || '') : ''];
      b.metrics = [newMetric('agg', this.schema.numeric_fields || [])];
      return b;
    },

    _syncDrawerSpecs(w) {
      this.drawerSpecs = {
        data_spec: w && w.data_spec ? JSON.parse(JSON.stringify(w.data_spec)) : null,
        view_spec: w && w.view_spec ? JSON.parse(JSON.stringify(w.view_spec)) : null,
      };
    },

    closeDrawer() {
      this.editingId = null;
      this.editingType = null;
      this.drawerDraft = {};
      this.drawerAskError = '';
      this.drawerAdvice = null;
      this.drawerAskOpen = false;
      this.drawerSaveError = '';
      this.drawerSpecError = '';
      this.drawerTab = 'data';
    },

    addBuilderMetric() {
      const b = this.builder;
      if (!b || b.metrics.length >= this.maxMetrics) return;
      b.metrics.push(newMetric('agg', this.schema.numeric_fields || []));
    },

    removeBuilderMetric(index) {
      const b = this.builder;
      if (!b || b.metrics.length <= 1) return;
      b.metrics.splice(index, 1);
    },

    // Al pasar una métrica a "por grupo", arranca agrupando por la primera columna agrupable.
    onMetricTypeChange(m) {
      if (m.type === 'grouped' && !m.group_by) m.group_by = (this.schema.dimension_fields || [])[0] || '';
    },

    // "Aplicar al widget seleccionado": escribe data_spec vía update_widget_spec. NUNCA llama a
    // la IA. Si el backend rechaza la combinación, muestra su mensaje en el banner y no toca el
    // widget. Retorna true si se aplicó.
    async applyBuilder() {
      const w = this.editingWidget;
      const b = this.builder;
      if (!w || !b) return false;
      const payload = this._payload(b);
      if (!payload.metrics.length) {
        this.drawerSpecError = 'Agrega al menos una métrica con su columna.';
        return false;
      }
      this.drawerApplying = true;
      this.drawerSpecError = '';
      try {
        const isNew = w.id < 0;
        // Widget nuevo: se crea desde el builder. Existente: se edita su spec. Ninguno usa IA.
        const { r, data } = await fetchJsonSafe(
          isNew
            ? apiUrl(`/api/dashboard/${this.dashboardId}/widgets/`)
            : apiUrl(`/api/widget/${w.id}/spec/`),
          {
            method: isNew ? 'POST' : 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              ...payload,
              title: this.drawerDraft.title,
              ...(isNew ? { type: this.editingType, position: w.getPosition(), display: w.getProperties() } : {}),
            }),
          },
          RENDER_FETCH_TIMEOUT_MS,
        );
        if (!r.ok || !data) {
          this.drawerSpecError = (data && data.error) || 'No se pudo aplicar el cambio.';
          return false;
        }
        if (isNew) this._swapWidgetId(w, data.id);
        w.applyServerState(data);
        w.updateChrome();
        w.applyRender(data);
        this.drawerDraft.builder = this._builderDraft(w);
        this._syncDrawerSpecs(w);
        return true;
      } catch (e) {
        this.drawerSpecError = 'No se pudo conectar con el servidor.';
        return false;
      } finally {
        this.drawerApplying = false;
      }
    },

    get builderDirty() {
      const w = this.editingWidget;
      if (!w || !this.builder) return false;
      if (w.id < 0) return true;
      return JSON.stringify(this._payload(this.builder))
        !== JSON.stringify(this._payload(this._builderDraft(w)));
    },

    _swapWidgetId(w, newId) {
      const oldId = w.id;
      w.id = newId;
      if (w.el) {
        w.el.dataset.widgetId = newId;
        const chartContainer = w.el.querySelector(`#chart-${oldId}`);
        if (chartContainer) chartContainer.id = `chart-${newId}`;
      }
      if (this.editingId === oldId) this.editingId = newId;
    },

    // "Consulta con la IA": pide la configuración sugerida para el pedido del usuario. No
    // modifica el widget; el panel la muestra como pasos.
    async askAssistant() {
      const prompt = (this.drawerDraft.prompt || '').trim();
      if (!prompt) return;
      this.drawerAsking = true;
      this.drawerAskError = '';
      this.drawerAdvice = null;
      try {
        const { r, data } = await fetchJsonSafe(apiUrl(`/api/dashboard/${this.dashboardId}/table-assistant/`), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ prompt }),
        });
        if (!r.ok || !data) {
          throw new Error((data && data.error) || 'El servidor no respondió correctamente (puede que la IA haya tardado demasiado). Intenta de nuevo.');
        }
        this.drawerAdvice = {
          builder: builderFromSpec(data.data_spec, data.view_spec, 'table'),
          filters: data.data_spec.filters || [],
          having: data.data_spec.having || [],
          title: (data.view_spec && data.view_spec.title) || '',
        };
      } catch (e) {
        this.drawerAskError = e.name === 'AbortError'
          ? 'La IA tardó demasiado en responder. Intenta de nuevo.'
          : e.message;
      } finally {
        this.drawerAsking = false;
      }
    },

    // Rellena el constructor con la sugerencia. No guarda: el usuario revisa y pulsa
    // "Aplicar al widget".
    applyAdvice() {
      const a = this.drawerAdvice;
      if (!a) return;
      const builder = JSON.parse(JSON.stringify(a.builder));
      builder.widget = this.editingType;
      this.drawerDraft.builder = builder;
      if (a.title) this.drawerDraft.title = a.title;
      if (this.drawerDraft.rowTotals) {
        this.drawerDraft.rowTotals = totalsLevels(this.drawerDraft.rowTotals, builder.dimensions.length);
        this.drawerDraft.columnTotals = totalsLevels(this.drawerDraft.columnTotals, builder.pivots.length);
      }
    },

    async saveDrawer() {
      const w = this.editingWidget;
      if (!w) return;
      this.drawerSaving = true;
      this.drawerSaveError = '';
      try {
        const { prompt, builder, ...presentation } = this.drawerDraft;
        // Cambios del builder sin aplicar: se aplican con el mismo camino que el botón.
        if (builder && this.builderDirty && !(await this.applyBuilder())) return;

        Object.assign(w, presentation);
        await this._saveWidget(w);
        w.updateChrome();
        // Opciones de presentación que cambian el contenido (ej. totales de la tabla): se
        // redibuja con los datos ya calculados, sin volver a pedirlos.
        if (w._lastEntry) w.applyRender(w._lastEntry);
        this.closeDrawer();
      } catch (e) {
        this.drawerSaveError = e.message;
      } finally {
        this.drawerSaving = false;
      }
    },
  });
});
