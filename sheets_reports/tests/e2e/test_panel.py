"""
El panel del editor en un navegador de verdad (Playwright + Chromium): lo que los tests de
Python no ven, el JS del panel y el dibujo de las tablas.

Solo corren con E2E=1 (necesitan Playwright, Chromium y los CDN de Alpine, Tabulator y ApexCharts):

    venv/bin/pip install -r requirements-dev.txt       # una vez
    venv/bin/python -m playwright install chromium     # una vez
    E2E=1 venv/bin/python manage.py test sheets_reports.tests.e2e
"""
import os
import unittest
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.staticfiles.testing import StaticLiveServerTestCase

from sheets_reports.models import Widget
from sheets_reports.tests.fixtures import agg, fields, make_board, sales_df

E2E = os.environ.get("E2E") == "1"
SHEET = "sheets_reports.services.sheets.get_sheet_dataframe"
COLUMNS = [{"name": "categoria", "type": "text", "include": True},
           {"name": "mes", "type": "text", "include": True},
           {"name": "anio", "type": "number", "include": True},
           {"name": "ventas", "type": "number", "include": True, "format": "currency"}]


@unittest.skipUnless(E2E, "Pruebas en el navegador: correr con E2E=1 (requirements-dev.txt + Chromium)")
class PanelTests(StaticLiveServerTestCase):
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
        cls.browser = cls._playwright.chromium.launch()

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
            position=extra.pop("position", {"x": 0, "y": 0, "w": 12, "h": 420}), **extra)

    def open_editor(self, widget):
        self.page.goto(f"{self.live_server_url}/tableros/{self.dashboard.id}/edit/")
        card = self.page.locator(f'[data-widget-id="{widget.id}"]')
        card.locator(".tabulator-row").first.wait_for()
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
        cells = card.locator(".tabulator-row .tb-num").all_inner_texts()
        self.assertTrue(cells and all(c.startswith("$") for c in cells if c.strip() not in ("", "-")), cells)
        totals = card.locator(".tabulator-calcs .tb-num").all_inner_texts()
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
        self.page.wait_for_timeout(300)   # el reparto del ancho corre tras construir la tabla
        sizes = card.evaluate("""el => {
            const holder = el.querySelector('.tabulator-tableholder');
            const table = el.querySelector('.tabulator-table');
            const rows = el.querySelectorAll('.tabulator-tableholder .tabulator-row');
            const footer = el.querySelector('.tabulator-footer');
            return {holder: holder.clientWidth, table: table.scrollWidth,
                    gap: footer.getBoundingClientRect().top - rows[rows.length - 1].getBoundingClientRect().bottom};
        }""")
        self.assertLessEqual(abs(sizes["holder"] - sizes["table"]), 2, sizes)
        self.assertLessEqual(abs(sizes["gap"]), 2, sizes)
