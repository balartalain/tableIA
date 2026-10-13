// Pestaña «Seguimiento de correos» de la vista compartida (board_view.html, solo con el envío activado): en
// qué está el envío, el avance, los enviados por día, el detalle por remitente, los errores
// por motivo, la tabla de destinatarios y el registro de envíos. Mientras la pestaña está a
// la vista consulta cada pocos segundos los totales del envío; si cambiaron (el crontab
// envió correos), vuelve a pedir el gráfico y las tablas.
const EMAIL_REPORT_POLL_MS = 10000;
const EMAIL_REPORT_PAGE_SIZE = 50;
const EMAIL_STATUS_LABELS = { pending: 'Pendiente', sent: 'Notificado', failed: 'Falló' };
const EMAIL_STATUS_CLASSES = {
  pending: 'bg-paper text-ink/60 border-line',
  sent: 'bg-moss-50 text-moss-700 border-moss-100',
  failed: 'bg-red-50 text-red-700 border-red-200',
};

// La franja de estado: icono y colores de cada `state` del reporte.
const EMAIL_STATE_STYLES = {
  running: { box: 'bg-moss-50 border-moss-100', icon: 'ti-loader-2 animate-spin text-moss-700' },
  scheduled: { box: 'bg-white border-line', icon: 'ti-clock text-ink/50' },
  stopped: { box: 'bg-red-50 border-red-200', icon: 'ti-alert-triangle text-red-600' },
  done: { box: 'bg-moss-50 border-moss-100', icon: 'ti-circle-check text-moss-700' },
};
const EMAIL_CHART_HEIGHT = 240;

document.addEventListener('DOMContentLoaded', () => {
  const tabs = Array.from(document.querySelectorAll('#view-tabs .view-tab'));
  const panels = { charts: document.getElementById('tab-charts'), emails: document.getElementById('tab-emails') };
  const reportUrl = apiUrl(`/api/dashboard/${window.DASHBOARD_ID}/email-report/`);
  const errorEl = document.getElementById('email-report-error');
  let summary = null;
  let tables = null;
  let chart = null;
  let timer = null;
  const logsEl = document.getElementById('email-logs');
  const number = (n) => n.toLocaleString('es');
  const longDate = (value, options) => new Date(value).toLocaleString('es', options);

  const dateTime = (cell) => {
    const value = cell.getValue();
    return value ? new Date(value).toLocaleString('es', { dateStyle: 'short', timeStyle: 'short' }) : '';
  };

  // Una tabla paginada en el servidor ({rows, total_rows, last_page}).
  const remoteTable = (selector, path, columns, placeholder) => new Tabulator(selector, {
    columns,
    layout: 'fitColumns',
    placeholder,
    pagination: true,
    paginationMode: 'remote',
    paginationSize: EMAIL_REPORT_PAGE_SIZE,
    ajaxURL: `${reportUrl}${path}`,
    ajaxURLGenerator: (url, config, params) =>
      `${url}?${new URLSearchParams({ page: params.page || 1, size: params.size || EMAIL_REPORT_PAGE_SIZE })}`,
    ajaxResponse: (url, params, response) =>
      ({ last_page: response.last_page || 1, last_row: response.total_rows || 0, data: response.rows || [] }),
    paginationCounter: 'rows',
  });

  // Una tabla corta con los datos que trae `stats/`.
  const localTable = (selector, columns, placeholder) => new Tabulator(selector, {
    columns, layout: 'fitColumns', placeholder, data: [],
  });

  const logsTable = () => remoteTable('#email-logs-table', 'logs/', [
    { title: 'Fecha', field: 'created_at', headerSort: false, width: 150, formatter: dateTime },
    { title: 'Mensaje', field: 'message', headerSort: false, minWidth: 280, tooltip: true, formatter: (cell) => {
      if (cell.getData().level === 'error') cell.getElement().classList.add('text-red-700');
      return cell.getValue();
    } },
    { title: 'Destinatario', field: 'recipient_email', headerSort: false, minWidth: 180 },
    { title: 'Remitente', field: 'sender_email', headerSort: false, minWidth: 180 },
  ], 'Todavía no se ejecutó ningún envío.');

  const buildTables = () => ({
    senders: localTable('#email-senders-table', [
      { title: 'Remitente', field: 'email', headerSort: false, minWidth: 200, formatter: (cell) =>
        (cell.getData().active ? cell.getValue() : `${cell.getValue()} (quitado)`) },
      { title: 'Hoy', field: 'sent_today', headerSort: false, width: 80, hozAlign: 'right' },
      { title: 'Total', field: 'sent_total', headerSort: false, width: 80, hozAlign: 'right' },
      { title: 'Errores', field: 'errors', headerSort: false, width: 90, hozAlign: 'right' },
      { title: 'Último error', field: 'last_error', headerSort: false, minWidth: 180, tooltip: true },
    ], 'No hay remitentes.'),
    errors: localTable('#email-errors-table', [
      { title: 'Motivo', field: 'message', headerSort: false, minWidth: 200, tooltip: true },
      { title: 'Veces', field: 'count', headerSort: false, width: 80, hozAlign: 'right' },
      { title: 'Última vez', field: 'last_at', headerSort: false, width: 140, formatter: dateTime },
    ], 'No hay errores.'),
    // El registro está plegado: su tabla se crea al desplegarlo.
    logs: logsEl.open ? logsTable() : null,
    recipients: remoteTable('#email-recipients-table', 'recipients/', [
      { title: 'Correo', field: 'email', headerSort: false, minWidth: 220 },
      { title: 'Estado', field: 'status', headerSort: false, width: 130, formatter: (cell) => {
        const status = cell.getValue();
        const badge = document.createElement('span');
        badge.className = `inline-flex text-[11px] font-semibold rounded-full border px-2 py-0.5 ${EMAIL_STATUS_CLASSES[status] || ''}`;
        badge.textContent = EMAIL_STATUS_LABELS[status] || status;
        return badge;
      } },
      { title: 'Enviado', field: 'sent_at', headerSort: false, width: 150, formatter: dateTime },
      { title: 'Remitente', field: 'sender_email', headerSort: false, minWidth: 180 },
      { title: 'Error', field: 'error', headerSort: false, minWidth: 200, tooltip: true },
    ], 'No hay destinatarios.'),
  });

  const renderStatus = (data) => {
    const style = EMAIL_STATE_STYLES[data.state] || EMAIL_STATE_STYLES.scheduled;
    let title = 'Envío completado';
    let detail = `${number(data.sent)} notificados${data.failed ? ` · ${number(data.failed)} no se pudieron enviar` : ''}.`;
    if (data.state === 'running') {
      title = 'Enviando ahora';
      detail = `${number(data.sent_today)} de ${number(data.daily_limit)} correos de hoy.`;
    } else if (data.state === 'stopped') {
      title = 'El envío de hoy terminó antes de llegar al máximo';
      detail = `${number(data.sent_today)} de ${number(data.daily_limit)} correos de hoy.`
        + (data.last_error ? ` Último error${data.last_error.sender_email ? ` (${data.last_error.sender_email})` : ''}: ${data.last_error.message}` : '');
    } else if (data.state === 'scheduled') {
      title = data.next_run
        ? `Próximo envío: ${longDate(data.next_run, { weekday: 'long', day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit' })}`
        : 'No hay un próximo envío programado';
      detail = data.sent_today ? `Hoy se enviaron ${number(data.sent_today)} de ${number(data.daily_limit)}.`
        : `Hasta ${number(data.daily_limit)} correos por día.`;
    }
    document.getElementById('email-status').className = `flex items-start gap-3 border rounded p-4 ${style.box}`;
    document.getElementById('email-status-icon').className = `ti text-xl ${style.icon}`;
    document.getElementById('email-status-title').textContent = title;
    document.getElementById('email-status-detail').textContent = detail;
  };

  const renderSummary = (data) => {
    renderStatus(data);
    const share = (n) => `${data.total ? (n / data.total) * 100 : 0}%`;
    document.getElementById('email-kpi-sent').textContent = number(data.sent);
    document.getElementById('email-kpi-total').textContent = number(data.total);
    document.getElementById('email-bar-sent').style.width = share(data.sent);
    document.getElementById('email-bar-failed').style.width = share(data.failed);
    document.getElementById('email-legend-sent').textContent = `${number(data.sent)} notificados`;
    document.getElementById('email-legend-pending').textContent = `${number(data.pending)} pendientes`;
    document.getElementById('email-legend-failed').textContent = `${number(data.failed)} fallidos`;
    // La fecha llega sin hora (AAAA-MM-DD): se pinta como día local, no en UTC.
    document.getElementById('email-kpi-end').textContent = data.estimated_end
      ? `Termina aprox. el ${longDate(`${data.estimated_end}T00:00`, { weekday: 'long', day: 'numeric', month: 'long' })}.`
      : '';
  };

  // Barras de enviados por día, con la línea del máximo diario.
  const renderChart = (stats) => {
    const days = stats.by_day;
    const top = Math.max(stats.daily_limit, ...days.map(d => d.sent));
    const options = {
      series: [{ name: 'Enviados', data: days.map(d => d.sent) }],
      xaxis: {
        categories: days.map(d => longDate(`${d.date}T00:00`, { day: 'numeric', month: 'short' })),
        axisTicks: { show: false },
      },
      yaxis: { min: 0, max: Math.ceil(top * 1.15), forceNiceScale: true, labels: { formatter: (v) => number(Math.round(v)) } },
      plotOptions: { bar: { borderRadius: 4, borderRadiusApplication: 'end', columnWidth: `${Math.min(60, Math.max(days.length, 1) * 8)}%` } },
      dataLabels: { enabled: days.length <= 12, offsetY: -18, style: { colors: ['#333'], fontWeight: 600 } },
      annotations: { yaxis: [{
        y: stats.daily_limit, borderColor: '#6b7280', strokeDashArray: 4,
        label: { text: `Máximo diario: ${number(stats.daily_limit)}`, borderWidth: 0, style: { color: '#4b5563', background: 'transparent' } },
      }] },
    };
    if (chart) return chart.updateOptions(options);
    chart = new ApexCharts(document.getElementById('email-by-day-chart'), {
      ...options,
      chart: { type: 'bar', height: EMAIL_CHART_HEIGHT, fontFamily: 'inherit', toolbar: { show: false }, animations: { enabled: false } },
      colors: [getComputedStyle(document.documentElement).getPropertyValue('--color-moss-500').trim() || '#1F8A5F'],
      plotOptions: { ...options.plotOptions, bar: { ...options.plotOptions.bar, dataLabels: { position: 'top' } } },
      grid: { borderColor: '#eee', strokeDashArray: 3 },
      tooltip: { y: { formatter: (v) => `${number(v)} correos` } },
      noData: { text: 'Todavía no se envió ningún correo.' },
    });
    return chart.render();
  };

  // El gráfico y las tablas de remitentes y de errores.
  const refreshStats = async () => {
    const r = await fetch(`${reportUrl}stats/`);
    const stats = await r.json().catch(() => null);
    if (!r.ok || !stats) throw new Error((stats && stats.error) || `Error ${r.status} al cargar el reporte de correos`);
    renderChart(stats);
    tables.senders.setData(stats.by_sender);
    tables.errors.setData(stats.errors);
  };

  const changed = (a, b) => !a
    || ['state', 'total', 'sent', 'pending', 'failed', 'sent_today', 'last_log_id'].some(key => a[key] !== b[key]);

  // Pide los totales; si cambiaron, vuelve a pedir el gráfico y las tablas.
  const refresh = async ({ initial = false } = {}) => {
    if (initial) setPageLoading(true);
    try {
      const r = await fetch(reportUrl);
      const data = await r.json().catch(() => null);
      if (!r.ok || !data) throw new Error((data && data.error) || `Error ${r.status} al cargar el reporte de correos`);
      errorEl.classList.add('hidden');
      const isNew = changed(summary, data);
      renderSummary(data);
      if (isNew && summary) {
        tables.recipients.replaceData();
        if (tables.logs) tables.logs.replaceData();
      }
      summary = data;
      if (isNew) await refreshStats();
    } catch (e) {
      errorEl.textContent = e.message === 'Failed to fetch' ? 'No se pudo conectar con el servidor.' : e.message;
      errorEl.classList.remove('hidden');
    } finally {
      if (initial) setPageLoading(false);
    }
  };

  const startPolling = () => {
    if (!tables) tables = buildTables();
    refresh({ initial: !summary });
    if (!timer) timer = setInterval(() => { if (!document.hidden) refresh(); }, EMAIL_REPORT_POLL_MS);
  };

  const stopPolling = () => {
    clearInterval(timer);
    timer = null;
  };

  const showTab = (name) => {
    tabs.forEach(tab => {
      const active = tab.dataset.tab === name;
      tab.setAttribute('aria-selected', active);
      tab.classList.toggle('border-moss-500', active);
      tab.classList.toggle('text-ink', active);
      tab.classList.toggle('border-transparent', !active);
      tab.classList.toggle('text-ink/50', !active);
    });
    panels.charts.classList.toggle('hidden', name !== 'charts');
    panels.emails.classList.toggle('hidden', name !== 'emails');
    history.replaceState(null, '', name === 'emails' ? '#correos' : window.location.pathname + window.location.search);
    if (name === 'emails') {
      startPolling();
    } else {
      stopPolling();
      // Los gráficos dibujados con la pestaña oculta toman su ancho al volver.
      window.dispatchEvent(new Event('resize'));
    }
  };

  tabs.forEach(tab => tab.addEventListener('click', () => showTab(tab.dataset.tab)));
  logsEl.addEventListener('toggle', () => {
    if (logsEl.open && tables && !tables.logs) tables.logs = logsTable();
  });
  // Al volver a la página, no espera al siguiente ciclo.
  document.addEventListener('visibilitychange', () => { if (!document.hidden && timer) refresh(); });
  showTab(window.location.hash === '#correos' ? 'emails' : 'charts');
});
