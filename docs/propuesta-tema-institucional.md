# Prompt: aplicar el tema institucional UAPA a Tableros IA (app + tableros)

## Contexto

Trabajas sobre **Tableros IA**, una aplicación Django con plantillas HTML, Tailwind CSS 3, Alpine.js, Tabulator (tablas) y ApexCharts (gráficos). El usuario arma tableros arrastrando widgets (barras, líneas, dona, dispersión, KPI, tabla, tabla dinámica, ranking, matriz de correlación, V de Cramér) a un lienzo con GridStack.

Hay que reemplazar los colores actuales (naranja genérico de Tailwind, morado en "Generar con IA", grises varios, y las paletas Verde / Azul / Índigo de los tableros) por el tema institucional de UAPA. El trabajo tiene **dos partes con hojas de estilo separadas**:

1. **La app**: header, sidebar de módulos, listado de tableros, paneles, modales, formularios, tablas, tabs, botones y badges. Usa `tokens.css` + Tailwind.
2. **Los tableros**: todo lo que se dibuja dentro del lienzo (tarjetas de widget, KPI, tablas, tablas dinámicas, gráficos). Usa una hoja aparte, `tablero-theme.css`, con variables propias `--tb-*`. Hoy toman los colores institucionales, pero están separadas para que en el futuro cada tablero pueda tener sus propios colores sin afectar la app.

---

## Colores institucionales

| Rol | Hex | Uso |
|---|---|---|
| Navy | `#041147` | Barras, fondos de énfasis, títulos, texto sobre botones naranjas |
| Naranja | `#ff8301` | Fondo de botones principales y acentos |
| Naranja texto | `#b85c00` | Naranja usado como TEXTO sobre fondo claro (links, tab activo, ítem activo) |

### Reglas de contraste (obligatorias)
1. **Nunca pongas texto blanco sobre `#ff8301`** (contraste 2,5:1, no cumple WCAG AA). El texto sobre naranja siempre es navy (`--color-on-accent`, 7,2:1).
2. **Nunca uses `#ff8301` como color de texto sobre blanco** (2,5:1). Para texto naranja usa siempre `--color-accent-text` (`#b85c00`, 4,6:1).
3. Texto blanco sobre navy está permitido (18:1).

---

## Arquitectura de estilos

```
static/css/tokens.css          ← variables de la APP; único lugar con hex de la app
static/css/tablero-theme.css   ← variables --tb-* y estilos de lo que va DENTRO del lienzo
static/src/input.css           ← entrada de Tailwind: importa tokens, base, componentes de la app, Tabulator de la app
static/js/chart-theme.js       ← ApexCharts lee la paleta --tb-chart-* del lienzo
tailwind.config.js             ← solo nombres que apuntan a var(--…); ningún valor
```

Orden de carga en `base.html` y en las plantillas de Vista previa y de tablero compartido:
1. CSS original de Tabulator
2. CSS compilado de Tailwind (incluye `tokens.css`)
3. `tablero-theme.css`
4. `apexcharts.min.js` → `chart-theme.js` → el código que crea los gráficos

### Reglas del sistema
1. **Ningún hex fuera de `tokens.css` y `tablero-theme.css`.** Ni en plantillas, ni en `input.css`, ni en JS, ni en `style=""` (salvo las variables personalizadas del tablero, ver más abajo), ni en clases arbitrarias como `bg-[#ff8301]`.
2. **Tres capas de tokens en la app:** marca (`--brand-*`) → semánticos (`--color-*`, `--fs-*`, `--radius-*`) → componentes (`--btn-*`, `--table-*`, `--sidebar-*`). Las plantillas y componentes nunca usan `--brand-*` directamente.
3. **Dentro del lienzo solo se usan variables `--tb-*` y clases `.tb-*`.** Nada de utilidades de color de Tailwind (`bg-surface`, `text-fg-muted`, `bg-gray-*`…) dentro de los widgets: esas apuntan a la app y no cambiarían si el tablero tiene otro tema. Las utilidades de layout (`flex`, `gap-2`, `p-4`, `truncate`) sí se pueden usar.
4. **Ningún color dentro del JS de los gráficos.** Todo color de ApexCharts sale de `chart-theme.js`, que lee `--tb-chart-*`.
5. **Si falta un color o un tamaño, se agrega como token**, nunca como valor suelto. Un color nuevo entra de abajo hacia arriba: marca → semántico → componente (o `--tb-*` si es del tablero).

### Convención de nombres
`--{componente o categoría}-{parte}-{variante}-{estado}`. Ejemplos: `--btn-primary-bg-hover`, `--table-header-text`, `--tb-kpi-value`, `--tb-chart-3`.

---

## Archivo 1: `static/css/tokens.css` (créalo tal cual)

```css
/* tokens.css — Tableros IA · Tema institucional UAPA (APP)
   Capa 1 MARCA → Capa 2 SEMÁNTICOS → Capa 3 COMPONENTES */
:root {
  /* ═══ 1. MARCA ═══ */
  --brand-navy-900: #020a2e;
  --brand-navy-800: #041147;
  --brand-navy-700: #0b1f6b;
  --brand-navy-600: #1d3594;
  --brand-navy-400: #6b84d6;
  --brand-navy-200: #c5cbe3;
  --brand-navy-100: #e6e9f4;
  --brand-navy-50:  #f3f5fa;

  --brand-orange-600: #e67500;
  --brand-orange-500: #ff8301;
  --brand-orange-700: #b85c00;
  --brand-orange-300: #ffb366;
  --brand-orange-100: #ffe9d1;
  --brand-orange-50:  #fff5ea;

  --neutral-0:   #ffffff;
  --neutral-50:  #f6f7fa;
  --neutral-100: #eef0f5;
  --neutral-200: #dde1ea;
  --neutral-400: #9aa1b5;
  --neutral-500: #8a93b2;
  --neutral-600: #555d73;
  --neutral-800: #2a3045;
  --neutral-900: #141a2e;

  --status-success: #1e7a4c;  --status-success-bg: #e6f4ec;
  --status-danger:  #c02626;  --status-danger-bg:  #fdecec;
  --status-warning: #9a6200;  --status-warning-bg: #fff4dc;

  /* ═══ 2. SEMÁNTICOS ═══ */
  --color-primary:       var(--brand-navy-800);
  --color-primary-hover: var(--brand-navy-700);
  --color-on-primary:    var(--neutral-0);

  --color-accent:        var(--brand-orange-500);
  --color-accent-hover:  var(--brand-orange-600);
  --color-on-accent:     var(--brand-navy-800);
  --color-accent-text:   var(--brand-orange-700);
  --color-accent-soft:   var(--brand-orange-50);

  --color-bg-app:        var(--neutral-50);
  --color-surface:       var(--neutral-0);
  --color-surface-muted: var(--brand-navy-50);

  --color-border:        var(--neutral-200);
  --color-border-strong: var(--brand-navy-200);

  --color-text:          var(--neutral-900);
  --color-text-muted:    var(--neutral-600);
  --color-text-subtle:   var(--neutral-400);
  --color-heading:       var(--brand-navy-800);

  --color-focus-ring:    rgba(255, 131, 1, .45);

  --font-sans: "Ubuntu", system-ui, -apple-system, "Segoe UI", sans-serif;
  --font-mono: ui-monospace, Menlo, Consolas, monospace;

  --fs-xs:   12px;   /* ayudas, etiquetas, encabezados de tabla */
  --fs-sm:   13px;   /* celdas, inputs, botones */
  --fs-base: 14px;   /* texto general */
  --fs-md:   16px;   /* títulos de tarjeta y sección */
  --fs-lg:   20px;   /* títulos de panel/modal */
  --fs-xl:   28px;   /* título de página */
  --fs-kpi:  34px;   /* número de la tarjeta KPI */

  --fw-regular: 400; --fw-medium: 500; --fw-semibold: 600; --fw-bold: 700;
  --lh-tight: 1.25;  --lh-base: 1.5;
  --tracking-label: .06em;

  --radius-sm:   6px;
  --radius-md:   8px;
  --radius-lg:   12px;
  --radius-pill: 999px;
  --shadow-sm: 0 1px 2px rgba(4, 17, 71, .08);
  --shadow-md: 0 4px 16px rgba(4, 17, 71, .10);

  /* ═══ 3. COMPONENTES ═══ */
  --header-bg:     var(--color-surface);
  --header-text:   var(--color-text);
  --header-border: var(--color-border);

  --sidebar-bg:          var(--color-surface);
  --sidebar-border:      var(--color-border);
  --sidebar-active-bg:   var(--color-accent-soft);
  --sidebar-active-text: var(--color-accent-text);
  --module-icon-bg:      var(--color-surface-muted);
  --module-icon-text:    var(--color-primary);

  --btn-primary-bg:       var(--color-accent);
  --btn-primary-bg-hover: var(--color-accent-hover);
  --btn-primary-text:     var(--color-on-accent);
  --btn-secondary-bg:     var(--color-surface);
  --btn-secondary-text:   var(--color-primary);
  --btn-secondary-border: var(--color-border-strong);
  --btn-dark-bg:          var(--color-primary);
  --btn-dark-text:        var(--color-on-primary);

  --table-header-bg:   var(--neutral-100);
  --table-header-text: var(--brand-navy-800);
  --table-row-hover:   var(--brand-navy-50);
  --table-border:      var(--color-border);
  --table-total-bg:    var(--brand-navy-50);

  --tab-text:          var(--color-text-muted);
  --tab-active-text:   var(--color-primary);
  --tab-active-border: var(--color-accent);

  --input-bg:           var(--color-surface);
  --input-border:       var(--color-border);
  --input-border-focus: var(--color-accent);
}
```

---

## Archivo 2: `static/css/tablero-theme.css` (créalo tal cual)

Las variables `--tb-*` toman por defecto los valores de la app, así hoy el tablero se ve igual que la app. Pero son independientes: un tablero puede sobrescribirlas sin afectar la interfaz.

```css
/* =====================================================================
   tablero-theme.css — Tema de los TABLEROS (todo lo que va dentro del lienzo)
   Se aplica al contenedor del lienzo:  <div id="lienzo" class="tablero" data-tema-tablero="institucional">
   Prioridad (de menor a mayor):
     1. Valores por defecto (.tablero)       → institucional, heredan de tokens.css
     2. Preset  ([data-tema-tablero="…"])   → paletas predefinidas (hoy solo institucional)
     3. Inline  (style="--tb-chart-1: …")   → colores personalizados del usuario (futuro)
   ===================================================================== */

.tablero {
  /* ── Lienzo y tarjetas de widget ── */
  --tb-bg:             var(--color-bg-app);
  --tb-card-bg:        var(--color-surface);
  --tb-card-border:    var(--color-border);
  --tb-card-radius:    var(--radius-lg);
  --tb-card-shadow:    var(--shadow-sm);
  --tb-title-text:     var(--color-text-muted);   /* "GRÁFICO DE BARRAS", etiqueta superior */
  --tb-text:           var(--color-text);
  --tb-text-muted:     var(--color-text-muted);
  --tb-font:           var(--font-sans);

  /* ── KPI ── */
  --tb-kpi-value:      var(--color-heading);
  --tb-kpi-up:         var(--status-success);
  --tb-kpi-up-bg:      var(--status-success-bg);
  --tb-kpi-down:       var(--status-danger);
  --tb-kpi-down-bg:    var(--status-danger-bg);
  --tb-kpi-track:      var(--neutral-100);
  --tb-kpi-fill:       var(--brand-orange-500);

  /* ── Tablas, tablas dinámicas y ranking (mismo estilo que la app) ── */
  --tb-table-header-bg:   var(--table-header-bg);
  --tb-table-header-text: var(--table-header-text);
  --tb-table-row-hover:   var(--table-row-hover);
  --tb-table-border:      var(--table-border);
  --tb-table-total-bg:    var(--table-total-bg);
  --tb-table-total-text:  var(--color-heading);
  --tb-bar-fill:          var(--tb-chart-1);      /* barras dentro de celdas y del ranking */
  --tb-bar-track:         var(--neutral-100);

  /* ── Gráficos: paleta categórica (series y categorías) ── */
  --tb-chart-1: var(--brand-navy-600);
  --tb-chart-2: var(--brand-orange-500);
  --tb-chart-3: var(--brand-navy-400);
  --tb-chart-4: var(--brand-orange-300);
  --tb-chart-5: var(--neutral-500);
  --tb-chart-6: var(--brand-navy-800);

  /* ── Gráficos: secuencial, de menos a más (mapas de calor, V de Cramér 0→1) ── */
  --tb-seq-1: var(--brand-navy-50);
  --tb-seq-2: var(--brand-navy-200);
  --tb-seq-3: var(--brand-navy-400);
  --tb-seq-4: var(--brand-navy-600);
  --tb-seq-5: var(--brand-navy-800);

  /* ── Gráficos: divergente, negativo ↔ positivo (matriz de correlación −1→+1) ── */
  --tb-div-neg:     var(--brand-orange-600);
  --tb-div-neg-mid: var(--brand-orange-100);
  --tb-div-zero:    var(--neutral-100);
  --tb-div-pos-mid: var(--brand-navy-200);
  --tb-div-pos:     var(--brand-navy-600);

  /* ── Gráficos: ejes y cuadrícula ── */
  --tb-chart-axis:  var(--color-text-muted);
  --tb-chart-grid:  var(--color-border);
  --tb-chart-label: var(--color-text);

  background: var(--tb-bg);
  color: var(--tb-text);
  font-family: var(--tb-font);
}

/* ═══ PRESETS ═══
   "institucional" usa los valores por defecto. Para agregar otro preset en el futuro,
   sobrescribe aquí solo las variables que cambian. */
.tablero[data-tema-tablero="institucional"] { }


/* ═══ COMPONENTES DEL TABLERO ═══ */

/* Tarjeta de widget */
.tablero .tb-widget {
  background: var(--tb-card-bg);
  border: 1px solid var(--tb-card-border);
  border-radius: var(--tb-card-radius);
  box-shadow: var(--tb-card-shadow);
  padding: 16px;
}
.tablero .tb-widget-title {
  font-size: var(--fs-xs);
  font-weight: var(--fw-semibold);
  letter-spacing: var(--tracking-label);
  text-transform: uppercase;
  color: var(--tb-title-text);
}

/* KPI */
.tablero .tb-kpi-value { font-size: var(--fs-kpi); font-weight: var(--fw-bold); color: var(--tb-kpi-value); line-height: 1.1; }
.tablero .tb-kpi-label { font-size: var(--fs-xs); font-weight: var(--fw-semibold); letter-spacing: var(--tracking-label); text-transform: uppercase; color: var(--tb-text-muted); }
.tablero .tb-kpi-sub   { font-size: var(--fs-xs); color: var(--tb-text-muted); }
.tablero .tb-delta     { display: inline-flex; align-items: center; gap: 4px; padding: 2px 10px; border-radius: var(--radius-pill); font-size: var(--fs-xs); font-weight: var(--fw-semibold); }
.tablero .tb-delta-up   { background: var(--tb-kpi-up-bg);   color: var(--tb-kpi-up); }
.tablero .tb-delta-down { background: var(--tb-kpi-down-bg); color: var(--tb-kpi-down); }
.tablero .tb-progress   { height: 8px; border-radius: 4px; background: var(--tb-kpi-track); overflow: hidden; }
.tablero .tb-progress > span { display: block; height: 100%; border-radius: 4px; background: var(--tb-kpi-fill); }

/* Ranking y barras dentro de celdas */
.tablero .tb-bar       { height: 8px; border-radius: 4px; background: var(--tb-bar-track); }
.tablero .tb-bar > span { display: block; height: 100%; border-radius: 4px; background: var(--tb-bar-fill); }
.tablero .tb-rank-pos  { font-weight: var(--fw-bold); color: var(--tb-text-muted); }

/* Tablas HTML propias del tablero */
.tablero .tb-table { width: 100%; border-collapse: collapse; font-size: var(--fs-sm); }
.tablero .tb-table th {
  background: var(--tb-table-header-bg); color: var(--tb-table-header-text);
  font-size: var(--fs-xs); font-weight: var(--fw-semibold); letter-spacing: var(--tracking-label);
  text-transform: uppercase; text-align: left; padding: 12px 16px; white-space: nowrap;
}
.tablero .tb-table td { padding: 12px 16px; border-top: 1px solid var(--tb-table-border); }
.tablero .tb-table tbody tr:hover { background: var(--tb-table-row-hover); }
.tablero .tb-table tfoot td, .tablero .tb-table .tb-total {
  background: var(--tb-table-total-bg); color: var(--tb-table-total-text); font-weight: var(--fw-bold);
  border-top: 2px solid var(--tb-table-header-text);
}

/* Tabulator dentro del tablero (Tabla y Tabla dinámica).
   Gana a las reglas de la app porque .tablero .tabulator es más específico que .tabulator. */
.tablero .tabulator {
  font-family: var(--tb-font); font-size: var(--fs-sm);
  background: var(--tb-card-bg); border: 1px solid var(--tb-table-border); border-radius: var(--radius-md);
}
.tablero .tabulator .tabulator-header,
.tablero .tabulator .tabulator-header .tabulator-col,
.tablero .tabulator .tabulator-header .tabulator-col-group {
  background: var(--tb-table-header-bg); color: var(--tb-table-header-text); border-color: var(--tb-table-border);
}
.tablero .tabulator .tabulator-header .tabulator-col .tabulator-col-title {
  font-size: var(--fs-xs); font-weight: var(--fw-semibold); letter-spacing: var(--tracking-label); text-transform: uppercase;
}
.tablero .tabulator-row,
.tablero .tabulator-row.tabulator-row-even { background: var(--tb-card-bg); color: var(--tb-text); }
.tablero .tabulator-row:hover,
.tablero .tabulator-row.tabulator-selectable:hover { background: var(--tb-table-row-hover); }
.tablero .tabulator-row .tabulator-cell { border-color: var(--tb-table-border); }
.tablero .tabulator .tabulator-calcs-holder,
.tablero .tabulator .tabulator-row.tabulator-calcs {
  background: var(--tb-table-total-bg); color: var(--tb-table-total-text); font-weight: var(--fw-bold);
}
.tablero .tabulator .tabulator-footer { background: var(--tb-card-bg); border-color: var(--tb-table-border); color: var(--tb-text-muted); }
.tablero .tabulator .tabulator-footer .tabulator-page.active {
  background: var(--tb-table-header-text); color: var(--tb-card-bg); border-color: var(--tb-table-header-text);
}
```

---

## Archivo 3: `static/js/chart-theme.js` (créalo tal cual)

```js
/* chart-theme.js — ApexCharts toma TODOS sus colores de las variables --tb-* del lienzo.
   Cargar después de apexcharts.min.js y antes de crear gráficos. */
(function () {
  function read(el) {
    var s = getComputedStyle(el || document.getElementById('lienzo') || document.documentElement);
    var v = function (n) { return s.getPropertyValue(n).trim(); };
    return {
      categorical: [1, 2, 3, 4, 5, 6].map(function (i) { return v('--tb-chart-' + i); }),
      sequential:  [1, 2, 3, 4, 5].map(function (i) { return v('--tb-seq-' + i); }),
      diverging: { neg: v('--tb-div-neg'), negMid: v('--tb-div-neg-mid'), zero: v('--tb-div-zero'),
                   posMid: v('--tb-div-pos-mid'), pos: v('--tb-div-pos') },
      axis: v('--tb-chart-axis'), grid: v('--tb-chart-grid'), label: v('--tb-chart-label'),
      font: v('--tb-font')
    };
  }

  /* Opciones base de todos los gráficos. */
  function baseOptions(el) {
    var t = read(el);
    return {
      colors: t.categorical,
      chart: { fontFamily: t.font, foreColor: t.axis, toolbar: { show: false }, background: 'transparent' },
      grid: { borderColor: t.grid, strokeDashArray: 3 },
      dataLabels: { style: { fontSize: '12px', fontWeight: 600 } },
      legend: { fontSize: '12px', labels: { colors: t.label } },
      tooltip: { theme: 'light' },
      stroke: { width: 2 },
      plotOptions: { bar: { borderRadius: 4, columnWidth: '60%' }, pie: { donut: { size: '65%' } } }
    };
  }

  /* Escala secuencial para heatmaps (ej. V de Cramér de 0 a 1). */
  function sequentialScale(min, max, el) {
    var c = read(el).sequential, step = (max - min) / c.length;
    return { ranges: c.map(function (color, i) {
      return { from: min + step * i, to: i === c.length - 1 ? max : min + step * (i + 1), color: color };
    }) };
  }

  /* Escala divergente para la matriz de correlación (−1 a +1). */
  function divergingScale(el) {
    var d = read(el).diverging;
    return { ranges: [
      { from: -1,   to: -0.6, color: d.neg },
      { from: -0.6, to: -0.2, color: d.negMid },
      { from: -0.2, to:  0.2, color: d.zero },
      { from:  0.2, to:  0.6, color: d.posMid },
      { from:  0.6, to:  1,   color: d.pos }
    ] };
  }

  /* Aplica el tema a todos los gráficos nuevos (window.Apex = opciones globales de ApexCharts). */
  function apply(el) { window.Apex = baseOptions(el); }

  apply();

  window.ChartTheme = {
    read: read,
    baseOptions: baseOptions,
    sequentialScale: sequentialScale,
    divergingScale: divergingScale,
    /* Llamar si cambian las variables del tablero en vivo: vuelve a aplicar y actualiza los gráficos existentes. */
    refresh: function (el, charts) {
      apply(el);
      var o = baseOptions(el);
      (charts || []).forEach(function (c) { c.updateOptions({ colors: o.colors, chart: o.chart, grid: o.grid, legend: o.legend }, false, false); });
    }
  };
})();
```

Uso en el código de los widgets:
```js
new ApexCharts(el, { chart: { type: 'bar' }, series, xaxis }).render();   // colores automáticos
new ApexCharts(el, { chart: { type: 'heatmap' },
  plotOptions: { heatmap: { colorScale: ChartTheme.divergingScale() } }, series }).render();      // correlación
new ApexCharts(el, { chart: { type: 'heatmap' },
  plotOptions: { heatmap: { colorScale: ChartTheme.sequentialScale(0, 1) } }, series }).render(); // V de Cramér
```

---

## Archivo 4: `tailwind.config.js`

Ajusta `content` a las rutas reales del proyecto y conserva cualquier plugin que ya exista.

```js
/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ['./templates/**/*.html', './**/templates/**/*.html', './static/js/**/*.js'],
  theme: {
    extend: {
      colors: {
        primary: { DEFAULT: 'var(--color-primary)', hover: 'var(--color-primary-hover)', on: 'var(--color-on-primary)' },
        accent:  { DEFAULT: 'var(--color-accent)', hover: 'var(--color-accent-hover)', on: 'var(--color-on-accent)',
                   text: 'var(--color-accent-text)', soft: 'var(--color-accent-soft)' },
        app: 'var(--color-bg-app)',
        surface: { DEFAULT: 'var(--color-surface)', muted: 'var(--color-surface-muted)' },
        line:    { DEFAULT: 'var(--color-border)', strong: 'var(--color-border-strong)' },
        fg:      { DEFAULT: 'var(--color-text)', muted: 'var(--color-text-muted)', subtle: 'var(--color-text-subtle)' },
        heading: 'var(--color-heading)',
        success: { DEFAULT: 'var(--status-success)', bg: 'var(--status-success-bg)' },
        danger:  { DEFAULT: 'var(--status-danger)',  bg: 'var(--status-danger-bg)' },
        warning: { DEFAULT: 'var(--status-warning)', bg: 'var(--status-warning-bg)' },
      },
      fontFamily: { sans: 'var(--font-sans)', mono: 'var(--font-mono)' },
      fontSize: {
        xs:   ['var(--fs-xs)',   { lineHeight: 'var(--lh-base)' }],
        sm:   ['var(--fs-sm)',   { lineHeight: 'var(--lh-base)' }],
        base: ['var(--fs-base)', { lineHeight: 'var(--lh-base)' }],
        md:   ['var(--fs-md)',   { lineHeight: 'var(--lh-tight)' }],
        lg:   ['var(--fs-lg)',   { lineHeight: 'var(--lh-tight)' }],
        xl:   ['var(--fs-xl)',   { lineHeight: 'var(--lh-tight)' }],
      },
      borderRadius: { sm: 'var(--radius-sm)', DEFAULT: 'var(--radius-md)', md: 'var(--radius-md)',
                      lg: 'var(--radius-lg)', full: 'var(--radius-pill)' },
      boxShadow: { sm: 'var(--shadow-sm)', md: 'var(--shadow-md)' },
      letterSpacing: { label: 'var(--tracking-label)' },
    },
  },
  plugins: [],
};
```

Los colores van en `extend` para que la migración sea gradual y no se rompa nada que todavía use la paleta por defecto. Aun así, al terminar **no debe quedar ninguna clase de la paleta por defecto** (`gray-*`, `orange-*`, `purple-*`, `green-*`…) en las plantillas; la lista de verificación lo comprueba.

Los modificadores de opacidad (`bg-primary/50`) no funcionan con estas variables. Si hace falta transparencia, crea un token.

---

## Archivo 5: `static/src/input.css`

```css
@import "../css/tokens.css";

@tailwind base;
@tailwind components;
@tailwind utilities;

@layer base {
  body { @apply bg-app text-fg font-sans text-base antialiased; }
  h1 { @apply text-xl font-bold text-heading; }
  h2 { @apply text-lg font-semibold text-heading; }
  h3 { @apply text-md font-semibold text-heading; }
  a  { @apply text-accent-text; }
  :focus-visible { outline: 3px solid var(--color-focus-ring); outline-offset: 1px; }
}

@layer components {
  /* Header */
  .app-header { @apply flex items-center gap-3 px-4 py-3;
    background: var(--header-bg); color: var(--header-text); border-bottom: 1px solid var(--header-border); }

  /* Sidebar y módulos */
  .sidebar { background: var(--sidebar-bg); border-right: 1px solid var(--sidebar-border); }
  .nav-item { @apply flex items-center gap-2 min-h-[40px] px-3 rounded-md font-medium text-fg no-underline; }
  .nav-item:hover { @apply bg-surface-muted; }
  .nav-item.is-active, .nav-item[aria-current="page"] { @apply font-semibold;
    background: var(--sidebar-active-bg); color: var(--sidebar-active-text); }
  .sidebar-label { @apply mt-2 text-xs font-semibold uppercase tracking-label text-fg-muted; }
  .module-card { @apply flex items-center gap-3 px-3 py-2 bg-surface border border-line rounded-md cursor-grab; }
  .module-card:hover { @apply border-line-strong shadow-sm; }
  .module-card__icon { @apply grid place-items-center w-8 h-8 rounded-sm shrink-0;
    background: var(--module-icon-bg); color: var(--module-icon-text); }
  .module-card__title { @apply block text-sm font-semibold text-fg; }
  .module-card__desc  { @apply block text-xs text-fg-muted truncate; }

  /* Botones */
  .btn { @apply inline-flex items-center justify-center gap-2 h-9 px-4 rounded-full text-sm font-semibold
                whitespace-nowrap border border-transparent transition-colors cursor-pointer
                disabled:opacity-50 disabled:cursor-not-allowed; }
  .btn-sm { @apply h-8 px-3 text-xs; }
  .btn-primary { background: var(--btn-primary-bg); color: var(--btn-primary-text); }
  .btn-primary:hover:not(:disabled) { background: var(--btn-primary-bg-hover); }
  .btn-secondary { background: var(--btn-secondary-bg); color: var(--btn-secondary-text); border-color: var(--btn-secondary-border); }
  .btn-secondary:hover:not(:disabled) { @apply bg-surface-muted; }
  .btn-dark { background: var(--btn-dark-bg); color: var(--btn-dark-text); }
  .btn-dark:hover:not(:disabled) { @apply bg-primary-hover; }
  .btn-ghost { @apply bg-transparent text-fg-muted; }
  .btn-ghost:hover:not(:disabled) { @apply bg-surface-muted text-fg; }
  .btn-danger { @apply bg-danger-bg text-danger border-danger; }
  .btn-icon { @apply h-9 w-9 px-0 rounded-md bg-transparent text-fg-muted; }
  .btn-icon:hover { @apply bg-surface-muted text-primary; }

  /* Formularios */
  .input, .select { @apply h-[38px] w-full px-3 text-sm text-fg rounded-md;
    background: var(--input-bg); border: 1px solid var(--input-border); }
  .input::placeholder { @apply text-fg-subtle; }
  .input:focus, .select:focus { outline: 3px solid var(--color-focus-ring); border-color: var(--input-border-focus); }
  .input:disabled, .select:disabled { @apply bg-app text-fg-subtle; }
  .field-label { @apply block mb-1 text-xs font-semibold text-fg; }
  .field-help  { @apply mt-1 text-xs text-fg-muted; }
  .checkbox    { accent-color: var(--color-accent); @apply w-4 h-4; }

  /* Tarjetas de la app (no las del lienzo: esas son .tb-widget) */
  .card { @apply bg-surface border border-line rounded-lg shadow-sm; }
  .card-header { @apply px-5 pt-5 pb-3; }
  .card-title  { @apply text-md font-semibold text-heading; }

  /* Tablas propias de la app */
  .table { @apply w-full border-collapse text-sm; }
  .table th { @apply px-4 py-3 text-left text-xs font-semibold uppercase tracking-label whitespace-nowrap;
    background: var(--table-header-bg); color: var(--table-header-text); }
  .table td { @apply px-4 py-3; border-top: 1px solid var(--table-border); }
  .table tbody tr:hover { background: var(--table-row-hover); }
  .table-wrap { @apply overflow-x-auto bg-surface border border-line rounded-lg; }

  /* Tabs */
  .tabs { @apply flex gap-6 border-b border-line; }
  .tab { @apply py-3 -mb-px text-base bg-transparent border-0 border-b-[3px] border-transparent cursor-pointer;
    color: var(--tab-text); }
  .tab[aria-selected="true"], .tab.is-active { @apply font-semibold;
    color: var(--tab-active-text); border-bottom-color: var(--tab-active-border); }

  /* Badges y chips */
  .badge { @apply inline-flex items-center px-2 py-0.5 rounded-full text-xs font-semibold; }
  .badge-success { @apply bg-success-bg text-success; }
  .badge-warning { @apply bg-warning-bg text-warning; }
  .badge-danger  { @apply bg-danger-bg text-danger; }
  .badge-accent  { @apply bg-accent-soft text-accent-text; }
  .code-chip { @apply font-mono text-xs px-1.5 py-0.5 rounded-sm bg-surface-muted text-primary; }

  /* Paneles inferiores (Configuración, Gestionar fuente) */
  .panel { @apply bg-surface rounded-t-lg shadow-md; }
  .panel-header { @apply flex items-center justify-between px-6 py-4 border-b border-line; }
  .panel-title  { @apply text-md font-semibold text-heading; }
  .danger-zone  { @apply p-5 rounded-lg bg-danger-bg text-danger border border-danger; }
}

/* Tabulator en la APP (ej. tabla de columnas en "Gestionar fuente").
   Dentro del lienzo lo sobrescribe tablero-theme.css. */
.tabulator { @apply font-sans text-sm bg-surface border border-line rounded-lg; }
.tabulator .tabulator-header,
.tabulator .tabulator-header .tabulator-col {
  background: var(--table-header-bg); color: var(--table-header-text); border-color: var(--table-border); }
.tabulator .tabulator-header .tabulator-col .tabulator-col-title { @apply text-xs font-semibold uppercase tracking-label; }
.tabulator-row, .tabulator-row.tabulator-row-even { background: var(--color-surface); }
.tabulator-row:hover, .tabulator-row.tabulator-selectable:hover { background: var(--table-row-hover); }
.tabulator-row .tabulator-cell { border-color: var(--table-border); }
.tabulator .tabulator-footer { @apply bg-surface border-line text-fg-muted; }
.tabulator .tabulator-footer .tabulator-page.active {
  background: var(--color-primary); color: var(--color-on-primary); border-color: var(--color-primary); }
```

---

## Migración de la APP (plantillas fuera del lienzo)

| Clase actual (ejemplos) | Reemplazo |
|---|---|
| `bg-orange-500`, `bg-orange-600` en botones | `.btn .btn-primary` |
| `bg-orange-50` / `bg-orange-100` + `text-orange-600` (botón suave) | `.btn .btn-secondary` |
| `bg-purple-*`, `bg-violet-*`, `text-purple-*` ("Generar con IA") | `.btn .btn-primary` |
| `text-orange-500/600` como texto o link | `text-accent-text` |
| `bg-orange-50` en ítem activo del menú | `.nav-item.is-active` |
| `bg-white` | `bg-surface` |
| `bg-gray-50`, `bg-slate-50` (fondo de página) | `bg-app` |
| `bg-gray-100`, `bg-slate-100` (zonas atenuadas) | `bg-surface-muted` |
| `border-gray-200`, `border-gray-300` | `border-line` / `border-line-strong` |
| `text-gray-900`, `text-black` | `text-fg` |
| `text-gray-500`, `text-gray-600` | `text-fg-muted` |
| `text-gray-400` (placeholders, ayudas) | `text-fg-subtle` |
| Títulos (`text-gray-900 font-bold` en h1–h3) | `text-heading` |
| `text-red-*`, `bg-red-50` | `text-danger`, `bg-danger-bg` |
| `text-green-*`, `bg-green-50` (estados) | `text-success`, `bg-success-bg` |
| `text-yellow-*`, `bg-amber-50` | `text-warning`, `bg-warning-bg` |
| `text-[15px]` u otros tamaños arbitrarios | el más cercano de la escala: `text-xs/sm/base/md/lg/xl` |
| `rounded-xl`, `rounded-2xl` en tarjetas | `rounded-lg` (o `.card`) |
| `<table>` con estilos sueltos | `.table-wrap` + `.table` |
| Pestañas con bordes naranjas sueltos | `.tabs` + `.tab` con `aria-selected` |
| Inputs y selects con clases sueltas | `.input` / `.select` |

### Jerarquía de botones
- **Primario (`.btn-primary`, naranja con texto navy):** una acción principal por zona. Header del editor: "Generar con IA". Listado: "Crear tablero". Paneles: "Guardar" / "Guardar cambios". Estado vacío del lienzo: "Generar con IA".
- **Secundario (`.btn-secondary`):** "Gestionar fuentes", "Vista previa", "Compartir", "Cambiar fuente", "Agregar fuente", "Generar nombres con IA".
- **Oscuro (`.btn-dark`):** "Actualizar datos" u otra acción fuerte que no sea la principal.
- **Fantasma / icono (`.btn-ghost`, `.btn-icon`):** editar, duplicar y eliminar en filas de tabla; "Cerrar" en paneles.
- **Peligro (`.btn-danger`):** solo "Eliminar tablero definitivamente" y confirmaciones destructivas.

---

## Migración de los TABLEROS (dentro del lienzo)

1. **Contenedor del lienzo**: en el editor, en Vista previa y en la vista compartida, el contenedor queda así:
   ```html
   <div id="lienzo" class="tablero" data-tema-tablero="{{ tablero.tema|default:'institucional' }}"
        {% if tablero.colores_personalizados %}style="{{ tablero.estilo_variables }}"{% endif %}>
   ```
   `estilo_variables` es una propiedad del modelo que hoy devuelve una cadena vacía (ver punto 6).
2. **Tarjetas de widget**: reemplaza las clases de color de cada tarjeta por `.tb-widget`, y la etiqueta superior ("GRÁFICO DE BARRAS", "TARJETA KPI"…) por `.tb-widget-title`. Los controles de edición de la tarjeta (lápiz, cerrar, asa de arrastre) son de la app: siguen con clases de la app.
3. **KPI**: valor → `.tb-kpi-value`; etiqueta → `.tb-kpi-label`; variación → `.tb-delta .tb-delta-up` / `.tb-delta-down`; barra de progreso → `.tb-progress > span`; texto secundario → `.tb-kpi-sub`. La línea de tendencia del KPI (si es ApexCharts) toma `--tb-chart-1` desde `chart-theme.js`.
4. **Tabla y Tabla dinámica (Tabulator)**: no les pongas clases de color; `tablero-theme.css` ya las estiliza con `.tablero .tabulator`. Las filas de "Total general" usan los cálculos de Tabulator (`tabulator-calcs`) o la clase `.tb-total`. Si alguna tabla dinámica es HTML propio, usa `.tb-table`.
5. **Gráficos (ApexCharts)**:
   - Elimina todo `colors: [...]`, `fill.colors`, `stroke.colors`, `foreColor`, `grid.borderColor` y `colorScale` con colores fijos en el código de los widgets. Los valores base salen de `window.Apex` (fijado por `chart-theme.js`).
   - Matriz de correlación → `ChartTheme.divergingScale()`.
   - V de Cramér y cualquier mapa de calor de 0 a 1 → `ChartTheme.sequentialScale(0, 1)`.
   - Ranking: barras con `.tb-bar > span` (HTML) o color `--tb-chart-1` si es ApexCharts.
   - Si un gráfico necesita destacar un único valor (p. ej., el líder del ranking), usa `--tb-chart-2` (naranja) para ese valor y `--tb-chart-1` para el resto.
   - Si el código guarda las instancias de los gráficos, mantenlas en un arreglo para poder llamar `ChartTheme.refresh(lienzo, charts)` cuando cambie el tema.
6. **Modelo y Configuración (preparar para colores configurables, sin construir aún el editor de colores):**
   - En el modelo del tablero, el campo de tema (hoy Verde / Azul / Índigo) pasa a tener como opción por defecto `"institucional"`. Crea una migración de datos que pase **todos** los tableros existentes a `"institucional"`.
   - Agrega un campo `colores_personalizados = JSONField(default=dict, blank=True)` para el futuro. Guarda pares `{"--tb-chart-1": "#1d3594", …}`.
   - Agrega la propiedad `estilo_variables` en el modelo: une `colores_personalizados` en `"--tb-chart-1:#1d3594;--tb-chart-2:#ff8301"`, **validando** que cada clave empiece con `--tb-` y cada valor sea un hex válido (`^#[0-9a-fA-F]{6}$`). Descarta lo que no cumpla. Esto evita inyectar CSS arbitrario.
   - En el panel Configuración › Apariencia y tema, reemplaza las opciones Verde / Azul / Índigo por una sola opción "Institucional", seleccionada, con una muestra de la paleta (6 círculos con `--tb-chart-1…6`). Texto de ayuda: "Los gráficos y tablas de este tablero usan los colores institucionales."
   - Las plantillas generadas por IA (specs JSON) no deben incluir colores. Si el esquema del spec tiene campos de color, ignóralos al renderizar y quítalos del prompt de generación.

---

## Pasos

1. Crea `tokens.css`, `tablero-theme.css` y `chart-theme.js`; actualiza `tailwind.config.js` e `input.css`.
2. Ajusta el orden de carga en `base.html`, Vista previa y vista compartida.
3. Migra la app pantalla por pantalla: layout base (header + sidebar), listado "Recientes", editor (header y panel de módulos), Configuración, Gestionar fuente de datos (Columnas y Campos calculados) y modales.
4. Migra el lienzo: contenedor, tarjetas, KPI, tablas, tabla dinámica, ranking y cada tipo de gráfico.
5. Aplica los cambios de modelo, la migración de datos y el panel Configuración.
6. Elimina los estilos obsoletos: `<style>` en plantillas, `style=""` con colores, CSS personalizado previo con hex y paletas fijas en JS.
7. Compila Tailwind y revisa que no haya errores de `@apply` con clases inexistentes.

## Verificación antes de terminar

- [ ] `grep -rnE "#[0-9a-fA-F]{3,6}\b" templates/ static/src/ static/js/` no devuelve colores (fuera de `tokens.css` y `tablero-theme.css`).
- [ ] `grep -rnE "(bg|text|border|fill|stroke)-(gray|slate|orange|purple|violet|indigo|red|green|emerald|blue|amber|yellow)-[0-9]" templates/ static/js/` no devuelve resultados.
- [ ] Dentro de las plantillas de widgets no hay utilidades de color de la app (`bg-surface`, `text-fg*`, `text-heading`, `border-line`…), solo clases `.tb-*`.
- [ ] Ningún botón naranja tiene texto blanco, y ningún texto usa el naranja claro (`#ff8301`) como color de letra.
- [ ] Los encabezados de tabla (app y tableros) se ven con fondo gris claro y texto navy, y el "Total general" con fondo navy muy claro y texto en negrita.
- [ ] Barras, líneas, dona y dispersión usan navy y naranja como primeros colores; la matriz de correlación va de naranja (negativo) a navy (positivo); la V de Cramér va de navy claro a navy oscuro.
- [ ] Todos los tableros existentes quedan con tema "institucional".
- [ ] Prueba de aislamiento: pon temporalmente en el lienzo `style="--tb-chart-1:#c02626; --tb-table-header-bg:#fdecec"`. Solo deben cambiar el primer color de los gráficos y los encabezados de las tablas del tablero; la app (incluida la tabla de "Gestionar fuente") no debe cambiar. Después, quita la prueba.
- [ ] Prueba del sistema: cambia `--brand-orange-500` en `tokens.css`. Deben cambiar los botones primarios y el segundo color de los gráficos sin tocar nada más. Después, revierte el cambio.
- [ ] Un valor inválido en `colores_personalizados` (clave sin `--tb-` o valor que no sea hex) no aparece en el HTML.

Al terminar, entrega la lista de archivos modificados y cualquier caso donde no encontraste un token adecuado (en vez de inventar un valor).
