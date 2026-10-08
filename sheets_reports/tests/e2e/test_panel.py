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
        return self.page.locator('aside[aria-label="Piezas de la fórmula"]')

    def piece(self, panel, text):
        return panel.locator("button", has_text=text).first

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
        self.piece(panel, "SI").click()
        self.piece(panel, "[ ] = [ ]").click()
        self.piece(panel, "categoria").click()
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
        canvas = self.page.locator('[aria-label="Fórmula"]')
        canvas.get_by_text("SUM", exact=True).wait_for()
        self.page.get_by_text("Toda la hoja: 755").wait_for()
        # «+» soltado sobre el bloque SUM: SUM([ventas]) + [ ].
        sum_block = canvas.locator("[data-fb-drag='']")
        self.drag(self.page.get_by_role("button", name="Sumar"), sum_block)
        hole = canvas.get_by_text("suelta aquí")
        hole.wait_for()
        value = panel.get_by_label("Valor", exact=True)
        value.fill("1")
        self.drag(panel.locator("button.font-mono", has_text="1"), hole)
        self.page.get_by_text("Toda la hoja: 756").wait_for()
        # Un bloque arrastrado al panel se quita.
        self.drag(canvas.locator("[data-fb-drag='1']"), panel.get_by_text("Condición"))
        canvas.get_by_text("suelta aquí").wait_for()
        self.page.get_by_text("Completa los huecos").wait_for()

    def test_el_texto_vuelve_al_mismo_arbol(self):
        """Ida y vuelta: árbol del servidor → texto del constructor → el mismo árbol."""
        from sheets_reports.engine.formulas import formula_tree

        formulas = [
            'SUM(IF([categoria] = "Hogar", [ventas], 0)) / SUM([ventas]) * 100',
            'AVG([Respuesta] = "Sí") * 100',
            'IF([grado] = "a" OR [grado] = "b", "X", IF([grado] = "c" OR [grado] = "d", "Y", "Z"))',
            "[a] - ([b] - [c])", "([a] + [b]) * [c]", "-([a] + 1)", "NOT ([a] = 1 OR [b] = 2)",
            "[a] / [b] / [c]", "[a] / ([b] / [c])", "COUNT(1)", "-1.5 * [x]", "IF([t] = 'dice \"sí\"', 1, 0)",
        ]
        self.open_calculated()
        for formula in formulas:
            with self.subTest(formula=formula):
                tree = formula_tree(formula)
                text = self.page.evaluate("tree => FormulaBlocks.text(tree)", tree)
                self.assertEqual(formula_tree(text), tree, text)
