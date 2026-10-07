"""Las vistas son adaptadores HTTP sobre `WidgetService`: render de solo lectura (sin IA),
alta/edición con la misma validación que la IA, asistente con IA y CRUD de tableros."""
import json
from unittest import mock
from urllib.parse import quote

from django.contrib.auth import get_user_model
from django.test import TestCase

from sheets_reports.models import Dashboard, Widget
from sheets_reports.services.ai_spec import SpecGenerationError
from sheets_reports.tests.fixtures import agg, fields, make_board, sales_df


def board_filters(*conditions) -> str:
    """Query string de los filtros del tablero (lo que arma la caja de filtros)."""
    return "filters=" + quote(json.dumps(list(conditions)))


ANIO_2026 = {"field": "anio", "op": "in", "value": [2026]}


def payload(widget_type="bar", title="Ventas por categoría", fields_data=None, style=None, **extra):
    return {"type": widget_type, "title": title, "fields": fields_data or fields(), "style": style or {}, **extra}


@mock.patch("sheets_reports.services.sheets.get_sheet_dataframe", side_effect=lambda *a, **k: sales_df())
class ViewsTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("admin", "a@a.com", "x")
        self.client.force_login(self.user)
        self.dashboard, self.source = make_board(self.user, sheet_id="abc123")
        self.widget = Widget.objects.create(
            dashboard=self.dashboard, source=self.source, type="bar", title="Ventas por categoría",
            fields=fields(), style={},
        )

    # --------------------------------------------------------------- render
    def test_render_no_llama_a_la_ia(self, _df):
        with mock.patch("sheets_reports.services.ai_spec.generate_widget_form") as ai:
            r = self.client.get(f"/api/dashboard/{self.dashboard.id}/render/?{board_filters(ANIO_2026)}")
        ai.assert_not_called()
        self.assertEqual(r.status_code, 200)
        data = r.json()["widgets"][0]["data"]
        self.assertEqual(dict(zip(data["categories"], data["series"][0]["data"])),
                         {"Hogar": 175.0, "Electrónica": 500.0})

    def test_kpi_participacion_respeta_los_filtros_del_tablero(self, _df):
        Widget.objects.create(
            dashboard=self.dashboard, source=self.source, type="kpi", title="Participación de Hogar",
            fields={"dimensions": [], "filters": [],
                    "metrics": [{**agg("hogar"),
                                 "filters": [{"field": "categoria", "op": "eq", "value": "Hogar"}]},
                                agg("total"),
                                {"type": "formula", "alias": "participacion",
                                 "expression": "hogar / total * 100"}]},
            style={"primary": "participacion"},
        )
        r = self.client.get(f"/api/dashboard/{self.dashboard.id}/render/?{board_filters(ANIO_2026)}")
        kpi = next(w for w in r.json()["widgets"] if w["type"] == "kpi")
        # 175 de Hogar entre las 675 de 2026 (no entre las 755 de la hoja completa).
        self.assertEqual(round(kpi["data"]["value"], 1), 25.9)

    def test_filtro_de_columna_inexistente_se_ignora_sin_tumbar_el_tablero(self, _df):
        # Ej. una URL compartida cuando la columna ya no está en la hoja.
        r = self.client.get(f"/api/dashboard/{self.dashboard.id}/render/?"
                            + board_filters({"field": "pais", "op": "in", "value": ["DO"]}, ANIO_2026))
        self.assertEqual(r.status_code, 200)
        self.assertIn("'pais' no existe", r.json()["filter_errors"][0])
        self.assertEqual(r.json()["widgets"][0]["error"], None)
        data = r.json()["widgets"][0]["data"]
        self.assertEqual(dict(zip(data["categories"], data["series"][0]["data"])),
                         {"Hogar": 175.0, "Electrónica": 500.0})

    def test_render_valida_las_reglas_de_las_condiciones(self, _df):
        r = self.client.get(f"/api/dashboard/{self.dashboard.id}/render/?"
                            + board_filters({"field": "ventas", "op": "between", "value": [1]}))
        self.assertEqual(r.status_code, 200)
        self.assertIn("dos números", r.json()["filter_errors"][0])

    def test_filters_que_no_es_una_lista_json(self, _df):
        r = self.client.get(f"/api/dashboard/{self.dashboard.id}/render/?filters=nada")
        self.assertEqual(r.status_code, 400)

    def test_render_widget_con_tipo_que_ya_no_existe(self, _df):
        Widget.objects.filter(id=self.widget.id).update(type="fantasma")
        r = self.client.get(f"/api/dashboard/{self.dashboard.id}/render/")
        self.assertEqual(r.json()["widgets"][0]["error"], "El tipo de widget «fantasma» ya no existe. Edita el widget.")

    def test_render_de_un_widget_roto_no_tumba_el_tablero(self, _df):
        Widget.objects.filter(id=self.widget.id).update(fields={"dimensions": ["pais"], "metrics": [agg()]})
        r = self.client.get(f"/api/dashboard/{self.dashboard.id}/render/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("ya no existe en la hoja", r.json()["widgets"][0]["error"])

    # --------------------------------------------------------------- alta
    def test_crear_kpi_con_condiciones_y_roles(self, _df):
        r = self.client.post(
            f"/api/dashboard/{self.dashboard.id}/widgets/",
            json.dumps(payload(
                "kpi", "Ventas vs año anterior",
                fields_data={"dimensions": [], "filters": [],
                             "metrics": [
                                 agg("actual", filters=[{"field": "anio", "op": "eq", "relative": "latest"}]),
                                 agg("anterior", filters=[{"field": "anio", "op": "eq", "relative": "previous"}]),
                             ]},
                style={"compare": "anterior", "targetMetric": "fixed", "target": 1000,
                       "targetLabel": "Meta", "status_good": 100, "status_warn": 50},
            )),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 201, r.content)
        data = r.json()["data"]
        self.assertEqual(data["value"], 675.0)
        self.assertEqual(data["compare"]["value"], 80.0)
        self.assertEqual(data["compare"]["delta"], 595.0)
        self.assertEqual(data["target"]["pct"], 67.5)
        self.assertEqual(data["status"], "warn")

    def test_crear_barras_con_orden_y_limite(self, _df):
        r = self.client.post(
            f"/api/dashboard/{self.dashboard.id}/widgets/",
            json.dumps(payload(fields_data=fields(sort_by="-total_ventas", limit=1))),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 201, r.content)
        data = r.json()["data"]
        self.assertEqual(data["categories"], ["Electrónica"])
        self.assertEqual(data["series"][0]["data"], [500.0])

    def test_crear_widget_desde_builder_sin_ia(self, _df):
        with mock.patch("sheets_reports.services.ai_spec.generate_widget_form") as ai:
            r = self.client.post(
                f"/api/dashboard/{self.dashboard.id}/widgets/",
                json.dumps(payload(
                    "dynamic_table", "Ventas por categoría y mes",
                    fields_data=fields(pivots=["mes"], metrics=[agg("cantidad", agg="count")]),
                    position={"x": 0, "y": 3, "w": 12, "h": 400},
                )),
                content_type="application/json",
            )
        ai.assert_not_called()
        self.assertEqual(r.status_code, 201, r.content)
        widget = Widget.objects.get(id=r.json()["id"])
        self.assertIsNone(widget.source_prompt)
        self.assertEqual(widget.position, {"x": 0, "y": 3, "w": 12, "h": 400})
        headers = {ch["header"] for c in r.json()["data"]["columns"] for ch in c.get("children", [])}
        self.assertEqual(headers, {"Ene", "Feb", "Mar"})

    def test_crear_widget_invalido_no_guarda_nada(self, _df):
        r = self.client.post(
            f"/api/dashboard/{self.dashboard.id}/widgets/",
            json.dumps(payload(fields_data=fields(pivots=["categoria"]))),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 422)
        self.assertIn("no puede estar también en las dimensiones", r.json()["error"])
        self.assertEqual(Widget.objects.count(), 1)

    def test_crear_barras_con_pivote_y_varias_metricas_se_rechaza(self, _df):
        r = self.client.post(
            f"/api/dashboard/{self.dashboard.id}/widgets/",
            json.dumps(payload(fields_data=fields(pivots=["mes"], metrics=[agg("total_ventas"),
                                                                          agg("cantidad", agg="count")]))),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 422)
        self.assertIn("UNA métrica", r.json()["error"])
        self.assertEqual(Widget.objects.count(), 1)

    def test_crear_estilo_fuera_del_schema_se_descarta(self, _df):
        r = self.client.post(
            f"/api/dashboard/{self.dashboard.id}/widgets/",
            json.dumps(payload(style={"stacked": True, "no_existe": "x"})),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(Widget.objects.get(id=r.json()["id"]).style, {"stacked": True})

    def test_un_solo_widget_de_filtros_por_tablero(self, _df):
        body = json.dumps(payload("filter", "Filtros", fields_data={"dimensions": ["categoria"], "metrics": []}))
        first = self.client.post(f"/api/dashboard/{self.dashboard.id}/widgets/", body,
                                 content_type="application/json")
        second = self.client.post(f"/api/dashboard/{self.dashboard.id}/widgets/", body,
                                  content_type="application/json")
        self.assertEqual(first.status_code, 201, first.content)
        self.assertEqual(second.status_code, 422)
        self.assertIn("Solo se puede agregar un widget «Filtros» por tablero.", second.json()["error"])

    # ------------------------------------------------------------- edición
    def test_update_desde_builder_sin_ia(self, _df):
        with mock.patch("sheets_reports.services.ai_spec.generate_widget_form") as ai:
            r = self.client.put(
                f"/api/widget/{self.widget.id}/",
                json.dumps({"fields": fields(pivots=["mes"], metrics=[agg("total_ventas")]),
                            "style": {"stacked": True}, "title": "Ventas por categoría y mes"}),
                content_type="application/json",
            )
        ai.assert_not_called()
        self.assertEqual(r.status_code, 200, r.content)
        self.widget.refresh_from_db()
        self.assertEqual(self.widget.fields["pivots"], ["mes"])
        self.assertEqual(self.widget.title, "Ventas por categoría y mes")
        self.assertTrue(r.json()["data"]["stacked"])
        self.assertEqual(len(r.json()["data"]["series"]), 3)

    def test_update_persiste_el_style_completo_del_panel_del_kpi(self, _df):
        """Lo que edita el panel del KPI (roles, meta, semáforo, formato) se guarda tal cual."""
        kpi = Widget.objects.create(
            dashboard=self.dashboard, source=self.source, type="kpi", title="Ventas",
            fields={"dimensions": [], "filters": [],
                    "metrics": [agg("total_ventas"), agg("promedio", "avg")]},
            style={},
        )
        style = {"title": "Ventas", "decimals": 1, "abbreviate": True, "prefix": "RD$",
                 "suffix": "", "primary": "total_ventas", "compare": "promedio",
                 "compareMode": "abs", "targetMetric": "fixed", "target": 900,
                 "targetLabel": "Plan", "statusBasis": "target_pct", "status_good": 95,
                 "status_warn": 70, "higher_is_better": False}
        r = self.client.put(f"/api/widget/{kpi.id}/", json.dumps({"style": style}),
                            content_type="application/json")
        self.assertEqual(r.status_code, 200, r.content)
        kpi.refresh_from_db()
        self.assertEqual(kpi.style, style)

    def test_update_persiste_los_totales_por_nivel_de_la_tabla_dinamica(self, _df):
        table = Widget.objects.create(
            dashboard=self.dashboard, source=self.source, type="dynamic_table", title="Ventas",
            fields=fields(dimensions=["categoria", "mes"], pivots=["anio"]), style={},
        )
        style = {"showTotals": False, "rowSubtotal1": True, "rowSubtotal2": False,
                 "showColumnTotals": True, "columnSubtotal1": False, "repeatRowLabels": True,
                 "pageSize": 25, "showPagination": False, "boldLastRow": True}
        r = self.client.put(f"/api/widget/{table.id}/", json.dumps({"style": style}),
                            content_type="application/json")
        self.assertEqual(r.status_code, 200, r.content)
        table.refresh_from_db()
        self.assertEqual(table.style, style)

    def test_el_editor_incluye_el_panel_de_cada_tipo(self, _df):
        from sheets_reports.widgets import WIDGETS
        r = self.client.get(f"/tableros/{self.dashboard.id}/edit/")
        self.assertEqual(r.status_code, 200)
        html = r.content.decode()
        for key in WIDGETS.keys():
            with self.subTest(widget=key):
                self.assertIn(f"editingType === '{key}'", html)
        # Paneles propios: la meta del KPI y los totales por nivel de la tabla dinámica.
        self.assertIn("Valor objetivo", html)
        self.assertIn("['showTotals', 'rowSubtotal1', 'rowSubtotal2'][idx]", html)
        self.assertIn("drawerDraft.style.stacked", html)

    def test_update_rechaza_pivote_igual_a_dimension(self, _df):
        r = self.client.put(
            f"/api/widget/{self.widget.id}/",
            json.dumps({"fields": fields(pivots=["categoria"])}),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 422)
        self.widget.refresh_from_db()
        self.assertEqual(self.widget.fields["pivots"], [])

    def test_update_conserva_lo_que_no_se_envia(self, _df):
        r = self.client.put(f"/api/widget/{self.widget.id}/", json.dumps({"style": {"stacked": True}}),
                            content_type="application/json")
        self.assertEqual(r.status_code, 200, r.content)
        self.widget.refresh_from_db()
        self.assertEqual(self.widget.fields["dimensions"], ["categoria"])
        self.assertEqual(self.widget.style, {"stacked": True})

    def test_eliminar_widget(self, _df):
        r = self.client.delete(f"/api/widget/{self.widget.id}/")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Widget.objects.filter(id=self.widget.id).exists())

    def test_widget_de_otro_usuario_no_se_toca(self, _df):
        other = get_user_model().objects.create(username="otro")
        dashboard, source = make_board(other, nombre="Ajeno")
        widget = Widget.objects.create(dashboard=dashboard, source=source, type="bar", fields=fields(), style={})
        self.assertEqual(self.client.delete(f"/api/widget/{widget.id}/").status_code, 404)

    # ---------------------------------------------------------------- schema
    def test_schema_incluye_columnas_agrupables(self, _df):
        r = self.client.get(f"/api/sources/{self.source.id}/schema/")
        # anio es numérica pero con pocos valores enteros: se puede agrupar. ventas no.
        self.assertEqual(r.json()["dimension_fields"], ["categoria", "mes", "anio"])
        self.assertEqual(r.json()["sample_values"]["mes"], ["Ene", "Feb", "Mar"])
        # Columnas de tiempo (años, meses, fechas): admiten «el periodo más reciente».
        self.assertEqual(r.json()["time_fields"], ["mes", "anio", "ventas"])

    def test_schema_trae_el_manifiesto_con_las_columnas_de_la_hoja(self, _df):
        manifest = self.client.get(f"/api/sources/{self.source.id}/schema/").json()["widget_manifest"]
        self.assertEqual(manifest["bar"]["capabilities"], {
            "dimensions": [1, 1], "pivots": [0, 1], "metrics": [1, 5],
            "sort": True, "limit": True, "filters": True,
            "windows": ["percent_of_total", "percent_of_row", "running_total", "pct_change"],
        })
        self.assertEqual(manifest["bar"]["max_per_dashboard"], None)
        self.assertEqual(manifest["filter"]["max_per_dashboard"], 1)
        self.assertEqual(manifest["bar"]["style_defaults"]["stacked"], False)
        # La página del editor se arma con lo mismo, sin leer la hoja.
        page = self.client.get(f"/tableros/{self.dashboard.id}/edit/").context["widget_manifest"]
        self.assertEqual(page, manifest)

    # -------------------------------------------------------------- asistente
    def test_asistente_devuelve_el_form_sin_guardar(self, _df):
        proposal = {"widget_type": "dynamic_table", "title": "Ventas por categoría",
                    "fields": fields(pivots=["mes"]), "style": {"showTotals": True}}
        widgets_before = Widget.objects.count()
        with mock.patch("sheets_reports.services.ai_spec.generate_widget_form",
                        return_value=proposal) as ai:
            r = self.client.post(
                f"/api/dashboard/{self.dashboard.id}/table-assistant/",
                json.dumps({"prompt": "ventas por categoría"}),
                content_type="application/json",
            )
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(ai.call_args.args[:2], ("ventas por categoría", None))
        self.assertEqual(r.json(), {"widget_type": "dynamic_table", "title": "Ventas por categoría",
                                    "fields": fields(pivots=["mes"]), "style": {"showTotals": True}})
        self.assertEqual(Widget.objects.count(), widgets_before)

    def test_asistente_recibe_el_borrador_y_el_historial(self, _df):
        proposal = {"widget_type": "bar", "title": "Ventas", "fields": fields(), "style": {}}
        current = {"title": "Ventas", "fields": fields(), "style": {"stacked": True}, "otra": 1}
        history = [{"role": "user", "text": "ventas por categoría"},
                   {"role": "assistant", "proposal": proposal},
                   {"role": "assistant", "error": "falló"},   # sin propuesta: no se envía
                   {"role": "sistema", "text": "x"}]
        with mock.patch("sheets_reports.services.ai_spec.generate_widget_form",
                        return_value=proposal) as ai:
            r = self.client.post(
                f"/api/dashboard/{self.dashboard.id}/table-assistant/",
                json.dumps({"prompt": "ahora apilado", "widget_type": "bar",
                            "current": current, "history": history}),
                content_type="application/json",
            )
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(ai.call_args.kwargs["current"],
                         {"title": "Ventas", "fields": fields(), "style": {"stacked": True}})
        self.assertEqual(ai.call_args.kwargs["history"],
                         [{"role": "user", "text": "ventas por categoría"},
                          {"role": "assistant", "proposal": proposal}])

    def test_sugerencias_del_chat_por_tipo(self, _df):
        with mock.patch("sheets_reports.services.ai_suggestions.widget_suggestions",
                        return_value=["Ventas de Hogar vs total", "Total de ventas"]) as suggest:
            r = self.client.get(f"/api/dashboard/{self.dashboard.id}/widget-suggestions/?widget_type=kpi")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json(), {"suggestions": ["Ventas de Hogar vs total", "Total de ventas"]})
        widget, df, source = suggest.call_args.args
        self.assertEqual(widget.key, "kpi")
        self.assertIn("categoria", df.columns)
        self.assertEqual(source, "abc123:0")

    def test_otras_ideas_pasan_refresh_y_las_que_evitar(self, _df):
        with mock.patch("sheets_reports.services.ai_suggestions.widget_suggestions",
                        return_value=["C", "D"]) as suggest:
            r = self.client.get(f"/api/dashboard/{self.dashboard.id}/widget-suggestions/"
                                "?widget_type=kpi&refresh=1&avoid=A&avoid=B")
        self.assertEqual(r.json(), {"suggestions": ["C", "D"]})
        self.assertEqual(suggest.call_args.kwargs, {"refresh": True, "avoid": ["A", "B"]})

    def test_sugerencias_tipo_desconocido_u_otro_tablero(self, _df):
        with mock.patch("sheets_reports.services.ai_suggestions.widget_suggestions") as suggest:
            unknown = self.client.get(f"/api/dashboard/{self.dashboard.id}/widget-suggestions/?widget_type=x")
            missing = self.client.get(f"/api/dashboard/{self.dashboard.id}/widget-suggestions/")
            other = self.client.get("/api/dashboard/9999/widget-suggestions/?widget_type=kpi")
        suggest.assert_not_called()
        self.assertEqual((unknown.status_code, missing.status_code, other.status_code), (400, 400, 404))

    def test_asistente_error_legible(self, _df):
        with mock.patch("sheets_reports.services.ai_spec.generate_widget_form",
                        side_effect=SpecGenerationError("La columna Precio no existe.")):
            r = self.client.post(
                f"/api/dashboard/{self.dashboard.id}/table-assistant/",
                json.dumps({"prompt": "precio promedio"}),
                content_type="application/json",
            )
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.json()["error"], "La columna Precio no existe.")

    def test_asistente_sin_prompt(self, _df):
        r = self.client.post(f"/api/dashboard/{self.dashboard.id}/table-assistant/",
                             json.dumps({"prompt": " "}), content_type="application/json")
        self.assertEqual(r.status_code, 400)

    # -------------------------------------------------------------- páginas
    def test_paginas_renderizan(self, _df):
        for url in ("/", f"/tableros/{self.dashboard.id}/edit/", f"/tableros/{self.dashboard.id}/shared/"):
            r = self.client.get(url)
            self.assertEqual(r.status_code, 200, url)
        r = self.client.get(f"/tableros/{self.dashboard.id}/edit/")
        self.assertNotContains(r, "REFRESH_MINUTES")
        self.assertContains(r, "donut-widget.js")
        self.assertContains(r, 'id="module-rail"')
        self.assertContains(r, "tableia:rail-collapsed")
        # Header del editor: salir, nombre editable, modos y compartir.
        self.assertContains(r, 'aria-label="Salir del editor"')
        self.assertContains(r, 'id="board-title"')
        self.assertContains(r, 'id="preview-btn"')
        self.assertContains(r, 'id="share-btn"')
        self.assertContains(self.client.get("/"), "Tableros IA")


class DashboardCrudTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create(username="ana")
        self.client.force_login(self.user)
        self.dashboard, self.source = make_board(self.user)

    def test_lista_solo_los_propios(self):
        make_board(get_user_model().objects.create(username="pepe"), nombre="Ajeno")
        r = self.client.get("/api/dashboards/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual([d["nombre"] for d in r.json()], ["Ventas"])
        self.assertEqual(r.json()[0]["cardCount"], 0)
        self.assertEqual(r.json()[0]["sources"], ["abc… · gid 0"])

    def test_crear_validando_campos(self):
        r = self.client.post("/api/dashboards/", json.dumps({"nombre": "Nueva", "sheet_id": "abc"}),
                             content_type="application/json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()["nombre"], "Nueva")
        self.assertEqual(self.client.post("/api/dashboards/", json.dumps({"nombre": "", "sheet_id": "abc"}),
                                          content_type="application/json").status_code, 400)
        self.assertEqual(self.client.post("/api/dashboards/", json.dumps({"nombre": "x", "sheet_id": ""}),
                                          content_type="application/json").status_code, 400)

    def test_editar_y_borrar(self):
        r = self.client.put(f"/api/dashboards/{self.dashboard.id}/",
                            json.dumps({"nombre": "Renombrado"}), content_type="application/json")
        self.assertEqual(r.status_code, 200)
        self.dashboard.refresh_from_db()
        self.assertEqual(self.dashboard.nombre, "Renombrado")
        self.assertEqual(self.client.put(f"/api/dashboards/{self.dashboard.id}/",
                                         json.dumps({"nombre": "  "}), content_type="application/json").status_code, 400)
        r = self.client.delete(f"/api/dashboards/{self.dashboard.id}/")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Dashboard.objects.filter(id=self.dashboard.id).exists())

    def test_duplica_con_sus_fuentes_y_widgets(self):
        self.source.name, self.source.first_row_headers = "Mis ventas", False
        self.source.save()
        Widget.objects.create(dashboard=self.dashboard, source=self.source, type="bar", title="Ventas",
                              fields=fields(), style={"stacked": True})
        r = self.client.post(f"/api/dashboards/{self.dashboard.id}/duplicate/", {},
                             content_type="application/json")
        self.assertEqual(r.status_code, 201, r.content)
        copy = Dashboard.objects.get(id=r.json()["id"])
        self.assertEqual(copy.nombre, "Ventas (copia)")
        widget = copy.widgets.get()
        self.assertEqual((widget.type, widget.title, widget.style), ("bar", "Ventas", {"stacked": True}))
        # El widget de la copia usa la copia de la fuente, no la del tablero original.
        self.assertEqual(widget.source, copy.sources.get())
        self.assertNotEqual(widget.source_id, self.source.id)
        self.assertEqual((widget.source.name, widget.source.first_row_headers), ("Mis ventas", False))

    def test_no_toca_tableros_ajenos(self):
        other, _ = make_board(get_user_model().objects.create(username="pepe"), nombre="Ajeno")
        self.assertEqual(self.client.get(f"/api/dashboards/{other.id}/").status_code, 404)
        self.assertEqual(self.client.delete(f"/api/dashboards/{other.id}/").status_code, 404)

class SinUsuarioTests(TestCase):
    def test_lista_explica_como_crear_usuario(self):
        r = self.client.get("/api/dashboards/")
        self.assertEqual(r.status_code, 401)
        self.assertIn("createsuperuser", r.json()["error"])

    def test_home_siempre_muestra_crear_tablero(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Crear tablero")
        self.assertContains(r, "/tableros/nuevo/")
