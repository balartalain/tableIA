// Gestor de las fuentes de datos del tablero (bottom sheet «Fuentes de datos»): la tabla de
// fuentes, y el selector (source-picker.js) para agregar una o editarla. Cada fila tiene su estado
// (si la cuenta de servicio todavía llega a la pestaña) y sus acciones: actualizar (relee la hoja
// de Google: el tablero no lo hace por su cuenta), cambiar hoja, abrir en Google Sheets, editar
// columnas y eliminar (avisa qué widgets se quedan sin datos).
const SOURCE_STATUS = {
  no_access: { label: 'Sin acceso',
    title: 'La cuenta de servicio ya no puede abrir la hoja (se dejó de compartir o se borró). El tablero muestra los últimos datos leídos.' },
  tab_missing: { label: 'Pestaña no encontrada',
    title: 'La pestaña se borró o se reemplazó: usa «Cambiar fuente». El tablero muestra los últimos datos leídos.' },
};

function sourceManager() {
  return {
    view: 'list',
    sources: [],
    loading: false,
    error: '',
    // Fuente por eliminar, con los widgets que la usan: {source, impact}.
    pendingDelete: null,
    // Por fuente: su estado ({id: "ok" | "no_access" | "tab_missing" | null}), si se está
    // actualizando y el error del último «Actualizar».
    statuses: {},
    refreshing: {},
    refreshErrors: {},

    init() {
      window.addEventListener('sources:open', () => this.open());
      window.addEventListener('sources:back', () => { this.view = 'list'; });
      window.addEventListener('sources:changed', () => this.afterChange());
    },

    open() {
      this.view = 'list';
      this.pendingDelete = null;
      this.load();
    },

    async load() {
      this.loading = true;
      this.error = '';
      try {
        const { r, data } = await fetchJsonSafe(apiUrl(`/api/dashboard/${window.DASHBOARD_ID}/sources/`));
        if (!r.ok || !data) throw new Error((data && data.error) || `Error ${r.status}`);
        this.sources = data.sources || [];
        this.sources.forEach(s => this.checkStatus(s));
      } catch (e) {
        this.error = e.message === 'Failed to fetch' ? 'No se pudo conectar con el servidor.' : e.message;
      } finally {
        this.loading = false;
      }
    },

    columnsLabel(s) {
      return s.columns_total == null ? 'Todas' : `${s.columns_included} de ${s.columns_total} incluidas`;
    },

    refreshedLabel(s) {
      if (!s.refreshed_at) return 'Sin leer todavía';
      const ago = timeAgo(s.refreshed_at);
      return ago[0].toUpperCase() + ago.slice(1);
    },

    statusInfo(s) {
      return SOURCE_STATUS[this.statuses[s.id]] || null;
    },

    // Si la cuenta de servicio todavía llega a la pestaña (sin bloquear la tabla).
    async checkStatus(s) {
      const { r, data } = await fetchJsonSafe(apiUrl(`/api/sources/${s.id}/status/`));
      if (r.ok && data) this.statuses = { ...this.statuses, [s.id]: data.status };
    },

    // «Actualizar»: relee la hoja de Google y recalcula el tablero.
    async refresh(s) {
      if (this.refreshing[s.id]) return;
      this.refreshing = { ...this.refreshing, [s.id]: true };
      this.refreshErrors = { ...this.refreshErrors, [s.id]: '' };
      const { r, data } = await fetchJsonSafe(apiUrl(`/api/sources/${s.id}/refresh/`), { method: 'POST' });
      if (data && 'status' in data) this.statuses = { ...this.statuses, [s.id]: data.status };
      this.refreshing = { ...this.refreshing, [s.id]: false };
      if (!r.ok || !data || data.error) {
        this.refreshErrors = { ...this.refreshErrors, [s.id]: (data && data.error) || 'No se pudo actualizar.' };
        return;
      }
      await this.afterChange();
    },

    sheetUrl(s) {
      return `https://docs.google.com/spreadsheets/d/${encodeURIComponent(s.sheet_id)}/edit#gid=${encodeURIComponent(s.gid)}`;
    },

    add() {
      this.view = 'picker';
      window.dispatchEvent(new CustomEvent('source-picker:start', { detail: { mode: 'add' } }));
    },

    changeSheet(source) {
      this.view = 'picker';
      window.dispatchEvent(new CustomEvent('source-picker:start', { detail: { mode: 'edit', source, changeSheet: true } }));
    },

    edit(source) {
      this.view = 'picker';
      window.dispatchEvent(new CustomEvent('source-picker:start', { detail: { mode: 'edit', source } }));
    },

    async remove(source) {
      const { r, data } = await fetchJsonSafe(apiUrl(`/api/sources/${source.id}/?dry_run=1`), { method: 'DELETE' });
      if (!r.ok || !data) {
        showToast('No se pudo eliminar la fuente.');
        return;
      }
      this.pendingDelete = { source, impact: data.impact || [] };
    },

    async confirmRemove() {
      const { source } = this.pendingDelete || {};
      if (!source) return;
      const r = await fetch(apiUrl(`/api/sources/${source.id}/`), { method: 'DELETE' }).catch(() => null);
      this.pendingDelete = null;
      if (!r || !r.ok) {
        showToast('No se pudo eliminar la fuente.');
        return;
      }
      this.afterChange();
    },

    // Otra lista de fuentes y otras columnas: el panel de los widgets y el lienzo se recalculan.
    async afterChange() {
      this.view = 'list';
      await this.load();
      const store = Alpine.store('dashboard');
      store.resetSources(this.sources.map(s => ({ id: s.id, label: s.label })));
      await store.refreshData();
      // El panel abierto vuelve a leer su widget: sus columnas pueden haberse renombrado.
      if (store.editingId != null) store.openDrawer(store.editingId);
    },
  };
}
