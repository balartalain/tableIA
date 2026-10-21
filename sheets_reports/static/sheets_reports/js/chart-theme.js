/* chart-theme.js — ApexCharts toma TODOS sus colores de las variables --tb-* del lienzo.
   Cargar después de apexcharts.min.js y antes de los widgets.

   Las variables viven en el contenedor `.tablero` (no en :root), así que cada lectura se hace
   sobre ese elemento cuando se arma el gráfico: no hay opciones globales (window.Apex), que
   además alcanzarían a los gráficos de la app. ApexCharts necesita colores resueltos (calcula
   tonos y rangos a partir de ellos), por eso se devuelven los valores computados, no var(--…). */
(function () {
  function board(el) {
    if (el && el.closest) {
      var t = el.closest('.tablero');
      if (t) return t;
    }
    return document.querySelector('.tablero') || document.documentElement;
  }

  function read(el) {
    var s = getComputedStyle(board(el));
    var v = function (n) { return s.getPropertyValue(n).trim(); };
    return {
      categorical: [1, 2, 3, 4, 5, 6].map(function (i) { return v('--tb-chart-' + i); }),
      onCategorical: [1, 2, 3, 4, 5, 6].map(function (i) { return v('--tb-chart-' + i + '-on'); }),
      sequential:  [1, 2, 3, 4, 5].map(function (i) { return v('--tb-seq-' + i); }),
      onSequential: [1, 2, 3, 4, 5].map(function (i) { return v('--tb-seq-' + i + '-on'); }),
      // Cuatro tonos por lado, del más débil al más fuerte, y el color del texto sobre cada uno.
      diverging: {
        zero: v('--tb-div-zero'), zeroOn: v('--tb-div-zero-on'),
        neg: [1, 2, 3, 4].map(function (i) { return v('--tb-div-neg-' + i); }),
        pos: [1, 2, 3, 4].map(function (i) { return v('--tb-div-pos-' + i); }),
        negOn: [1, 2, 3, 4].map(function (i) { return v('--tb-div-neg-' + i + '-on'); }),
        posOn: [1, 2, 3, 4].map(function (i) { return v('--tb-div-pos-' + i + '-on'); })
      },
      axis: v('--tb-chart-axis'), grid: v('--tb-chart-grid'), label: v('--tb-chart-label'),
      ref: v('--tb-chart-ref'), refText: v('--tb-chart-ref-text'), stroke: v('--tb-chart-stroke'),
      card: v('--tb-card-bg'), border: v('--tb-card-border'), bg: v('--tb-bg'),
      text: v('--tb-text'), textMuted: v('--tb-text-muted'), track: v('--tb-bar-track'),
      font: v('--tb-font')
    };
  }

  /* Un token suelto del lienzo, resuelto (ej. ChartTheme.color('--tb-status-bad', el)). */
  function color(name, el) {
    return getComputedStyle(board(el)).getPropertyValue(name).trim();
  }

  /* Opciones base de todos los gráficos del lienzo; cada widget las mezcla con las suyas. */
  function baseOptions(el) {
    var t = read(el);
    return {
      colors: t.categorical,
      chart: { fontFamily: t.font, foreColor: t.axis, background: 'transparent' },
      grid: { borderColor: t.grid, strokeDashArray: 3 },
      dataLabels: { style: { fontSize: '12px', fontWeight: 600 } },
      legend: { fontSize: '12px', labels: { colors: t.label } },
      tooltip: { theme: 'light' }
    };
  }

  /* Escala secuencial para heatmaps (ej. V de Cramér de 0 a 1). */
  function sequentialScale(min, max, el) {
    var c = read(el).sequential, step = (max - min) / c.length;
    return { ranges: c.map(function (col, i) {
      return { from: min + step * i, to: i === c.length - 1 ? max : min + step * (i + 1), color: col };
    }) };
  }

  /* Escala divergente de −1 a +1 (matriz de correlación): el neutro hasta el primer corte y
     un tono por lado entre cada par de cortes. `cuts`: los cuatro |valor| donde empieza cada
     tono (por defecto, la escala de Evans). Nueve tramos, de −1 a +1. */
  function divergingScale(el, cuts) {
    var d = read(el).diverging, c = cuts || [0.2, 0.4, 0.6, 0.8];
    var upper = function (i) { return i === c.length - 1 ? 1 : c[i + 1]; };
    var neg = c.map(function (from, i) { return { from: -upper(i), to: -from, color: d.neg[i] }; }).reverse();
    var pos = c.map(function (from, i) { return { from: from, to: upper(i), color: d.pos[i] }; });
    return { ranges: neg.concat([{ from: -c[0], to: c[0], color: d.zero }], pos) };
  }

  window.ChartTheme = {
    read: read,
    color: color,
    baseOptions: baseOptions,
    sequentialScale: sequentialScale,
    divergingScale: divergingScale,
    /* Llamar si cambian las variables del tablero en vivo: actualiza los gráficos existentes. */
    refresh: function (el, charts) {
      var o = baseOptions(el);
      (charts || []).forEach(function (c) {
        c.updateOptions({ colors: o.colors, chart: o.chart, grid: o.grid, legend: o.legend }, false, false);
      });
    }
  };
})();
