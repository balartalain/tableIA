"""
El panel del editor en un navegador de verdad (Playwright + Chromium): lo que los tests de
Python no ven, el JS del panel y el dibujo de las tablas.

Solo corren con E2E=1 (necesitan Playwright, Chromium y los CDN de Alpine, Tabulator y ApexCharts):

    venv/bin/pip install -r requirements-dev.txt       # una vez
    venv/bin/python -m playwright install chromium     # una vez
    E2E=1 venv/bin/python manage.py test sheets_reports.tests.e2e

Para verlas: `HEADED=1 SLOWMO=300` antes del comando abre Chromium con ventana y pausa entre
acciones; `PWDEBUG=1` abre además el Inspector de Playwright para ir paso a paso.
"""
import os
import re
import unittest
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.core.servers.basehttp import WSGIServer
from django.test.testcases import LiveServerThread

from sheets_reports.models import Widget
from sheets_reports.tests.fixtures import agg, fields, make_board, sales_df

E2E = os.environ.get("E2E") == "1"
SHEET = "sheets_reports.services.sheets.get_sheet_dataframe"
COLUMNS = [{"name": "categoria", "type": "text", "include": True},
           {"name": "mes", "type": "text", "include": True},
           {"name": "anio", "type": "number", "include": True},
           {"name": "ventas", "type": "number", "include": True, "format": "currency"}]


class SerialWSGIServer(WSGIServer):
    """Atiende las peticiones de a una, en el hilo del servidor de pruebas."""

    def __init__(self, *args, connections_override=None, **kwargs):
        super().__init__(*args, **kwargs)


class SerialLiveServerThread(LiveServerThread):
    """El servidor de pruebas sin hilos por petición. Con SQLite en memoria todos los hilos
    comparten una conexión, y las peticiones simultáneas de la página (schema, render,
    sugerencias…) corrompían la caché de sentencias de sqlite3: `KeyError` con el SQL y
    errores 500 al azar."""
    server_class = SerialWSGIServer


@unittest.skipUnless(E2E, "Pruebas en el navegador: correr con E2E=1 (requirements-dev.txt + Chromium)")
class PanelTests(StaticLiveServerTestCase):
    server_thread_class = SerialLiveServerThread

    @classmethod
    def setUpClass(cls):
        # Playwright (sync) deja un event loop en el hilo principal: el ORM lo tomaría por
        # código async y se negaría a correr.
        os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
        super().setUpClass()
        from playwright.sync_api import sync_playwright

        cls._sheet = mock.patch(SHEET, side_effect=lambda *a, **k: sales_df())
        cls._sheet.start()
        cls._playwright = sync_playwright().start()
        # HEADED=1: con ventana; SLOWMO=300: 300 ms entre acciones, para seguirlas con la vista.
        cls.browser = cls._playwright.chromium.launch(
            headless=os.environ.get("HEADED") != "1", slow_mo=int(os.environ.get("SLOWMO") or 0))

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls._playwright.stop()
        cls._sheet.stop()
        super().tearDownClass()

    def setUp(self):
        self.user = get_user_model().objects.create_superuser("admin", "a@a.com", "x")
        self.client.force_login(self.user)
        self.dashboard, self.source = make_board(self.user, columns=COLUMNS)
        self.context = self.browser.new_context(viewport={"width": 1400, "height": 900})
        self.context.add_cookies([{"name": "sessionid", "url": self.live_server_url,
                                   "value": self.client.cookies["sessionid"].value}])
        self.page = self.context.new_page()

    def tearDown(self):
        self.context.close()

    # ------------------------------------------------------------------ helpers
    def widget(self, widget_fields, **extra):
        return Widget.objects.create(
            dashboard=self.dashboard, source=self.source, type="dynamic_table", title="Tabla",
            fields=widget_fields, style=extra.pop("style", {}),
            position=extra.pop("position", {"order": 0, "w": 12, "h": 420, "row_id": "row_1"}), **extra)

    def open_editor(self, widget):
        self.page.goto(f"{self.live_server_url}/tableros/{self.dashboard.id}/edit/")
        card = self.page.locator(f'[data-widget-id="{widget.id}"]')
        card.locator(".tb-table tbody tr").first.wait_for()
        return card

    def open_panel(self, card):
        card.hover()
        card.locator(".edit-widget-btn").click()
        drawer = self.page.locator("#edit-drawer")
        drawer.locator('select[aria-label="Agregación"]').first.wait_for()
        return drawer

    # ------------------------------------------------------------------ flujos
    def test_el_formato_de_la_fuente_llega_a_todas_las_columnas(self):
        """`ventas` es Moneda en la fuente: las celdas del pivote y el total muestran «$»."""
        card = self.open_editor(self.widget(fields(pivots=["mes"])))
        cells = card.locator(".tb-table tbody .tb-num").all_inner_texts()
        self.assertTrue(cells and all(c.startswith("$") for c in cells if c.strip() not in ("", "-")), cells)
        totals = card.locator(".tb-table tfoot .tb-num").all_inner_texts()
        self.assertTrue(totals and all(t.startswith("$") for t in totals if t.strip()), totals)

    def test_mostrar_como_desaparece_con_promedio(self):
        drawer = self.open_panel(self.open_editor(self.widget(fields())))
        show_as = drawer.locator('select[aria-label="Mostrar como"]')
        self.assertEqual(show_as.count(), 1)
        drawer.locator('select[aria-label="Agregación"]').first.select_option("avg")
        show_as.first.wait_for(state="detached")
        self.assertEqual(drawer.get_by_text("no aplica").count(), 0)

    def test_la_dimension_obligatoria_no_se_puede_quitar(self):
        drawer = self.open_panel(self.open_editor(self.widget(fields())))
        remove = drawer.locator('button[aria-label="Quitar dimensión"]')
        self.assertEqual(remove.count(), 1)
        self.assertFalse(remove.first.is_visible())
        # El bloque de dimensiones: el padre de su lista (su encabezado tiene «Agregar»).
        block = drawer.locator('[data-column-list="dimensions"]').locator("xpath=..")
        block.get_by_role("button", name="Agregar").click()
        self.page.wait_for_function(
            "() => [...document.querySelectorAll('#edit-drawer button[aria-label=\"Quitar dimensión\"]')]"
            ".filter(b => b.offsetParent !== null).length === 2")

    def test_sobre_una_columna_de_texto_solo_se_cuenta(self):
        drawer = self.open_panel(self.open_editor(self.widget(fields())))
        drawer.locator('select[aria-label="Columna"]').first.select_option("categoria")
        agg_select = drawer.locator('select[aria-label="Agregación"]').first
        self.page.wait_for_function(
            "() => document.querySelector('#edit-drawer select[aria-label=\"Agregación\"]').value === 'count'")
        values = agg_select.locator("option").evaluate_all("os => os.map(o => o.value)")
        self.assertNotIn("sum", values)
        self.assertIn("count_distinct", values)

    def test_la_tabla_dinamica_llena_el_ancho_y_el_total_queda_pegado(self):
        card = self.open_editor(self.widget(fields(pivots=["mes"], metrics=[agg("total_ventas")]),
                                            style={"showColumnTotals": True}))
        sizes = card.evaluate("""el => {
            const holder = el.querySelector('.tb-table-scroll');
            const table = el.querySelector('.tb-table');
            const rows = table.querySelectorAll('tbody tr');
            const footer = table.querySelector('tfoot');
            return {holder: holder.clientWidth, table: table.scrollWidth,
                    gap: footer.getBoundingClientRect().top - rows[rows.length - 1].getBoundingClientRect().bottom};
        }""")
        self.assertLessEqual(abs(sizes["holder"] - sizes["table"]), 2, sizes)
        self.assertLessEqual(abs(sizes["gap"]), 2, sizes)


    def test_un_porcentaje_calculado_sale_con_dos_decimales(self):
        """Un campo calculado llega sin redondear (Feb: 50 / 330 = 15,1515…): la tabla muestra 2 decimales."""
        self.source.calculated_fields = [{"id": "h", "name": "% Hogar", "format": "percent",
                                          "formula": 'SUM(IF([categoria] = "Hogar", [ventas], 0)) / SUM([ventas]) * 100'}]
        self.source.save()
        card = self.open_editor(self.widget(fields(dimensions=["mes"], metrics=[{"field": "% Hogar", "agg": "auto", "alias": "hogar"}])))
        cells = card.locator(".tb-table tbody .tb-num").all_inner_texts()
        self.assertIn("15.15%", cells)

    def test_el_formateador_comun_de_numeros(self):
        """`formatNumber` (utils/number-format.js): el formato de todos los valores de datos."""
        self.page.goto(f"{self.live_server_url}/tableros/{self.dashboard.id}/edit/")
        cases = [
            ([15.151515], "15.15"), ([15.151515, {"percent": True}], "15.15%"), ([1234567], "1,234,567"),
            ([1234.5, {"currency": True}], "$1,234.50"), ([-5, {"currency": True}], "-$5.00"),
            ([25, {"decimals": 1}], "25.0"), ([25.04, {"percent": True, "decimals": 0}], "25%"),
            ([12345, {"compact": True}], "12.3K"), ([3, {"signed": True, "percent": True}], "+3%"),
            ([10, {"prefix": "RD$", "suffix": "pesos"}], "RD$10 pesos"),
            ([None], "-"), (["", {"empty": "—"}], "—"), (["Hogar"], "Hogar"), (["7.5"], "7.5"),
        ]
        for args, expected in cases:
            with self.subTest(args=args):
                self.assertEqual(self.page.evaluate("args => formatNumber(...args)", args), expected)

    def test_la_tabla_de_fuentes(self):
        """«Sin usar», columnas incluidas, estado de la fuente, sus acciones y la confirmación al
        eliminar."""
        with mock.patch("sheets_reports.services.google_drive.tab_status", return_value="no_access"):
            self.page.goto(f"{self.live_server_url}/tableros/{self.dashboard.id}/edit/")
            self.page.locator("#sources-btn").click()
            # Dentro del panel de fuentes: la ayuda también menciona «Sin acceso».
            self.page.locator("#bottom-sheet").get_by_text("Sin acceso").wait_for()
        self.assertTrue(self.page.get_by_role("cell", name="Sin usar").is_visible())
        self.assertTrue(self.page.get_by_text("5 de 5 incluidas").is_visible())
        # «Actualizar datos» ya no está en la fila: vive dentro de «Editar» (la fuente abierta).
        self.assertTrue(self.page.get_by_role("button", name=f"Editar {self.source.label}").is_visible())
        link = self.page.get_by_role("link", name=f"Abrir origen de {self.source.label}")
        self.assertEqual(link.get_attribute("href"), "https://docs.google.com/spreadsheets/d/abc/edit#gid=0")
        self.page.get_by_role("button", name=f"Eliminar {self.source.label}").click()
        self.page.get_by_text("Ningún widget la usa.").wait_for()
        self.page.get_by_role("alertdialog").get_by_role("button", name="Cancelar").click()
        # «Cambiar hoja» va directo a elegir otra hoja.
        self.page.get_by_role("button", name=f"Cambiar fuente de {self.source.label}").click()
        self.page.get_by_role("button", name="Hoja de Google").wait_for()

@unittest.skipUnless(E2E, "Pruebas en el navegador: correr con E2E=1 (requirements-dev.txt + Chromium)")
class FormulaBuilderTests(PanelTests):
    """El constructor de fórmulas por bloques (pestaña «Campos calculados» al editar la fuente)."""

    # Los casos de PanelTests no se repiten aquí.
    for _name in [n for n in dir(PanelTests) if n.startswith("test_")]:
        locals()[_name] = None
    del _name

    def open_calculated(self):
        self.page.goto(f"{self.live_server_url}/tableros/{self.dashboard.id}/edit/")
        self.page.locator("#sources-btn").click()
        self.page.get_by_role("button", name=f"Editar {self.source.label}").click()
        self.page.get_by_role("tab", name="Campos calculados").click()
        # El selector de fuente está dos veces en la página (agregar y editar): el que se ve.
        return self.page.locator('aside[aria-label="Piezas de la fórmula"]:visible')

    def piece(self, panel, text):
        return panel.locator("button", has_text=text).first

    # Las secciones de la paleta se pliegan y solo una está abierta (`activePaletteSection`).
    SECTIONS = {"Columnas": "columns", "Valores": "values", "Operadores": "operators",
                "Condición": "condition", "Agregaciones": "aggregations"}

    def open_section(self, panel, title):
        """Abre la sección de la paleta con un clic en su encabezado, si no está abierta (el
        clic sobre la abierta la cerraría)."""
        if panel.evaluate("el => Alpine.$data(el).activePaletteSection") == self.SECTIONS[title]:
            return
        panel.locator("button").filter(has_text=re.compile(rf"^\s*{title}\s*$")).first.click()
        # Las secciones se abren con una transición de 300 ms: un arrastre medido antes
        # tomaría las piezas a mitad de camino.
        self.page.wait_for_timeout(400)

    def drag(self, source, target):
        """Un arrastre de verdad con el mouse (interact.js necesita varios movimientos)."""
        source.wait_for()   # Alpine puede mostrarlos un instante después
        target.wait_for()
        a, b = source.bounding_box(), target.bounding_box()
        self.page.mouse.move(a["x"] + a["width"] / 2, a["y"] + a["height"] / 2)
        self.page.mouse.down()
        self.page.mouse.move(a["x"] + a["width"] / 2 + 10, a["y"] + a["height"] / 2 + 10, steps=3)
        self.page.mouse.move(b["x"] + 6, b["y"] + b["height"] / 2, steps=10)
        self.page.mouse.up()

    def saved_formulas(self):
        self.source.refresh_from_db()
        return [f["formula"] for f in self.source.calculated_fields]

    def test_armar_un_si_con_clics_y_guardarlo(self):
        panel = self.open_calculated()
        self.page.get_by_role("button", name="Agregar campo").click()
        self.page.get_by_label("Nombre del campo").fill("Nivel")
        # Cada clic coloca la pieza en el hueco elegido y elige el siguiente.
        self.open_section(panel, "Agregaciones")
        self.piece(panel, "SI").click()
        self.open_section(panel, "Condición")
        self.piece(panel, "[ ] = [ ]").click()
        self.open_section(panel, "Columnas")
        self.piece(panel, "categoria").click()
        self.open_section(panel, "Valores")
        panel.get_by_role("radio", name="Texto").click()
        value = panel.get_by_label("Valor", exact=True)
        for text in ("Hogar", "X", "Y"):
            value.fill(text)
            value.press("Enter")
        self.page.get_by_text("Primeras filas: X · Y · X · Y · Y").wait_for()
        self.page.get_by_role("button", name="Guardar cambios").click()
        self.page.wait_for_function("() => !document.querySelector('aside[aria-label=\"Piezas de la fórmula\"]')"
                                    "?.offsetParent")
        self.assertEqual(self.saved_formulas(), ['IF([categoria] = "Hogar", "X", "Y")'])

    def test_arrastrar_un_operador_envuelve_el_bloque(self):
        self.source.calculated_fields = [{"id": "t", "name": "Total", "formula": "SUM([ventas])", "format": "number"}]
        self.source.save()
        panel = self.open_calculated()
        canvas = self.page.locator('[aria-label="Fórmula"]:visible')
        canvas.get_by_text("SUM", exact=True).wait_for()
        self.page.get_by_text("Toda la hoja: 755").wait_for()
        # «+» soltado sobre el bloque SUM: SUM([ventas]) + [ ].
        sum_block = canvas.locator("[data-fb-drag='']")
        self.open_section(panel, "Operadores")
        self.drag(panel.get_by_role("button", name="Sumar"), sum_block)
        hole = canvas.get_by_text("suelta aquí")
        hole.wait_for()
        self.open_section(panel, "Valores")
        value = panel.get_by_label("Valor", exact=True)
        value.fill("1")
        self.drag(panel.locator("button.font-mono", has_text="1"), hole)
        self.page.get_by_text("Toda la hoja: 756").wait_for()
        # Un bloque arrastrado al panel se quita.
        self.drag(canvas.locator("[data-fb-drag='1']"), panel.get_by_text("Condición"))
        canvas.get_by_text("suelta aquí").wait_for()
        self.page.get_by_text("Completa los huecos").wait_for()

    def test_una_funcion_sin_huecos_se_dibuja_con_su_nombre(self):
        self.source.calculated_fields = [{"id": "h", "name": "Hoy", "formula": "TODAY()", "format": "number"}]
        self.source.save()
        self.open_calculated()
        canvas = self.page.locator('[aria-label="Fórmula"]:visible')
        block = canvas.locator("[data-fb-drag='']")
        block.get_by_text("HOY", exact=True).wait_for()
        self.assertNotIn("NaN", block.inner_text())

    def test_generar_con_ia_reemplaza_el_lienzo(self):
        self.source.calculated_fields = [{"id": "t", "name": "", "formula": "SUM([ventas])", "format": "number"}]
        self.source.save()
        panel = self.open_calculated()
        canvas = self.page.locator('[aria-label="Fórmula"]:visible')
        prompt = self.page.get_by_label("Pedido para generar la fórmula con IA")
        ask = "sheets_reports.services.ai_formula.generate_json"
        # No pudo: el motivo y el lienzo igual.
        with mock.patch(ask, return_value={"ok": False, "reason": "La hoja no tiene costos."}):
            prompt.fill("el margen")
            # El «Generar» de la fórmula, no los de «Generar con IA» del tablero.
            prompt.locator("xpath=ancestor::form[1]").get_by_role("button", name="Generar").click()
            self.page.get_by_text("No pude armar la fórmula: La hoja no tiene costos.").wait_for()
        canvas.get_by_text("SUM", exact=True).wait_for()
        # Pudo: los bloques nuevos reemplazan los anteriores y el campo toma el nombre propuesto.
        answer = {"ok": True, "formula": 'IF([categoria] = "Hogar", "Sí", "No")', "name": "Es Hogar"}
        with mock.patch(ask, return_value=answer):
            prompt.fill("marca si es de Hogar")
            prompt.press("Enter")
            self.page.get_by_text("Primeras filas: Sí · No · Sí · No · No").wait_for()
        self.assertEqual(canvas.get_by_text("SUM", exact=True).count(), 0)
        self.assertEqual(self.page.get_by_label("Nombre del campo").input_value(), "Es Hogar")
        self.assertEqual(prompt.input_value(), "")



class HelpPanelTests(PanelTests):
    """El centro de ayuda: se abre desde el menú del tablero, se navega con enlaces y se
    ensancha arrastrando su borde; un «?» del panel del widget abre su tópico."""

    for _name in [n for n in dir(PanelTests) if n.startswith("test_")]:
        locals()[_name] = None
    del _name

    def help_width(self):
        return self.page.locator("#help-panel").evaluate("el => el.getBoundingClientRect().width")

    def wait_help_width(self, width):
        """El panel se abre animando su ancho: se espera a que llegue."""
        self.page.wait_for_function(
            "w => Math.abs(document.getElementById('help-panel').getBoundingClientRect().width - w) <= 1", arg=width)

    def test_navegar_y_ensanchar_la_ayuda(self):
        self.page.goto(f"{self.live_server_url}/tableros/{self.dashboard.id}/edit/")
        self.page.get_by_role("button", name="Opciones del tablero").click()
        self.page.get_by_role("button", name="Ayuda", exact=True).click()
        panel = self.page.locator("#help-panel")
        panel.get_by_role("link", name="Dimensiones").click()
        panel.locator("#help-topic-dimensiones").wait_for()
        # Un enlace dentro del tópico lleva a otro, y «Atrás» vuelve.
        panel.locator("#help-topic-dimensiones").get_by_role("link", name="Pivotes").click()
        panel.locator("#help-topic-pivotes").wait_for()
        panel.get_by_role("button", name="Atrás").click()
        panel.locator("#help-topic-dimensiones").wait_for()
        # El buscador encuentra por el texto del tópico, sin tildes.
        panel.get_by_placeholder("Buscar en la ayuda…").fill("contrasena de aplicacion")
        panel.get_by_role("link", name="Envío de correos").wait_for()
        panel.get_by_role("link", name="Dimensiones").wait_for(state="hidden")
        # Arrastrar el borde izquierdo hacia la izquierda la ensancha, y el ancho se recuerda.
        self.wait_help_width(480)
        handle = panel.get_by_role("separator", name="Ancho de la ayuda").bounding_box()
        self.page.mouse.move(handle["x"] + 2, handle["y"] + 200)
        self.page.mouse.down()
        self.page.mouse.move(handle["x"] - 200, handle["y"] + 200, steps=8)
        self.page.mouse.up()
        width = self.help_width()
        self.assertAlmostEqual(width, 680, delta=3)
        self.page.reload()
        self.page.locator("#dashboard-canvas").wait_for()
        self.page.evaluate("() => Alpine.store('help').open('metricas')")
        self.wait_help_width(width)

    def test_el_signo_de_ayuda_del_panel_vuelve_al_widget(self):
        card = self.open_editor(self.widget(fields()))
        drawer = self.open_panel(card)
        title = drawer.locator('input[x-model="$store.dashboard.drawerDraft.title"]')
        title.fill("Borrador sin guardar")
        drawer.get_by_role("button", name="Ayuda de métricas").click()
        self.page.locator("#help-topic-metricas").wait_for()
        self.page.wait_for_function("() => document.getElementById('edit-drawer').getBoundingClientRect().width === 0")
        self.page.get_by_role("button", name="Cerrar la ayuda").click()
        self.page.wait_for_function("() => document.getElementById('edit-drawer').getBoundingClientRect().width > 400")
        self.assertEqual(title.input_value(), "Borrador sin guardar")

    def test_el_video_de_un_topico(self):
        """«Ver video» muestra el reproductor y lo arranca; cambiar de tópico lo pausa."""
        self.page.goto(f"{self.live_server_url}/tableros/{self.dashboard.id}/edit/")
        self.page.evaluate("() => Alpine.store('help').open('crear-tablero')")
        topic = self.page.locator("#help-topic-crear-tablero")
        topic.get_by_role("button", name="Ver video").click()
        video = topic.locator("video")
        video.wait_for()
        self.page.wait_for_function(
            "() => document.querySelector('#help-topic-crear-tablero video').currentTime > 0.5")
        topic.get_by_role("link", name="Conectar una hoja").first.click()
        self.page.locator("#help-topic-conectar-hoja").wait_for()
        self.assertTrue(video.evaluate("v => v.paused"))
