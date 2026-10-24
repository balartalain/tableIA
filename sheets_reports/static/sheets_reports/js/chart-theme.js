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

  /* Un color CSS cualquiera como [r, g, b] (el canvas lo normaliza a #rrggbb). */
  var swatch = document.createElement('canvas').getContext('2d');
  function rgb(c) {
    swatch.fillStyle = '#000';
    swatch.fillStyle = c;
    var hex = swatch.fillStyle;
    return [1, 3, 5].map(function (i) { return parseInt(hex.slice(i, i + 2), 16); });
  }

  /* Luminancia relativa (WCAG) de un color CSS. */
  function luminance(c) {
    var l = rgb(c).map(function (x) {
      x /= 255;
      return x <= 0.03928 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4);
    });
    return 0.2126 * l[0] + 0.7152 * l[1] + 0.0722 * l[2];
  }

  /* De los `candidates`, el color de texto con más contraste sobre `bg`. */
  function textOn(bg, candidates) {
    var lb = luminance(bg);
    var contrast = function (c) {
      var lc = luminance(c);
      return (Math.max(lb, lc) + 0.05) / (Math.min(lb, lc) + 0.05);
    };
    return candidates.reduce(function (best, c) { return contrast(c) > contrast(best) ? c : best; });
  }

  /* Escala divergente continua de −1 a +1 (matriz de correlación): `steps` rangos angostos
     cuyo color pasa gradualmente del neutro (0) a los cuatro tonos de cada signo, puestos en
     |valor| = 0,3 · 0,5 · 0,7 · 0,9 (el centro de cada nivel de Evans); de 0,9 a 1, el más
     fuerte. Valores cercanos quedan con colores cercanos, sin saltos en los cortes. */
  function divergingGradient(el, steps) {
    var d = read(el).diverging, n = steps || 100;
    var at = [0, 0.3, 0.5, 0.7, 0.9];
    var zero = rgb(d.zero), neg = d.neg.map(rgb), pos = d.pos.map(rgb);
    var colorAt = function (v) {
      var tones = [zero].concat(v < 0 ? neg : pos), a = Math.min(Math.abs(v), at[at.length - 1]);
      var i = 0;
      while (a > at[i + 1]) i++;
      var t = (a - at[i]) / (at[i + 1] - at[i]);
      var mix = tones[i].map(function (x, k) { return Math.round(x + (tones[i + 1][k] - x) * t); });
      return '#' + mix.map(function (x) { return x.toString(16).padStart(2, '0'); }).join('');
    };
    var ranges = [];
    for (var k = 0; k < n; k++) {
      var from = -1 + (2 * k) / n, to = -1 + (2 * (k + 1)) / n;
      ranges.push({ from: from, to: to, color: colorAt((from + to) / 2) });
    }
    return { ranges: ranges };
  }

  window.ChartTheme = {
    read: read,
    color: color,
    baseOptions: baseOptions,
    sequentialScale: sequentialScale,
    divergingGradient: divergingGradient,
    textOn: textOn,
    /* Llamar si cambian las variables del tablero en vivo: actualiza los gráficos existentes. */
    refresh: function (el, charts) {
      var o = baseOptions(el);
      (charts || []).forEach(function (c) {
        c.updateOptions({ colors: o.colors, chart: o.chart, grid: o.grid, legend: o.legend }, false, false);
      });
    }
  };
})();
