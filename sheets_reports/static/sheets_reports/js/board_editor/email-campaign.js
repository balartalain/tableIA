// Envío de correos del tablero (bottom sheet «Configurar envío de correos»): los destinatarios (desde un
// CSV), los remitentes (cuentas de Gmail con su contraseña de aplicación), cuántos correos se
// envían por día, en lotes de qué tamaño y cuándo, y el mensaje. El envío lo ejecuta el crontab del servidor; con el
// envío activado, la vista compartida muestra la pestaña «Seguimiento de correos» (email-report.js).
const EMAIL_WEEKDAYS = [
  { value: 0, label: 'Lun' }, { value: 1, label: 'Mar' }, { value: 2, label: 'Mié' },
  { value: 3, label: 'Jue' }, { value: 4, label: 'Vie' }, { value: 5, label: 'Sáb' },
  { value: 6, label: 'Dom' },
];

// Tope por imagen del mensaje: van incrustadas en base64 dentro del cuerpo (PUT JSON).
const EMAIL_BODY_IMAGE_BYTES = 300 * 1024;

function emailCampaign() {
  // Fuera de la reactividad de Alpine: la instancia de Quill y el input de archivo no son datos.
  let quill = null;
  let imageInput = null;
  let syncing = false;

  return {
    WEEKDAYS: EMAIL_WEEKDAYS,
    loading: false,
    error: '',
    saving: false,
    // Lo que se edita. Cada remitente: {email, password (solo si se escribe una nueva), has_password}.
    form: { enabled: false, subject: '', body: '', daily_limit: 100, batch_size: 50, frequency: 'daily', send_time: '08:00', weekdays: [], senders: [] },
    report: { total: 0, sent: 0, pending: 0, failed: 0, next_run: null },
    timeZone: '',
    cronMinutes: 5,
    uploading: false,
    // Resultado de la última carga del CSV: {added, duplicates, invalid, invalid_count}.
    uploadResult: null,
    confirmClear: false,
    showPasswordHelp: false,
    testTo: '',
    testing: false,
    testMessage: '',
    testFailed: false,

    init() {
      window.addEventListener('email-campaign:open', () => this.load());
      this.$nextTick(() => this.initBodyEditor());
    },

    // Editor del mensaje (Quill): arranca una sola vez y vuelca su HTML a form.body.
    initBodyEditor() {
      if (quill || !this.$refs.bodyEditor) return;
      if (typeof Quill === 'undefined') {
        showToast('No se pudo cargar el editor del mensaje.');
        return;
      }
      quill = new Quill(this.$refs.bodyEditor, {
        theme: 'snow',
        placeholder: 'Escribe el mensaje…',
        modules: {
          toolbar: {
            container: [
              ['bold', 'italic', 'underline', 'strike'],
              [{ header: [false, 1, 2, 3] }],
              [{ list: 'ordered' }, { list: 'bullet' }],
              ['blockquote', 'link', 'image'],
              ['clean'],
            ],
            handlers: { image: () => this.pickBodyImage() },
          },
        },
        formats: ['bold', 'italic', 'underline', 'strike', 'header', 'list', 'blockquote', 'link', 'image'],
      });
      quill.on('text-change', () => {
        if (syncing) return;
        this.form.body = quill.getSemanticHTML();
      });
      this.setBody(this.form.body);
    },

    // Vuelca el HTML guardado dentro del editor sin que el editor lo devuelva a form.body.
    setBody(html) {
      if (!quill) return;
      syncing = true;
      if (html) quill.clipboard.dangerouslyPasteHTML(html);
      else quill.setText('');
      syncing = false;
    },

    // «Imagen» de la barra: abre un diálogo de archivo y la incrusta en base64.
    pickBodyImage() {
      if (!imageInput) {
        imageInput = document.createElement('input');
        imageInput.type = 'file';
        imageInput.accept = 'image/*';
        imageInput.style.display = 'none';
        imageInput.addEventListener('change', () => this.insertBodyImage(imageInput.files[0]));
        document.body.appendChild(imageInput);
      }
      imageInput.value = '';
      imageInput.click();
    },

    insertBodyImage(file) {
      if (!file || !quill) return;
      if (file.size > EMAIL_BODY_IMAGE_BYTES) {
        showToast(`La imagen pesa ${Math.round(file.size / 1024)} KB; el máximo es 300 KB.`);
        return;
      }
      const reader = new FileReader();
      reader.onload = () => {
        const range = quill.getSelection(true) || { index: quill.getLength() };
        quill.insertEmbed(range.index, 'image', reader.result, 'user');
        quill.setSelection(range.index + 1, 0, 'silent');
      };
      reader.readAsDataURL(file);
    },

    get url() {
      return apiUrl(`/api/dashboard/${window.DASHBOARD_ID}/email-campaign/`);
    },

    _message(e) {
      return e.message === 'Failed to fetch' ? 'No se pudo conectar con el servidor.' : e.message;
    },

    // La respuesta del servidor (configuración + totales) al formulario.
    apply(data) {
      this.form = {
        enabled: data.enabled, subject: data.subject, body: data.body, daily_limit: data.daily_limit,
        batch_size: data.batch_size,
        frequency: data.frequency, send_time: data.send_time, weekdays: data.weekdays || [],
        senders: (data.senders || []).map(s => ({ email: s.email, password: '', has_password: s.has_password })),
      };
      this.report = data.report;
      this.timeZone = data.time_zone || '';
      this.cronMinutes = data.cron_minutes || 5;
      this.setBody(this.form.body);
    },

    async load() {
      this.loading = true;
      this.error = '';
      this.uploadResult = null;
      this.confirmClear = false;
      this.testMessage = '';
      try {
        const { r, data } = await fetchJsonSafe(this.url);
        if (!r.ok || !data) throw new Error((data && data.error) || `Error ${r.status}`);
        this.apply(data);
      } catch (e) {
        this.error = this._message(e);
      } finally {
        this.loading = false;
      }
    },

    addSender() {
      this.form.senders.push({ email: '', password: '', has_password: false });
    },

    removeSender(index) {
      this.form.senders.splice(index, 1);
    },

    toggleDay(day) {
      const days = this.form.weekdays;
      this.form.weekdays = days.includes(day) ? days.filter(d => d !== day) : [...days, day].sort();
    },

    get nextRunLabel() {
      if (!this.report.next_run) return '';
      return new Date(this.report.next_run).toLocaleString('es', { weekday: 'long', day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit' });
    },

    // La última franja de ticks antes de medianoche (p. ej. las 23:55 con 5 min): una hora dentro de
    // ella puede no alcanzar a correr con el cron del servidor. null si el intervalo no da ticks por minuto.
    get riskyFromMinute() {
      const n = this.cronMinutes;
      if (!(n >= 1 && n <= 59)) return null;
      return 23 * 60 + Math.floor(59 / n) * n;
    },

    get riskyFromLabel() {
      const from = this.riskyFromMinute;
      if (from === null) return '';
      return `${String(Math.floor(from / 60)).padStart(2, '0')}:${String(from % 60).padStart(2, '0')}`;
    },

    get riskySendTime() {
      const from = this.riskyFromMinute;
      if (from === null) return false;
      const match = /^(\d{1,2}):(\d{2})/.exec(this.form.send_time || '');
      if (!match) return false;
      return Number(match[1]) * 60 + Number(match[2]) > from;
    },

    // Sube el CSV elegido: sus correos se agregan a la lista (los que ya estaban no cambian).
    async upload(event) {
      const file = event.target.files[0];
      event.target.value = '';
      if (!file) return;
      this.uploading = true;
      this.error = '';
      this.uploadResult = null;
      const body = new FormData();
      body.append('file', file);
      try {
        const { r, data } = await fetchJsonSafe(`${this.url}recipients/`, { method: 'POST', body });
        if (!r.ok || !data) throw new Error((data && data.error) || `Error ${r.status}`);
        this.report = data.report;
        this.uploadResult = data;
      } catch (e) {
        this.error = this._message(e);
      } finally {
        this.uploading = false;
      }
    },

    async clearRecipients() {
      this.confirmClear = false;
      this.error = '';
      try {
        const { r, data } = await fetchJsonSafe(`${this.url}recipients/`, { method: 'DELETE' });
        if (!r.ok || !data) throw new Error((data && data.error) || `Error ${r.status}`);
        this.uploadResult = null;
        this.report = data.report;
        this.form.enabled = data.enabled;
      } catch (e) {
        this.error = this._message(e);
      }
    },

    // Guarda la configuración → true si se guardó.
    async save({ silent = false } = {}) {
      this.saving = true;
      this.error = '';
      const body = {
        ...this.form,
        daily_limit: Number(this.form.daily_limit),
        batch_size: Number(this.form.batch_size),
        senders: this.form.senders.filter(s => s.email.trim() || s.password)
          .map(s => ({ email: s.email.trim(), password: s.password })),
      };
      try {
        const { r, data } = await fetchJsonSafe(this.url, {
          method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
        });
        if (!r.ok || !data) throw new Error((data && data.error) || `Error ${r.status}`);
        this.apply(data);
        if (!silent) showToast(data.enabled ? 'Envío de correos guardado y activado.' : 'Envío de correos guardado.');
        return true;
      } catch (e) {
        this.error = this._message(e);
        return false;
      } finally {
        this.saving = false;
      }
    },

    // «Enviar prueba»: guarda lo escrito y manda el mensaje a una dirección con el primer remitente.
    async sendTest() {
      this.testMessage = '';
      this.testFailed = false;
      if (!(await this.save({ silent: true }))) return;
      this.testing = true;
      try {
        const { r, data } = await fetchJsonSafe(`${this.url}test/`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ to: this.testTo.trim() }),
        });
        if (!r.ok || !data) throw new Error((data && data.error) || `Error ${r.status}`);
        this.testMessage = `Prueba enviada desde ${data.sender}.`;
      } catch (e) {
        this.testFailed = true;
        this.testMessage = this._message(e);
      } finally {
        this.testing = false;
      }
    },
  };
}
