// Centro de ayuda del editor (help/_panel.html): panel a la derecha, en el lugar del panel de
// edición, con los tópicos de sheets_reports/help.py. Se navega con enlaces `#help:<slug>` (o
// `#help:<slug>/<id>`, a una parte del tópico) y con `$store.help.open('<slug>')`; tiene
// historial (Atrás), buscador y un ancho que el usuario ajusta y se recuerda en este navegador.
const HELP_WIDTH_STORAGE_KEY = 'dataviz:help-width';
const HELP_WIDTH = { default: 480, min: 320, max: 960, step: 40 };
// Lo que el panel deja siempre a la vista del lienzo al ensancharse.
const HELP_MIN_CANVAS = 360;

function storedHelpWidth() {
  try {
    const width = Number(localStorage.getItem(HELP_WIDTH_STORAGE_KEY));
    return width >= HELP_WIDTH.min && width <= HELP_WIDTH.max ? width : HELP_WIDTH.default;
  } catch (e) {
    return HELP_WIDTH.default;
  }
}

// Para buscar sin importar mayúsculas ni tildes.
const helpNormalize = (text) => String(text || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();

document.addEventListener('alpine:init', () => {
  const sections = JSON.parse(document.getElementById('help-index')?.textContent || '[]');
  const topics = sections.flatMap(s => s.topics);
  const texts = {};

  Alpine.store('help', {
    isOpen: false,
    // El tópico a la vista; null es el índice.
    topic: null,
    history: [],
    query: '',
    width: storedHelpWidth(),
    resizing: false,
    // El tópico con su video desplegado (uno a la vez).
    videoOpen: null,
    sections,
    topics,
    defaultWidth: HELP_WIDTH.default,

    get current() { return topics.find(t => t.slug === this.topic) || null; },
    get position() { return topics.findIndex(t => t.slug === this.topic); },
    get prev() { return this.position > 0 ? topics[this.position - 1] : null; },
    get next() { return this.position >= 0 && this.position < topics.length - 1 ? topics[this.position + 1] : null; },
    get searching() { return this.query.trim().length > 0; },

    // Abre la ayuda en un tópico (o en el índice) y, si se pide, en una parte de él (`id`).
    open(slug = null, anchor = null) {
      if (slug && !topics.some(t => t.slug === slug)) slug = null;
      if (slug !== this.topic) this._stopVideo();
      if (this.isOpen && slug !== this.topic) this.history.push(this.topic);
      if (!this.isOpen) this.history = [];
      this.topic = slug;
      this.query = '';
      this.isOpen = true;
      this._scrollTo(anchor);
    },
    showIndex() { this.open(null); },
    back() {
      if (!this.history.length) return;
      this._stopVideo();
      this.topic = this.history.pop();
      this.query = '';
      this._scrollTo(null);
    },
    close() {
      this._stopVideo();
      this.isOpen = false;
    },

    // «Ver video» del tópico: muestra el reproductor y lo arranca; otra vez, lo pausa y lo oculta.
    toggleVideo(slug) {
      if (this.videoOpen === slug) return this._stopVideo();
      this._stopVideo();
      this.videoOpen = slug;
      Alpine.nextTick(() => {
        const video = document.querySelector(`#help-topic-${slug} video`);
        if (video) video.play().catch(() => {});
      });
    },
    _stopVideo() {
      document.querySelectorAll('#help-panel video').forEach(v => v.pause());
      this.videoOpen = null;
    },

    // El buscador: el título, las palabras clave y el texto del tópico.
    matches(topic) {
      const words = helpNormalize(this.query).split(/\s+/).filter(Boolean);
      if (!words.length) return true;
      if (!(topic.slug in texts)) {
        const article = document.getElementById(`help-topic-${topic.slug}`);
        texts[topic.slug] = helpNormalize(`${topic.title} ${topic.keywords} ${article ? article.textContent : ''}`);
      }
      return words.every(w => texts[topic.slug].includes(w));
    },
    get results() { return topics.filter(t => this.matches(t)); },

    _scrollTo(anchor) {
      Alpine.nextTick(() => {
        const scroller = document.getElementById('help-scroll');
        if (!scroller) return;
        const target = anchor && document.getElementById(anchor);
        if (target) target.scrollIntoView({ block: 'start' });
        else scroller.scrollTop = 0;
      });
    },

    // ------------------------------------------------------------- ancho
    setWidth(width) {
      const body = document.getElementById('board-body');
      const max = Math.min(HELP_WIDTH.max, body ? body.clientWidth - HELP_MIN_CANVAS : HELP_WIDTH.max);
      this.width = Math.round(Math.max(HELP_WIDTH.min, Math.min(Math.max(max, HELP_WIDTH.min), width)));
      try { localStorage.setItem(HELP_WIDTH_STORAGE_KEY, String(this.width)); } catch (e) {}
    },
    // Arrastrar el borde izquierdo: hacia la izquierda ensancha.
    startResize(event) {
      const startX = event.clientX;
      const startWidth = this.width;
      const move = (e) => this.setWidth(startWidth + startX - e.clientX);
      const stop = () => {
        window.removeEventListener('pointermove', move);
        document.body.classList.remove('select-none', 'cursor-col-resize');
        this.resizing = false;
        // Los gráficos del lienzo vuelven a medir su ancho.
        window.dispatchEvent(new Event('resize'));
      };
      event.preventDefault();
      this.resizing = true;
      document.body.classList.add('select-none', 'cursor-col-resize');
      window.addEventListener('pointermove', move);
      window.addEventListener('pointerup', stop, { once: true });
    },
    // Con el teclado: flechas para ajustar e Inicio para volver al ancho original.
    onResizeKey(event) {
      const delta = { ArrowLeft: HELP_WIDTH.step, ArrowRight: -HELP_WIDTH.step }[event.key];
      if (delta) this.setWidth(this.width + delta);
      else if (event.key === 'Home') this.setWidth(HELP_WIDTH.default);
      else return;
      event.preventDefault();
      window.dispatchEvent(new Event('resize'));
    },
  });

  // Los enlaces del contenido: `#help:<slug>` o `#help:<slug>/<id>`, en cualquier parte de la página.
  document.addEventListener('click', (e) => {
    const link = e.target.closest('a[href^="#help:"]');
    if (!link) return;
    e.preventDefault();
    const [slug, anchor] = link.getAttribute('href').slice('#help:'.length).split('/');
    Alpine.store('help').open(slug || null, anchor || null);
  });
});
