"""Páginas del tablero: cada widget es de una (position.page_id), el render calcula una página
con sus propios filtros, y el PUT del tablero crea, renombra, reordena y quita páginas."""
import json
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase

from sheets_reports.models import Dashboard, Widget
from sheets_reports.services.widget_service import clean_position
from sheets_reports.tests.fixtures import fields, make_board, sales_df
from sheets_reports.tests.test_views import ANIO_2026, board_filters, payload

TWO_PAGES = [{"id": "page_1", "name": "Página 1"}, {"id": "page_2", "name": "Detalle"}]


@mock.patch("sheets_reports.services.sheets.get_sheet_dataframe", side_effect=lambda *a, **k: sales_df())
class PagesTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("admin", "a@a.com", "x")
        self.client.force_login(self.user)
        self.dashboard, self.source = make_board(self.user)
        self.dashboard.pages = TWO_PAGES
        self.dashboard.save()
        self.first = self._widget("Primera", "page_1")
        self.second = self._widget("Segunda", "page_2")

    def _widget(self, title, page_id):
        return Widget.objects.create(
            dashboard=self.dashboard, source=self.source, type="bar", title=title, fields=fields(),
            position={"order": 0, "w": 6, "h": 300, "row_id": "row_1", "page_id": page_id},
        )

    def _render(self, query=""):
        return self.client.get(f"/api/dashboard/{self.dashboard.id}/render/?{query}").json()

    def _put_pages(self, pages):
        return self.client.put(f"/api/dashboards/{self.dashboard.id}/", json.dumps({"pages": pages}),
                               content_type="application/json")

    # --------------------------------------------------------------- render
    def test_un_tablero_nuevo_tiene_la_pagina_1(self, _df):
        board = Dashboard.objects.create(nombre="Nuevo", owner=self.user)
        self.assertEqual(board.pages, [{"id": "page_1", "name": "Página 1"}])

    def test_render_trae_solo_los_widgets_de_la_pagina(self, _df):
        data = self._render("page=page_2")
        self.assertEqual(data["page"], "page_2")
        self.assertEqual([w["title"] for w in data["widgets"]], ["Segunda"])
        self.assertEqual(data["dashboard"]["pages"], TWO_PAGES)

    def test_sin_pagina_o_con_una_que_no_existe_es_la_primera(self, _df):
        for query in ("", "page=page_9"):
            data = self._render(query)
            self.assertEqual(data["page"], "page_1")
            self.assertEqual([w["title"] for w in data["widgets"]], ["Primera"])

    def test_un_widget_sin_pagina_o_de_una_que_no_existe_es_de_la_primera(self, _df):
        Widget.objects.create(dashboard=self.dashboard, source=self.source, type="bar", title="Sin página",
                              fields=fields(), position={"order": 1, "w": 6, "h": 300, "row_id": "row_1"})
        self._widget("Huérfano", "page_7")
        titles = [w["title"] for w in self._render("page=page_1")["widgets"]]
        self.assertEqual(sorted(titles), ["Huérfano", "Primera", "Sin página"])

    def test_los_filtros_son_de_la_pagina_que_se_pide(self, _df):
        filtered = self._render(f"page=page_2&{board_filters(ANIO_2026)}")["widgets"][0]["data"]
        self.assertEqual(dict(zip(filtered["categories"], filtered["series"][0]["data"])),
                         {"Hogar": 175.0, "Electrónica": 500.0})
        other = self._render("page=page_1")["widgets"][0]["data"]
        self.assertIn("Ropa", other["categories"])

    # -------------------------------------------------------------- páginas
    def test_crear_renombrar_y_reordenar(self, _df):
        pages = [{"id": "page_2", "name": "  Resumen  "}, {"id": "page_1", "name": "Página 1"},
                 {"id": "page_3", "name": "Nueva"}]
        r = self._put_pages(pages)
        self.assertEqual(r.status_code, 200, r.content)
        expected = [{"id": "page_2", "name": "Resumen"}, {"id": "page_1", "name": "Página 1"},
                    {"id": "page_3", "name": "Nueva"}]
        self.assertEqual(r.json()["pages"], expected)
        self.dashboard.refresh_from_db()
        self.assertEqual(self.dashboard.pages, expected)
        self.assertEqual(Widget.objects.filter(dashboard=self.dashboard).count(), 2)

    def test_quitar_una_pagina_borra_sus_widgets(self, _df):
        r = self._put_pages([{"id": "page_1", "name": "Página 1"}])
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(list(Widget.objects.values_list("title", flat=True)), ["Primera"])

    def test_paginas_no_validas(self, _df):
        cases = {
            "al menos una página": [],
            "id válido": [{"id": "hoja", "name": "X"}],
            "repetida": [{"id": "page_1", "name": "A"}, {"id": "page_1", "name": "B"}],
            "nombre": [{"id": "page_1", "name": "  "}],
            "60 caracteres": [{"id": "page_1", "name": "x" * 61}],
        }
        for message, pages in cases.items():
            r = self._put_pages(pages)
            self.assertEqual(r.status_code, 400, message)
            self.assertIn(message, r.json()["error"])
        self.assertEqual(Widget.objects.count(), 2)

    def test_un_filtro_por_pagina(self, _df):
        def add_filter(page_id):
            body = payload("filter", "Filtros", fields_data={"dimensions": ["categoria"], "metrics": []},
                           position={"page_id": page_id})
            return self.client.post(f"/api/dashboard/{self.dashboard.id}/widgets/", json.dumps(body),
                                    content_type="application/json")
        self.assertEqual(add_filter("page_1").status_code, 201)
        self.assertEqual(add_filter("page_2").status_code, 201)
        second = add_filter("page_2")
        self.assertEqual(second.status_code, 422)
        self.assertIn("por página", second.json()["error"])

    def test_clean_position_conserva_la_pagina(self, _df):
        self.assertEqual(clean_position({"page_id": "page_4"})["page_id"], "page_4")
        self.assertEqual(clean_position({})["page_id"], "page_1")

    def test_duplicar_conserva_las_paginas(self, _df):
        r = self.client.post(f"/api/dashboards/{self.dashboard.id}/duplicate/")
        copy = Dashboard.objects.get(id=r.json()["id"])
        self.assertEqual(copy.pages, TWO_PAGES)
        self.assertEqual(sorted(copy.page_of(w) for w in copy.widgets.all()), ["page_1", "page_2"])

    def test_la_vista_muestra_una_pestana_por_pagina(self, _df):
        html = self.client.get(f"/tableros/{self.dashboard.id}/view/").content.decode()
        self.assertIn('data-page="page_1"', html)
        self.assertIn(">Detalle</button>", html)

    def test_con_una_sola_pagina_y_sin_correos_no_hay_pestanas(self, _df):
        self._put_pages([{"id": "page_1", "name": "Página 1"}])
        self.assertNotContains(self.client.get(f"/tableros/{self.dashboard.id}/view/"), 'id="view-tabs"')
