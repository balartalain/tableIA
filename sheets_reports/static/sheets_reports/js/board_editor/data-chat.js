// Chatbot analítico sobre la fuente de datos del tablero (Gemini + Code Execution).
const DATA_CHAT_FETCH_TIMEOUT_MS = 120000;
async function fetchJsonSafeDataChat(url, options = {}, timeoutMs = DATA_CHAT_FETCH_TIMEOUT_MS) {
  if (typeof window.fetchJsonSafe === 'function') {
    return window.fetchJsonSafe(url, options, timeoutMs);
  }
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

function dataChat() {
  return {
    open: false,
    maximized: false,
    minimized: false,
    prompt: '',
    loading: false,
    error: '',
    messages: [],
    selectedSourceId: null,

    init() {
      // Si hay un contenedor de mensajes, hacer scroll al fondo al añadir mensajes
      this.$watch('messages', () => {
        this.$nextTick(() => {
          this.scrollToBottom();
        });
      });
    },

    get currentSourceLabel() {
      const store = Alpine.store('dashboard');
      if (!store || !store.sources || !store.sources.length) return 'Fuente actual';
      if (this.selectedSourceId) {
        const found = store.sources.find(s => s && s.id === this.selectedSourceId);
        if (found) return typeof found === 'object' ? (found.label || found.name || 'Fuente actual') : String(found);
      }
      const first = store.sources[0];
      if (!first) return 'Fuente actual';
      return typeof first === 'object' ? (first.label || first.name || 'Fuente actual') : String(first);
    },

    scrollToBottom() {
      const container = this.$refs.messagesContainer;
      if (container) {
        container.scrollTop = container.scrollHeight;
      }
    },

    clearHistory() {
      this.messages = [];
      this.error = '';
    },

    async send(customText = null) {
      const text = (customText || this.prompt || '').trim();
      if (!text || this.loading || !window.DASHBOARD_ID) return;

      this.messages.push({
        role: 'user',
        text,
        images: [],
        code: [],
        timestamp: new Date().toLocaleTimeString('es', { hour: '2-digit', minute: '2-digit' }),
      });

      this.prompt = '';
      this.loading = true;
      this.error = '';

      try {
        const body = { prompt: text };
        if (this.selectedSourceId) {
          body.source_id = this.selectedSourceId;
        }

        const { r, data } = await fetchJsonSafeDataChat(apiUrl(`/api/dashboard/${window.DASHBOARD_ID}/data-chat/`), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });

        if (!r.ok || !data || data.error) {
          throw new Error((data && data.error) || 'Error al consultar con el asistente.');
        }

        this.messages.push({
          role: 'assistant',
          text: data.text || '',
          images: data.images || [],
          code: data.code || [],
          timestamp: new Date().toLocaleTimeString('es', { hour: '2-digit', minute: '2-digit' }),
        });
      } catch (e) {
        this.error = e.message || 'Error de conexión con el asistente.';
      } finally {
        this.loading = false;
      }
    },

    renderContent(text) {
      if (!text) return '';
      // Eliminar sintaxis de imágenes markdown e HTML para evitar renderizado doble (ya se muestra en m.images)
      const cleanText = text.replace(/!\[.*?\]\([^)]*\)/g, '').replace(/<img[^>]*>/gi, '').trim();
      if (window.marked && typeof window.marked.parse === 'function') {
        try {
          return window.marked.parse(cleanText);
        } catch (e) {
          // fallback si falla el parser
        }
      }
      // Reemplazo básico de saltos de línea y negritas si marked no está disponible
      return cleanText
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
        .replace(/\n/g, '<br>');
    },
  };
}
