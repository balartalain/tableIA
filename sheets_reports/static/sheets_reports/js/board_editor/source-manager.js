// Gestor de las fuentes de datos del tablero (bottom sheet «Fuentes de datos»): la tabla de
// fuentes, y el selector (source-picker.js) para agregar una o editar sus columnas.
// Eliminar no chequea nada: los widgets que la usaban muestran el error al calcularse.
function sourceManager() {
  return {
    view: 'list',
    sources: [],
    loading: false,
    error: '',

    init() {
      window.addEventListener('sources:open', () => this.open());
      window.addEventListener('sources:back', () => { this.view = 'list'; });
      window.addEventListener('sources:changed', () => this.afterChange());
    },

    open() {
      this.view = 'list';
      this.load();
    },

    async load() {
      this.loading = true;
      this.error = '';
      try {
        const { r, data } = await fetchJsonSafe(apiUrl(`/api/dashboard/${window.DASHBOARD_ID}/sources/`));
        if (!r.ok || !data) throw new Error((data && data.error) || `Error ${r.status}`);
        this.sources = data.sources || [];
      } catch (e) {
        this.error = e.message === 'Failed to fetch' ? 'No se pudo conectar con el servidor.' : e.message;
      } finally {
        this.loading = false;
      }
    },

    columnsLabel(s) {
      return s.columns_total == null ? 'Todas' : `${s.columns_included} de ${s.columns_total}`;
    },

    add() {
      this.view = 'picker';
      window.dispatchEvent(new CustomEvent('source-picker:start', { detail: { mode: 'add' } }));
    },

    edit(source) {
      this.view = 'picker';
      window.dispatchEvent(new CustomEvent('source-picker:start', { detail: { mode: 'edit', source } }));
    },

    async remove(source) {
      const used = source.widgets ? ` ${source.widgets} widget(s) la usan y mostrarán un error.` : '';
      if (!confirm(`¿Eliminar la fuente «${source.label}»?${used}`)) return;
      const r = await fetch(apiUrl(`/api/sources/${source.id}/`), { method: 'DELETE' }).catch(() => null);
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
      store.refreshData();
      if (store.editingId != null) store.loadSchema(store.drawerDraft.source);
    },
  };
}
