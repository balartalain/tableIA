// Pestaña «Correos» de la vista compartida (board_view.html, solo con el envío activado): el
// KPI de notificados, la tabla de destinatarios y el registro de envíos. Mientras la pestaña
// está a la vista consulta cada pocos segundos los totales del envío; si cambiaron (el
// crontab envió correos), vuelve a pedir las tablas.
const EMAIL_REPORT_POLL_MS = 10000;
const EMAIL_REPORT_PAGE_SIZE = 50;
const EMAIL_STATUS_LABELS = { pending: 'Pendiente', sent: 'Notificado', failed: 'Falló' };
const EMAIL_STATUS_CLASSES = {
  pending: 'bg-paper text-ink/60 border-line',
  sent: 'bg-moss-50 text-moss-700 border-moss-100',
  failed: 'bg-red-50 text-red-700 border-red-200',
};

document.addEventListener('DOMContentLoaded', () => {
  const tabs = Array.from(document.querySelectorAll('#view-tabs .view-tab'));
  const panels = { charts: document.getElementById('tab-charts'), emails: document.getElementById('tab-emails') };
  const reportUrl = apiUrl(`/api/dashboard/${window.DASHBOARD_ID}/email-report/`);
  const errorEl = document.getElementById('email-report-error');
  let summary = null;
  let tables = null;
  let timer = null;

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

  const buildTables = () => ({
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
    logs: remoteTable('#email-logs-table', 'logs/', [
      { title: 'Fecha', field: 'created_at', headerSort: false, width: 150, formatter: dateTime },
      { title: 'Nivel', field: 'level', headerSort: false, width: 110,
        formatter: (cell) => (cell.getValue() === 'error' ? 'Error' : 'Información') },
      { title: 'Mensaje', field: 'message', headerSort: false, minWidth: 280, tooltip: true },
      { title: 'Destinatario', field: 'recipient_email', headerSort: false, minWidth: 180 },
      { title: 'Remitente', field: 'sender_email', headerSort: false, minWidth: 180 },
    ], 'Todavía no se ejecutó ningún envío.'),
  });

  const renderSummary = (data) => {
    const number = (n) => n.toLocaleString('es');
    document.getElementById('email-kpi-sent').textContent = number(data.sent);
    document.getElementById('email-kpi-total').textContent = number(data.total);
    document.getElementById('email-kpi-detail').textContent =
      `${number(data.pending)} pendientes · ${number(data.failed)} con error`;
    document.getElementById('email-kpi-next').textContent = data.next_run
      ? `Próximo envío: ${new Date(data.next_run).toLocaleString('es', { weekday: 'long', day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit' })}`
      : (data.pending ? '' : 'Envío completado.');
  };

  const changed = (a, b) => !a
    || ['total', 'sent', 'pending', 'failed', 'last_log_id'].some(key => a[key] !== b[key]);

  // Pide los totales; si cambiaron, pinta el KPI y vuelve a pedir las tablas.
  const refresh = async ({ initial = false } = {}) => {
    if (initial) setPageLoading(true);
    try {
      const r = await fetch(reportUrl);
      const data = await r.json().catch(() => null);
      if (!r.ok || !data) throw new Error((data && data.error) || `Error ${r.status} al cargar el reporte de correos`);
      errorEl.classList.add('hidden');
      const isNew = changed(summary, data);
      renderSummary(data);
      if (isNew && summary && tables) {
        tables.recipients.replaceData();
        tables.logs.replaceData();
      }
      summary = data;
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
  // Al volver a la página, no espera al siguiente ciclo.
  document.addEventListener('visibilitychange', () => { if (!document.hidden && timer) refresh(); });
  showTab(window.location.hash === '#correos' ? 'emails' : 'charts');
});
