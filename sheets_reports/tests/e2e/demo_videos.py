"""
Videos de demostración de la app (Playwright + Chromium): un `.webm` por flujo, con su portada
(`.png`, la última imagen), en `static/sheets_reports/help/videos/`. El centro de ayuda los
muestra en su tópico («Ver video»; `HELP_VIDEOS` en sheets_reports/help.py), así que regrabarlos
actualiza la ayuda.

No es parte de la suite (el nombre no empieza con `test_`); se corre a mano:

    DEMO=1 venv/bin/python manage.py test sheets_reports.tests.e2e.demo_videos
    DEMO=1 venv/bin/python manage.py test sheets_reports.tests.e2e.demo_videos.DemoVideos.test_04_agregar_widgets

Todo lo externo es simulado para que cada grabación salga igual: la hoja de Google (datos y
pestañas), el selector de Google Drive y las respuestas de la IA. `HEADED=1` abre la ventana
para mirarlos mientras se graban.
"""
import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import caches
from django.test import override_settings

from sheets_reports.models import Widget
from sheets_reports.tests.e2e.test_panel import PanelTests
from sheets_reports.tests.fixtures import agg, fields, make_board

DEMO = os.environ.get("DEMO") == "1"
OUT_DIR = Path(settings.BASE_DIR) / "sheets_reports" / "static" / "sheets_reports" / "help" / "videos"
SIZE = {"width": 1280, "height": 800}
SHEET_ID = "demo-ventas"
SHEET_NAME = "Ventas 2026"


# ------------------------------------------------------------------ datos de la demo
def demo_df() -> pd.DataFrame:
    """96 ventas: 4 regiones × 4 categorías × 6 meses, en dos canales."""
    regions = ["Norte", "Sur", "Este", "Oeste"]
    categories = ["Hogar", "Electrónica", "Ropa", "Deportes"]
    months = ["2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06"]
    rows = []
    for m, month in enumerate(months):
        for r, region in enumerate(regions):
            for c, category in enumerate(categories):
                units = 20 + (r * 7 + c * 11 + m * 5) % 37
                price = [45, 320, 38, 60][c]
                rows.append({
                    "region": region, "categoria": category,
                    "canal": "Online" if (r + c + m) % 3 else "Tienda",
                    "mes": month, "anio": 2026,
                    "ventas": float(units * price), "unidades": units,
                })
    df = pd.DataFrame(rows)
    df.insert(0, "ID", range(1, len(df) + 1))
    return df


COLUMN_LABELS = {"region": "Región", "categoria": "Categoría", "canal": "Canal de venta", "mes": "Mes",
                 "anio": "Año", "ventas": "Ventas (USD)", "unidades": "Unidades vendidas"}
COLUMNS = [{"name": n, "label": COLUMN_LABELS[n], "include": True,
            "type": "number" if n in ("anio", "ventas", "unidades") else "text",
            **({"format": "currency"} if n == "ventas" else {})}
           for n in COLUMN_LABELS]

# El tablero usa las columnas por su nombre a mostrar (`label`), no por el de la hoja.
L = COLUMN_LABELS
SALES = agg("total_ventas", "sum", L["ventas"])

# Lo que «la IA» arma para cada widget del tablero generado.
AI_PLAN = [
    {"widget_type": "kpi", "title": "Ventas totales", "description": "Suma de las ventas del semestre.",
     "request": "suma de ventas"},
    {"widget_type": "bar", "title": "Ventas por región", "description": "Comparar regiones.",
     "request": "ventas por región"},
    {"widget_type": "line", "title": "Evolución mensual", "description": "Tendencia de las ventas mes a mes.",
     "request": "ventas por mes"},
    {"widget_type": "dynamic_table", "title": "Categoría por canal", "description": "Ventas de cada categoría en cada canal.",
     "request": "ventas por categoría y canal"},
]
AI_FIELDS = {
    "kpi": fields(dimensions=[], metrics=[SALES]),
    "bar": fields(dimensions=[L["region"]], metrics=[SALES]),
    "line": fields(dimensions=[L["mes"]], metrics=[SALES]),
    "dynamic_table": fields(dimensions=[L["categoria"]], pivots=[L["canal"]], metrics=[SALES]),
}

ASSISTANT_ANSWER = {
    "widget_type": "bar", "title": "Unidades por canal",
    "fields": fields(dimensions=[L["canal"]], metrics=[agg("total_unidades", "sum", L["unidades"])]),
    "style": {"horizontal": True}, "calculated_fields": [], "column_scales": [],
}

INSIGHT = """### Lo más importante
- **Norte** es la región que más vende (USD 112.983) y **Oeste** la que menos (USD 97.913).
- La diferencia entre regiones es chica: menos de un 15 % entre la primera y la última.
- **Electrónica** explica cerca del 70 % de las ventas en todas las regiones.

### Sugerencia
Revisar **Ropa** en la región Norte: es la combinación con menor venta (USD 8.208)."""

CHAT_ANSWERS = {
    "resumen": {"text": "El conjunto tiene **96 ventas** de enero a junio de 2026 en 4 regiones y 4 categorías.\n\n"
                        "- Ventas totales: **USD 425.677**\n- Venta promedio: **USD 4.434**\n"
                        "- Canal con más ventas: **Online** (67 %)",
                "images": [], "code": ["df['ventas'].sum()", "df.groupby('canal')['ventas'].sum() / df['ventas'].sum()"]},
    "otro": {"text": "**Norte**: en junio vendió **USD 23.545**, un **51 % más** que en enero. "
                     "Es la región que más creció en el semestre; las otras tres bajaron entre un 10 % y un 19 %.",
             "images": [], "code": ["g = df.groupby(['region', 'mes'])['ventas'].sum().unstack()",
                                    "(g['2026-06'] / g['2026-01'] - 1).sort_values()"]},
}


def _chat(source, prompt):
    return CHAT_ANSWERS["resumen" if "resumen" in prompt.lower() else "otro"]


def _build(items, ctx):
    by_title = {i["title"]: i for i in AI_PLAN}
    return [{"widget_type": i["widget_type"], "title": i["title"], "style": {},
             "fields": AI_FIELDS[by_title.get(i["title"], i)["widget_type"]]} for i in items]


# ------------------------------------------------------------------ overlay: cursor y subtítulos
# Playwright no dibuja el mouse: un círculo sigue a `mousemove` y late en cada clic. Abajo, una
# franja con el paso que se está mostrando (window.__caption).
OVERLAY = """
(() => {
  const install = () => {
    if (document.getElementById('__demo_cursor')) return;
    const css = document.createElement('style');
    css.textContent = `
      #__demo_cursor { position: fixed; z-index: 2147483647; pointer-events: none; width: 22px; height: 22px;
        margin: -11px 0 0 -11px; border-radius: 50%; background: rgba(31,138,95,.35);
        border: 2px solid rgba(15,81,56,.9); transition: transform .12s ease; left: -40px; top: -40px; }
      #__demo_cursor.down { transform: scale(.65); background: rgba(31,138,95,.7); }
      #__demo_caption { position: fixed; z-index: 2147483646; pointer-events: none; left: 50%; bottom: 28px;
        transform: translateX(-50%); max-width: 80%; padding: 10px 18px; border-radius: 8px;
        background: rgba(20,23,31,.86); color: #fff; font: 600 17px/1.35 ui-sans-serif, system-ui, sans-serif;
        box-shadow: 0 6px 24px rgba(0,0,0,.25); opacity: 0; transition: opacity .25s ease; text-align: center; }
      #__demo_caption.on { opacity: 1; }`;
    document.head.appendChild(css);
    const cursor = Object.assign(document.createElement('div'), { id: '__demo_cursor' });
    const caption = Object.assign(document.createElement('div'), { id: '__demo_caption' });
    document.body.append(cursor, caption);
    document.addEventListener('mousemove', e => { cursor.style.left = e.clientX + 'px'; cursor.style.top = e.clientY + 'px'; }, true);
    document.addEventListener('mousedown', () => cursor.classList.add('down'), true);
    document.addEventListener('mouseup', () => cursor.classList.remove('down'), true);
    window.__caption = (text) => { caption.textContent = text || ''; caption.classList.toggle('on', !!text); };
  };
  if (document.body) install(); else document.addEventListener('DOMContentLoaded', install);
})();
"""

# El selector de Google Drive, simulado: «elige» la hoja de la demo al abrirse.
FAKE_PICKER = """
window.google = window.google || {};
window.google.picker = {
  ViewId: { SPREADSHEETS: 'spreadsheets' },
  Action: { PICKED: 'picked' },
  PickerBuilder: class {
    setOAuthToken() { return this; } setDeveloperKey() { return this; } setAppId() { return this; }
    setOrigin() { return this; } addView() { return this; }
    setCallback(cb) { this.cb = cb; return this; }
    build() {
      const cb = this.cb;
      return { setVisible: () => setTimeout(() => cb({ action: 'picked',
        docs: [{ id: '%s', name: '%s' }] }), 900) };
    }
  },
};
""" % (SHEET_ID, SHEET_NAME)


@unittest.skipUnless(DEMO, "Videos de demostración: correr con DEMO=1")
@override_settings(GOOGLE_OAUTH_CLIENT_ID="demo", GOOGLE_OAUTH_CLIENT_SECRET="demo")
class DemoVideos(PanelTests):
    # PanelTests se salta sin E2E=1; esta clase solo depende de DEMO=1.
    __unittest_skip__ = not DEMO

    # Los casos de PanelTests no se repiten aquí: solo su servidor, navegador y login.
    for _name in [n for n in dir(PanelTests) if n.startswith("test_")]:
        locals()[_name] = None
    del _name

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        base = "sheets_reports.services"
        cls._patches = [
            mock.patch(f"{base}.sheets.get_sheet_dataframe", side_effect=lambda *a, **k: demo_df()),
            mock.patch(f"{base}.google_drive.list_tabs", return_value={
                "name": SHEET_NAME, "tabs": [{"gid": "0", "title": "Ventas"}, {"gid": "1", "title": "Ventas 2025"}]}),
            mock.patch(f"{base}.google_drive.tab_status", return_value="ok"),
            # La cuenta de Google ya conectada: el botón dice «Elegir en mi Drive».
            mock.patch("google_auth.services.connection_state",
                       return_value={"connected": True, "email": "demo@reportia.app"}),
            mock.patch(f"{base}.ai_suggestions.widget_suggestions",
                       return_value=["Unidades por canal, de mayor a menor", "Ventas por región y categoría"]),
            mock.patch(f"{base}.ai_board.propose_board", return_value={"items": AI_PLAN, "note": ""}),
            mock.patch(f"{base}.ai_board.build_board_batch", side_effect=_build),
            mock.patch(f"{base}.ai_spec.generate_widget_form", return_value=ASSISTANT_ANSWER),
            mock.patch(f"{base}.ai_spec.generate_widget_insight", return_value=INSIGHT),
            mock.patch(f"{base}.ai_data_chat.ask_data_chat", side_effect=_chat),
            mock.patch(f"{base}.ai_column_labels.suggest_column_labels", return_value=COLUMN_LABELS),
        ]
        for p in cls._patches:
            p.start()
        OUT_DIR.mkdir(parents=True, exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        for p in reversed(cls._patches):
            p.stop()
        super().tearDownClass()

    def setUp(self):
        caches["sheets"].clear()
        self.user = get_user_model().objects.create_superuser("demo", "demo@reportia.app", "x")
        self.client.force_login(self.user)
        self._videos = tempfile.mkdtemp()
        self.context = self.browser.new_context(viewport=SIZE, record_video_dir=self._videos,
                                                record_video_size=SIZE,
                                                permissions=["clipboard-read", "clipboard-write"])
        self.context.add_cookies([{"name": "sessionid", "url": self.live_server_url,
                                   "value": self.client.cookies["sessionid"].value}])
        self.context.add_init_script(OVERLAY)
        self.context.add_init_script(FAKE_PICKER)
        self.context.route("**/auth/google/status/**", lambda route: route.fulfill(
            json={"configured": True, "connected": True, "email": "demo@reportia.app", "access_token": "demo"}))
        self.page = self.context.new_page()
        self.errors = []
        self.page.on("pageerror", lambda e: self.errors.append(str(e)))

    def tearDown(self):
        video = self.page.video
        self.pause(1500)
        OUT_DIR.joinpath(f"{self.name}.png").write_bytes(self.page.screenshot())
        self.context.close()
        shutil.move(video.path(), OUT_DIR / f"{self.name}.webm")
        shutil.rmtree(self._videos, ignore_errors=True)
        self.assertEqual(self.errors, [])

    @property
    def name(self):
        return self._testMethodName.removeprefix("test_")

    # ------------------------------------------------------------------ helpers de ritmo
    def pause(self, ms=700):
        self.page.wait_for_timeout(ms)

    def caption(self, text, ms=1200):
        self.page.evaluate("t => window.__caption && window.__caption(t)", text)
        self.pause(ms)

    def point(self, locator):
        """Lleva el cursor despacio hasta el centro del elemento."""
        locator.wait_for()
        locator.scroll_into_view_if_needed()
        box = locator.bounding_box()
        self.page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, steps=25)
        self.pause(250)

    def click(self, locator):
        self.point(locator)
        locator.click()
        self.pause()

    def type(self, locator, text):
        self.click(locator)
        locator.press_sequentially(text, delay=55)
        self.pause(400)

    def choose(self, select, value):
        """Los selects son VirtualSelect (x-vselect): el cursor va al control visible y el valor se
        elige en el select nativo, que es la fuente de verdad."""
        visible = select.locator("xpath=following-sibling::*[contains(@class,'vselect')][1]")
        self.point(visible if visible.count() else select)
        select.select_option(value)
        self.pause(600)

    def drag(self, source, target):
        """Arrastre con el mouse (SortableJS en modo fallback: necesita movimientos reales)."""
        self.point(source)
        a, b = source.bounding_box(), target.bounding_box()
        self.page.mouse.down()
        self.page.mouse.move(a["x"] + a["width"] / 2 + 15, a["y"] + a["height"] / 2 + 10, steps=5)
        self.page.mouse.move(b["x"] + b["width"] - 40, b["y"] + b["height"] / 2, steps=35)
        self.pause(300)
        self.page.mouse.up()
        self.pause(900)

    def close_drawer(self):
        drawer = self.page.locator("#edit-drawer")
        if drawer.is_visible():
            self.click(drawer.get_by_role("button", name="Cerrar", exact=True))
            self.pause(600)

    def board(self, widgets=True):
        """El tablero de la demo con la hoja ya conectada (y, si se pide, cuatro widgets)."""
        dashboard, source = make_board(self.user, nombre="Ventas 2026", sheet_id=SHEET_ID, columns=COLUMNS)
        if widgets:
            for order, item in enumerate(AI_PLAN):
                Widget.objects.create(
                    dashboard=dashboard, source=source, type=item["widget_type"], title=item["title"],
                    fields=AI_FIELDS[item["widget_type"]], style={},
                    position={"order": order, "w": 4 if order < 3 else 12, "h": 340,
                              "row_id": "row_1" if order < 3 else "row_2"})
        return dashboard, source

    def open_editor(self, dashboard):
        self.page.goto(f"{self.live_server_url}/tableros/{dashboard.id}/edit/")
        self.page.locator("#dashboard-canvas").wait_for()
        self.page.wait_for_load_state("networkidle")
        self.pause(800)

    def pick_google_sheet(self, tab="Ventas"):
        """Paso 1 del selector de fuente: Hoja de Google → Drive (simulado) → pestaña → Aceptar."""
        page = self.page
        self.caption("Elegimos el origen: una hoja de Google")
        self.click(page.get_by_role("button", name="Hoja de Google"))
        self.caption("Abrimos Google Drive y elegimos el documento")
        self.click(page.get_by_role("button", name="Elegir en mi Drive"))
        page.get_by_text(SHEET_NAME).first.wait_for()
        self.pause(800)
        self.caption("Elegimos la pestaña con los datos")
        self.click(page.locator("label", has=page.locator('input[name="source-tab"]')).filter(has_text=tab).first)
        self.click(page.get_by_role("button", name="Aceptar"))
        self.caption("Es una hoja normal, no respuestas de un formulario")
        self.click(page.get_by_role("button", name="No, es una hoja normal"))
        page.get_by_text("Nombre a mostrar").first.wait_for()
        self.pause(800)

    def submit_source(self):
        button = self.page.locator('button[x-text="submitLabel"]:visible')
        self.click(button)

    # ------------------------------------------------------------------ videos
    def test_01_crear_tablero(self):
        page = self.page
        page.goto(self.live_server_url + "/")
        self.pause(800)
        self.caption("Desde el inicio, creamos un tablero nuevo")
        self.click(page.get_by_role("link", name="Crear tablero").first)
        self.pick_google_sheet()
        self.caption("Le damos un nombre al tablero")
        name = page.get_by_label("Nombre del tablero")
        name.fill("")
        self.type(name, "Ventas 2026")
        self.caption("Revisamos las columnas: tipo y formato de cada una")
        self.choose(page.get_by_label("Formato de ventas"), "currency")
        self.caption("Creamos el tablero")
        self.submit_source()
        page.wait_for_url("**/edit/")
        page.locator("#dashboard-canvas").wait_for()
        self.caption("¡Listo! El tablero está conectado a la hoja y vacío, listo para agregar widgets", 2500)

    def test_02_nombres_de_columnas_con_ia(self):
        page = self.page
        page.goto(self.live_server_url + "/tableros/nuevo/")
        self.pause(800)
        self.pick_google_sheet()
        self.caption("Los nombres de las columnas vienen tal cual de la hoja")
        self.point(page.get_by_text("Nombre a mostrar").first)
        self.caption("La IA propone nombres claros para mostrar en el tablero")
        self.click(page.get_by_role("button", name="Generar nombres con IA"))
        page.wait_for_function("() => [...document.querySelectorAll('input[aria-label^=\"Nombre a mostrar\"]')]"
                               ".some(i => i.value === 'Región')")
        self.pause(1500)
        self.caption("Podemos ajustar cualquiera a mano antes de crear el tablero")
        label = page.get_by_label("Nombre a mostrar de unidades")
        label.fill("")
        self.type(label, "Unidades")
        self.submit_source()
        page.wait_for_url("**/edit/")
        self.caption("Los widgets usarán estos nombres en títulos, ejes y leyendas", 2000)

    def test_03_agregar_fuente(self):
        page = self.page
        dashboard, _ = self.board(widgets=False)
        self.open_editor(dashboard)
        self.caption("Un tablero puede combinar varias hojas: abrimos las fuentes de datos")
        self.click(page.locator("#sources-btn"))
        self.caption("Agregamos otra fuente")
        self.click(page.get_by_role("button", name="Agregar fuente"))
        self.pick_google_sheet(tab="Ventas 2025")
        self.caption("Guardamos la nueva fuente")
        self.submit_source()
        page.get_by_text("Ventas 2025").first.wait_for()
        self.caption("Las dos fuentes quedan disponibles para los widgets del tablero", 2500)

    def test_04_agregar_widgets(self):
        page = self.page
        dashboard, _ = self.board(widgets=False)
        self.open_editor(dashboard)
        drawer = page.locator("#edit-drawer")
        row = page.locator(".canvas-row-body").first

        self.caption("Arrastramos un indicador (KPI) desde el panel al tablero")
        self.drag(page.locator('.rail-item[data-type="kpi"]'), row)
        self.caption("En el panel elegimos qué mide: la suma de las ventas")
        self.choose(drawer.locator('select[aria-label="Columna"]').first, L["ventas"])
        self.choose(drawer.locator('select[aria-label="Agregación"]').first, "sum")
        self.click(drawer.get_by_role("button", name="Guardar", exact=True))
        self.close_drawer()

        self.caption("Ahora un gráfico de barras")
        self.drag(page.locator('.rail-item[data-type="bar"]'), row)
        self.caption("Ventas por región")
        self.choose(drawer.locator('select[aria-label="Dimensión"]').first, L["region"])
        self.choose(drawer.locator('select[aria-label="Columna"]').first, L["ventas"])
        self.choose(drawer.locator('select[aria-label="Agregación"]').first, "sum")
        self.click(drawer.get_by_role("button", name="Guardar", exact=True))
        self.close_drawer()

        self.caption("Y una tabla dinámica en una fila nueva")
        self.click(page.get_by_role("button", name="Agregar fila debajo").first)
        self.drag(page.locator('.rail-item[data-type="dynamic_table"]'), page.locator(".canvas-row-body").nth(1))
        self.caption("Las ventas de cada categoría, con su total")
        self.choose(drawer.locator('select[aria-label="Dimensión"]').first, L["categoria"])
        self.choose(drawer.locator('select[aria-label="Columna"]').first, L["ventas"])
        self.choose(drawer.locator('select[aria-label="Agregación"]').first, "sum")
        self.click(drawer.get_by_role("button", name="Guardar", exact=True))
        self.close_drawer()
        self.caption("Cada widget se guarda y se dibuja con los datos de la hoja", 2500)

    def test_05_generar_tablero_con_ia(self):
        page = self.page
        dashboard, _ = self.board(widgets=False)
        self.open_editor(dashboard)
        self.caption("Con el tablero vacío, le pedimos a la IA que lo arme")
        self.click(page.locator("#ai-board-btn"))
        self.type(page.locator("#ai-board-prompt"),
                  "Un tablero de ventas: el total, la comparación por región, la evolución mensual "
                  "y el detalle por categoría y canal")
        self.click(page.get_by_role("dialog").get_by_role("button", name="Generar", exact=True))
        page.get_by_label("Incluir Ventas totales").wait_for()
        self.caption("La IA propone los widgets: podemos quitar alguno o cambiarle el título")
        self.point(page.get_by_label("Título del widget").nth(1))
        self.pause(800)
        self.click(page.get_by_role("button", name="Crear 4 widgets"))
        page.get_by_role("button", name="Listo").wait_for()
        self.caption("La IA crea y configura cada widget")
        self.click(page.get_by_role("button", name="Listo"))
        self.caption("El tablero queda armado y se puede seguir editando", 2500)

    def test_06_asistente_ia_del_widget(self):
        page = self.page
        dashboard, _ = self.board()
        self.open_editor(dashboard)
        bar = page.locator(f'[data-widget-id="{dashboard.widgets.get(type="bar").id}"]')
        self.caption("Cada widget tiene un asistente de IA en su panel")
        self.point(bar)
        self.click(bar.locator(".edit-widget-btn"))
        drawer = page.locator("#edit-drawer")
        self.click(drawer.get_by_role("button", name="Consultar a la IA"))
        self.caption("Le pedimos un cambio con nuestras palabras")
        self.type(drawer.locator("textarea").first, "Muestra las unidades vendidas por canal, en barras horizontales")
        self.click(drawer.get_by_role("button", name="Enviar"))
        drawer.get_by_text("La IA propone:").wait_for()
        self.caption("La IA explica qué va a cambiar")
        self.pause(1200)
        self.click(drawer.get_by_role("button", name="Aplicar y guardar"))
        self.caption("Aplicado: el widget cambia y se puede deshacer", 1800)
        self.close_drawer()
        self.caption("Unidades por canal, en barras horizontales", 2000)

    def test_07_analisis_ia_de_un_widget(self):
        page = self.page
        dashboard, _ = self.board()
        self.open_editor(dashboard)
        bar = page.locator(f'[data-widget-id="{dashboard.widgets.get(type="bar").id}"]')
        self.caption("La IA también interpreta los datos de un widget")
        self.point(bar)
        self.click(bar.locator(".widget-ai-insight-btn"))
        page.get_by_text("Lo más importante").wait_for()
        self.caption("Un resumen con lo más importante y una sugerencia", 2500)
        self.click(page.get_by_role("button", name="Copiar texto"))
        self.caption("Se puede copiar para compartirlo")

    def test_08_chat_con_los_datos(self):
        page = self.page
        dashboard, _ = self.board()
        page.goto(f"{self.live_server_url}/tableros/{dashboard.id}/view/")
        page.wait_for_load_state("networkidle")
        self.pause(1000)
        self.caption("En la vista compartida, cualquiera puede preguntarle a sus datos")
        self.click(page.get_by_role("button", name="Abrir chat con tus datos"))
        self.click(page.get_by_role("button", name="¿Cuál es el resumen general", exact=False).first)
        page.get_by_text("96 ventas").wait_for()
        self.caption("La IA responde con el cálculo que hizo", 2000)
        self.type(page.get_by_label("Pregunta sobre la fuente"), "¿Qué región creció más en el semestre?")
        self.click(page.get_by_role("button", name="Enviar", exact=True))
        page.get_by_text("que más creció en el semestre").wait_for()
        self.caption("Preguntas en lenguaje natural, respuestas sobre la hoja", 2500)

    def test_09_campos_calculados(self):
        page = self.page
        dashboard, _ = self.board(widgets=False)
        self.open_editor(dashboard)
        self.caption("Un campo calculado es una columna nueva hecha con una fórmula")
        self.click(page.locator("#sources-btn"))
        self.click(page.get_by_role("button", name=re.compile("^Editar ")))
        self.click(page.get_by_role("tab", name="Campos calculados"))
        self.caption("Agregamos un campo y le damos un nombre")
        self.click(page.get_by_role("button", name="Agregar campo"))
        name = page.get_by_label("Nombre del campo")
        name.fill("")
        self.type(name, "Precio promedio")
        # El selector de fuente está dos veces en la página (agregar y editar): el que se ve.
        palette = page.locator('aside[aria-label="Piezas de la fórmula"]:visible')

        def piece(text):
            return palette.locator("button", has_text=text).first

        def section(title):
            self.click(palette.locator("button").filter(has_text=re.compile(f"^\\s*{title}\\s*$")).first)

        self.caption("La fórmula se arma con bloques: primero, una división")
        section("Operadores")
        self.click(piece("÷"))
        self.caption("En el primer hueco, la suma de las ventas")
        section("Agregaciones")
        self.click(piece("SUM"))
        section("Columnas")
        self.click(piece(L["ventas"]))
        self.caption("En el segundo, la suma de las unidades")
        section("Agregaciones")
        self.click(piece("SUM"))
        section("Columnas")
        self.click(piece(L["unidades"]))
        page.locator("p:visible", has_text=re.compile("Toda la hoja|Primeras filas")).first.wait_for()
        self.caption("Usa SUM: es un campo «Agregado», y la vista previa muestra su valor en toda la hoja", 2500)
        self.caption("Guardamos: el campo queda disponible como métrica en todos los widgets")
        self.click(page.get_by_role("button", name="Guardar cambios"))
        self.pause(1200)
        self.caption("Al volver a la fuente, el campo está guardado con su fórmula")
        self.click(page.get_by_role("button", name=re.compile("^Editar ")))
        self.click(page.get_by_role("tab", name="Campos calculados"))
        page.locator("p:visible", has_text=re.compile("Toda la hoja")).first.wait_for()
        self.pause(1500)
