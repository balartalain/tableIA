// Formato de los valores de datos en todo el front: tablas, KPI, gráficos y la vista previa de
// los campos calculados. El motor los manda sin redondear; aquí se decide cómo se ven, en un
// solo lugar, para que la misma cifra se vea igual en todas partes.

// Locale de los números: el del navegador.
const NUMBER_LOCALE = undefined;

// `value`: número (o texto numérico). Vacío o no numérico → `empty`; un texto no numérico se
// devuelve tal cual (celdas de texto). Opciones:
// - `percent`: agrega «%» (el valor ya viene multiplicado por 100);
// - `currency`: antepone «$», con 2 decimales fijos (salvo `decimals`);
// - `decimals`: null = hasta 2; un número = exactamente esos;
// - `compact`: notación compacta (1,2 mil); `signed`: signo también en positivos;
// - `prefix` / `suffix`: texto alrededor (el sufijo, separado por un espacio).
function formatNumber(value, {
  percent = false, currency = false, decimals = null, compact = false, signed = false,
  prefix = '', suffix = '', empty = '-',
} = {}) {
  if (value == null || value === '') return empty;
  const number = typeof value === 'number' ? value : Number(value);
  if (Number.isNaN(number)) return typeof value === 'string' ? value : empty;

  const fixed = decimals ?? (currency ? 2 : null);
  const options = fixed == null
    ? { maximumFractionDigits: 2 }
    : { minimumFractionDigits: fixed, maximumFractionDigits: fixed };
  if (compact) Object.assign(options, { notation: 'compact', maximumFractionDigits: fixed ?? 1 });
  if (signed) options.signDisplay = 'exceptZero';

  if (currency) {
    const text = Math.abs(number).toLocaleString(NUMBER_LOCALE, { ...options, signDisplay: 'never' });
    const sign = number < 0 ? '-' : (signed && number > 0 ? '+' : '');
    return `${sign}$${text}`;
  }
  const text = number.toLocaleString(NUMBER_LOCALE, options);
  if (percent) return `${text}%`;
  return `${prefix}${text}${suffix ? ` ${suffix}` : ''}`;
}
