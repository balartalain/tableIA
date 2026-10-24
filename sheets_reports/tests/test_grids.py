"""Cuadrículas de Google Forms como columnas virtuales (`engine/steps/grid.py`): su formato largo
(`form_transform.grid_long`), la fuente que las ofrece (`sheets.attach_grids`), el orden de la
consulta (filtros en ancho → desdoblar → filtros sobre las virtuales → agregar), la validación,
los filtros del tablero y el editor de la fuente."""
from unittest import mock

import pandas as pd
from django.contrib.auth import get_user_model
from django.core.cache import caches
from django.test import SimpleTestCase, TestCase, override_settings

from sheets_reports.engine.context import SheetContext
from sheets_reports.engine.formulas import apply_calculated_fields
from sheets_reports.engine.steps.filter import parse_conditions
from sheets_reports.models import Widget
from sheets_reports.services import sheets
from sheets_reports.services.ai_spec import columns_context, form_errors
from sheets_reports.services.form_transform import grid_long, transform
from sheets_reports.services.widget_service import WidgetService
from sheets_reports.tests.fixtures import execute, fields, make_board
from sheets_reports.widgets import WIDGETS

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"},
          "sheets": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache",
                     "LOCATION": "sheets-grid-tests", "TIMEOUT": None}}

ASPECT, VALUE = "Evaluación · Aspecto", "Evaluación · Respuesta"


def survey_df() -> pd.DataFrame:
    """Respuestas crudas (`dtype=str`): una cuadrícula de opción múltiple (Evaluación), una de
    escala numérica (Nota), una de casillas (Temas) y una columna de contexto (Carrera)."""
    return pd.DataFrame({
        "Carrera": ["Derecho", "Derecho", "Derecho", "Medicina", "Medicina"],
        "Evaluación [Contenido]": ["Bueno", "Malo", "Bueno", "Bueno", "Regular"],
        "Evaluación [Profesor]": ["Malo", "Malo", "Bueno", None, "Bueno"],
        "Evaluación [Materiales]": ["Bueno", "Regular", "Regular", "Bueno", "Bueno"],
        "Nota [Clase]": ["4", "2", "5", "3", "4"],
        "Nota [Examen]": ["3", "1", "5", "5", "4"],
        "Temas [Lunes]": ["Álgebra, Física", "Física", "Álgebra", "", "Física"],
        "Temas [Martes]": ["Física", "Álgebra, Física", "Física", "Álgebra", ""],
    }, dtype=object)


def survey_source_df(columns=None) -> pd.DataFrame:
    """La fuente como la sirve `load_source`: el frame transformado, configurado y con sus
    cuadrículas."""
    raw = sheets._coerce_numeric_columns(transform(survey_df()))
    out = sheets.apply_column_config(raw.copy(), columns or [])
    sheets.attach_grids(out, raw, columns or [])
    return out


def count(alias="n"):
    return {"agg": "count", "alias": alias}


def cells(rows, dimension, metric="n") -> dict:
    """{(fila, pivote): valor} de una tabla dinámica, sin las celdas vacías ni los totales."""
    out = {}
    for row in rows:
        for key, value in row.items():
            if key.startswith("__pivots.") and key.endswith(f".{metric}") and value is not None:
                out[(row[dimension], key[len("__pivots."):-len(metric) - 1])] = value
    return out


def dynamic_table(df, fields_data):
    rendered = WIDGETS.get("dynamic_table").render(df, {"fields": fields_data, "style": {}})
    assert not rendered.get("error"), rendered.get("error")
    return rendered["render_data"]["rows"]


class GridLongTests(SimpleTestCase):
    def test_transform_anota_las_cuadriculas(self):
        grids = transform(survey_df()).attrs["grids"]
        self.assertEqual(set(grids), {"Evaluación", "Nota", "Temas"})
        self.assertEqual(grids["Evaluación"]["kind"], "GRID")
        self.assertEqual([a for a, _ in grids["Evaluación"]["aspects"]], ["Contenido", "Profesor", "Materiales"])
        self.assertEqual(grids["Temas"]["kind"], "CHECKBOX_GRID")
        self.assertEqual(grids["Temas"]["aspects"][0][1], {"Álgebra": "Temas [Lunes]_Álgebra",
                                                           "Física": "Temas [Lunes]_Física"})

    def test_una_fila_por_respuesta_y_aspecto_sin_las_vacias(self):
        df = transform(survey_df())
        long = grid_long(df, df.attrs["grids"]["Evaluación"])
        self.assertEqual(len(long), 14)          # 5 × 3 menos el Profesor sin respuesta
        self.assertEqual(long.head(3)["aspect"].tolist(), ["Contenido", "Profesor", "Materiales"])
        self.assertEqual(long.head(3)["value"].tolist(), ["Bueno", "Malo", "Bueno"])

    def test_casillas_una_fila_por_opcion_marcada(self):
        df = transform(survey_df())
        long = grid_long(df, df.attrs["grids"]["Temas"])
        first = long[long["row"] == 0]
        self.assertEqual(list(zip(first["aspect"], first["value"])),
                         [("Lunes", "Álgebra"), ("Lunes", "Física"), ("Martes", "Física")])


class CheckboxTests(SimpleTestCase):
    """Una pregunta de casillas es una cuadrícula en ancho: cada opción, un aspecto con Sí/No."""

    def source(self):
        from sheets_reports.tests.test_form_transform import form_df
        raw = sheets._coerce_numeric_columns(transform(form_df()))
        out = sheets.apply_column_config(raw.copy(), [])
        sheets.attach_grids(out, raw, [])
        return raw, out

    def test_la_casilla_es_una_cuadricula_de_opciones(self):
        raw, _ = self.source()
        grid = raw.attrs["grids"]["Sabores favoritos"]
        self.assertEqual(grid["kind"], "CHECKBOX")
        self.assertEqual(grid["aspects"][0], ["Chocolate", "Sabores favoritos_Chocolate"])
        # Las de cuadrícula de casillas siguen siendo una sola cuadrícula.
        self.assertNotIn("Actividades [Deporte]", raw.attrs["grids"])

    def test_el_editor_incluye_las_columnas_si_no(self):
        from sheets_reports.views import _sheet_columns
        raw, _ = self.source()
        columns = _sheet_columns(raw)
        names = [c["name"] for c in columns]
        start = names.index("Sabores favoritos · Opción")
        self.assertEqual(names[start:start + 3], ["Sabores favoritos · Opción", "Sabores favoritos · Respuesta",
                                                  "Sabores favoritos_Chocolate"])
        by_name = {c["name"]: c for c in columns}
        self.assertEqual(by_name["Sabores favoritos · Opción"]["kind"], "CHECKBOX")
        self.assertEqual(by_name["Sabores favoritos · Opción"]["samples"], ["Chocolate", "Vainilla", "Fresa"])
        self.assertTrue(by_name["Sabores favoritos_Chocolate"].get("include", True))
        self.assertEqual(by_name["Sabores favoritos_Chocolate"]["grid_kind"], "CHECKBOX")

    def test_una_formula_combina_opciones_de_la_misma_persona(self):
        # Las columnas Sí/No tienen una fila por respuesta: «marcó las dos» solo se puede ahí.
        _, df = self.source()
        df = apply_calculated_fields(df, [
            {"name": "Ambos", "formula": 'SUM(IF([Sabores favoritos_Chocolate] = "Sí" AND '
                                         '[Sabores favoritos_Vainilla] = "Sí", 1, 0))'},
            {"name": "Alguno", "formula": 'SUM(IF([Sabores favoritos_Chocolate] = "Sí" OR '
                                          '[Sabores favoritos_Vainilla] = "Sí", 1, 0))'}])
        out = execute(df, fields(dimensions=[], metrics=[
            {"field": "Ambos", "agg": "auto", "alias": "ambos"},
            {"field": "Alguno", "agg": "auto", "alias": "alguno"}]))
        self.assertEqual(out["data"]["values"], {"ambos": 2, "alguno": 6})

    def test_porcentaje_que_marco_cada_opcion(self):
        _, df = self.source()
        option, answer = "Sabores favoritos · Opción", "Sabores favoritos · Respuesta"
        metric = {**count("pct"), "window": {"type": "percent_of_row"}}
        rows = dynamic_table(df, fields(dimensions=[option], pivots=[answer], metrics=[count(), metric]))
        self.assertEqual(cells(rows, option)[("Chocolate", "Sí")], 4)
        self.assertAlmostEqual(cells(rows, option, "pct")[("Chocolate", "Sí")], 66.67)
        # Cuántos marcaron cada opción: el filtro Respuesta = Sí.
        out = execute(df, fields(dimensions=[option], metrics=[count()],
                                 filters=[{"field": answer, "op": "eq", "value": "Sí"}]))
        self.assertEqual({r[option]: r["n"] for r in out["data"]}, {"Chocolate": 4, "Vainilla": 4, "Fresa": 2})


class GridQueryTests(SimpleTestCase):
    def test_tabla_aspecto_por_respuesta(self):
        rows = dynamic_table(survey_source_df(), fields(dimensions=[ASPECT], pivots=[VALUE], metrics=[count()]))
        self.assertEqual([r[ASPECT] for r in rows if ASPECT in r][:3], ["Contenido", "Profesor", "Materiales"])
        self.assertEqual(cells(rows, ASPECT), {
            ("Contenido", "Bueno"): 3, ("Contenido", "Malo"): 1, ("Contenido", "Regular"): 1,
            ("Profesor", "Malo"): 2, ("Profesor", "Bueno"): 2,
            ("Materiales", "Bueno"): 3, ("Materiales", "Regular"): 2,
        })
        # El total de cada aspecto son sus respuestas (el Profesor tiene una vacía).
        totals = {r[ASPECT]: r["__total.n"] for r in rows if ASPECT in r}
        self.assertEqual(totals, {"Contenido": 5, "Profesor": 4, "Materiales": 5})

    def test_porcentaje_de_la_fila(self):
        metric = {**count("pct"), "window": {"type": "percent_of_row"}}
        rows = dynamic_table(survey_source_df(), fields(dimensions=[ASPECT], pivots=[VALUE], metrics=[metric]))
        profesor = cells(rows, ASPECT, "pct")
        self.assertAlmostEqual(profesor[("Profesor", "Malo")], 50.0)
        self.assertAlmostEqual(profesor[("Contenido", "Bueno")], 60.0)

    def test_un_filtro_real_se_aplica_en_ancho_antes_de_desdoblar(self):
        df = survey_source_df()
        out = execute(df, fields(dimensions=[ASPECT], metrics=[count()],
                                 filters=[{"field": "Carrera", "op": "eq", "value": "Derecho"}]))
        self.assertEqual({r[ASPECT]: r["n"] for r in out["data"]},
                         {"Contenido": 3, "Profesor": 3, "Materiales": 3})
        # Quienes dieron Malo al Profesor (su columna ancha): cómo evaluaron el Contenido.
        out = execute(df, fields(dimensions=[ASPECT, VALUE], metrics=[count()],
                                 filters=[{"field": "Evaluación [Profesor]", "op": "eq", "value": "Malo"},
                                          {"field": ASPECT, "op": "eq", "value": "Contenido"}]))
        self.assertEqual({r[VALUE]: r["n"] for r in out["data"]}, {"Bueno": 1, "Malo": 1})

    def test_un_filtro_sobre_la_virtual_se_aplica_despues_de_desdoblar(self):
        out = execute(survey_source_df(), fields(dimensions=[ASPECT], metrics=[count()],
                                                 filters=[{"field": VALUE, "op": "eq", "value": "Malo"}]))
        self.assertEqual({r[ASPECT]: r["n"] for r in out["data"]}, {"Contenido": 1, "Profesor": 2})

    def test_una_respuesta_numerica_se_promedia_por_aspecto(self):
        out = execute(survey_source_df(), fields(dimensions=["Nota · Aspecto"], metrics=[
            {"agg": "avg", "field": "Nota · Respuesta", "alias": "media"}]))
        self.assertEqual({r["Nota · Aspecto"]: r["media"] for r in out["data"]}, {"Clase": 3.6, "Examen": 3.6})

    def test_casillas_cuenta_personas_por_aspecto(self):
        rows = dynamic_table(survey_source_df(), fields(dimensions=["Temas · Aspecto"], pivots=["Temas · Respuesta"],
                                                        metrics=[count()]))
        self.assertEqual(cells(rows, "Temas · Aspecto"), {
            ("Lunes", "Álgebra"): 2, ("Lunes", "Física"): 3, ("Martes", "Física"): 3, ("Martes", "Álgebra"): 2})
        # Quienes marcaron algo ese día (una persona puede marcar dos opciones).
        totals = {r["Temas · Aspecto"]: r["__total.n"] for r in rows if "Temas · Aspecto" in r}
        self.assertEqual(totals, {"Lunes": 4, "Martes": 4})

    def test_barras_con_la_respuesta_como_serie(self):
        rendered = WIDGETS.get("bar").render(survey_source_df(), {"fields": fields(
            dimensions=[ASPECT], pivots=[VALUE], metrics=[count()]), "style": {}})
        self.assertFalse(rendered.get("error"))
        data = rendered["render_data"]
        self.assertEqual(data["categories"], ["Contenido", "Profesor", "Materiales"])
        self.assertEqual({s["name"] for s in data["series"]}, {"Bueno", "Malo", "Regular"})

    def test_dos_cuadriculas_en_un_widget_se_rechazan(self):
        rendered = WIDGETS.get("dynamic_table").render(survey_source_df(), {"fields": fields(
            dimensions=[ASPECT], pivots=["Nota · Respuesta"], metrics=[count()]), "style": {}})
        self.assertIn("una sola cuadrícula", rendered["error"])

    def test_sin_virtuales_la_fuente_se_consulta_como_siempre(self):
        out = execute(survey_source_df(), fields(dimensions=["Carrera"], metrics=[count()]))
        self.assertEqual({r["Carrera"]: r["n"] for r in out["data"]}, {"Derecho": 3, "Medicina": 2})


class GridConfigTests(SimpleTestCase):
    def test_una_columna_ancha_excluida_sigue_en_la_cuadricula(self):
        df = survey_source_df([{"name": "Evaluación [Profesor]", "type": "text", "include": False}])
        self.assertNotIn("Evaluación [Profesor]", df.columns)
        out = execute(df, fields(dimensions=[ASPECT], metrics=[count()]))
        self.assertIn("Profesor", {r[ASPECT] for r in out["data"]})

    def test_nombre_a_mostrar_y_virtual_excluida(self):
        df = survey_source_df([
            {"name": ASPECT, "type": "text", "grid": "Evaluación", "role": "aspect", "label": "Aspecto"},
            {"name": VALUE, "type": "text", "grid": "Evaluación", "role": "value", "include": False},
        ])
        schema = sheets.get_sheet_schema(df)
        self.assertIn("Aspecto", schema["all_fields"])
        self.assertNotIn(VALUE, schema["all_fields"])
        # Un aspecto se deja fuera de los resultados con un filtro en el widget.
        out = execute(df, fields(dimensions=["Aspecto"], metrics=[count()],
                                 filters=[{"field": "Aspecto", "op": "not_in", "value": ["Materiales"]}]))
        self.assertEqual({r["Aspecto"] for r in out["data"]}, {"Contenido", "Profesor"})

    def test_el_contexto_conoce_las_virtuales(self):
        ctx = SheetContext.from_dataframe(survey_source_df(), "0")
        self.assertIn(ASPECT, ctx.fields)
        self.assertNotIn(ASPECT, ctx.sheet_fields)
        self.assertIn("Nota · Respuesta", ctx.numeric_fields)
        self.assertEqual(ctx.grid_fields[VALUE]["question"], "Evaluación")
        self.assertIn(ASPECT, ctx.aliases.values())
        text = columns_context(ctx)
        self.assertIn("columna virtual de la cuadrícula «Evaluación»", text)
        self.assertIn("Cuadrículas y preguntas de casillas del formulario", text)
        # Los límites, con su alternativa: la IA no planifica lo que no se puede.
        self.assertIn("No se puede", text)
        self.assertIn("% de satisfechos o completamente satisfechos", text)
        # Sin virtuales, la nota no aparece.
        no_grids = survey_source_df()
        no_grids.attrs.pop("grid_store")
        self.assertNotIn("No se puede", columns_context(SheetContext.from_dataframe(no_grids, "0")))

    def test_el_plan_descarta_lo_que_las_virtuales_no_permiten(self):
        from sheets_reports.services.ai_board import clean_plan
        ctx = SheetContext.from_dataframe(survey_source_df(), "0")

        def item(widget_type, columns):
            return {"widget_type": widget_type, "title": widget_type, "description": "",
                    "request": "algo", "columns": columns}
        plan = clean_plan({"items": [
            item("dynamic_table", [ASPECT, VALUE, "Carrera"]),
            item("correlation", ["Nota · Respuesta", "Nota [Clase]"]),
            item("bar", [ASPECT, "Nota · Respuesta"]),
        ], "note": ""}, ctx)
        problems = [p["problem"] for p in plan["items"]]
        self.assertEqual(problems[0], "")
        self.assertIn("no usa columnas de cuadrícula", problems[1])
        self.assertIn("dos cuadrículas", problems[2])

    def test_validacion_de_las_virtuales(self):
        ctx = SheetContext.from_dataframe(survey_source_df(), "0")

        def errors(widget_type, data):
            return form_errors({"widget_type": widget_type, "fields": data, "style": {}, "title": "T"},
                               ctx, widget_type)
        self.assertEqual(errors("dynamic_table", fields(dimensions=[ASPECT], pivots=[VALUE], metrics=[count()])), [])
        self.assertTrue(any("una sola cuadrícula" in e for e in errors(
            "dynamic_table", fields(dimensions=[ASPECT], pivots=["Nota · Respuesta"], metrics=[count()]))))
        self.assertTrue(any("no usa columnas de cuadrícula" in e for e in errors(
            "correlation", {"columns": [{"field": "Nota · Respuesta"}, {"field": "Nota [Clase]"}]})))


@override_settings(CACHES=LOCMEM)
class GridSourceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("admin", "a@a.com", "x")
        self.client.force_login(self.user)
        caches["sheets"].clear()
        mock.patch.object(sheets, "fetch_sheet_dataframe", return_value=survey_df()).start()
        self.addCleanup(mock.patch.stopall)
        self.dashboard, self.source = make_board(self.user, sheet_id="form1", is_form_response=True)

    def test_el_editor_lista_las_virtuales_y_las_anchas_incluidas(self):
        r = self.client.get(f"/api/sources/google/spreadsheets/form1/tabs/0/columns/?form=1")
        columns = r.json()["columns"]
        names = [c["name"] for c in columns]
        # Las virtuales van justo antes de las columnas anchas de su pregunta.
        self.assertEqual(names[names.index(ASPECT):names.index(ASPECT) + 3],
                         [ASPECT, VALUE, "Evaluación [Contenido]"])
        by_name = {c["name"]: c for c in columns}
        self.assertTrue(by_name[ASPECT]["virtual"])
        self.assertEqual(by_name[ASPECT]["samples"], ["Contenido", "Profesor", "Materiales"])
        self.assertEqual(by_name["Nota · Respuesta"]["type"], "number")
        self.assertTrue(by_name["Evaluación [Contenido]"].get("include", True))
        self.assertTrue(by_name["Carrera"].get("include", True))
        # Los alias de las columnas reales no se corren por las virtuales.
        self.assertEqual(by_name["Carrera"]["alias"], "col_2")
        self.assertEqual(len({c["alias"] for c in columns}), len(columns))

    def test_el_editor_y_el_motor_dan_los_mismos_alias(self):
        columns = self.client.get(f"/api/sources/{self.source.id}/columns/").json()["columns"]
        ctx = SheetContext.from_dataframe(sheets.load_source(self.source), "0")
        engine = {field: alias for alias, field in ctx.aliases.items()}
        self.assertEqual({c["name"]: c["alias"] for c in columns if c["name"] in engine},
                         {name: engine[name] for name in engine if name in {c["name"] for c in columns}})

    def test_guardar_las_virtuales_en_la_fuente(self):
        columns = self.client.get(f"/api/sources/{self.source.id}/columns/").json()["columns"]
        for c in columns:
            if c["name"] == ASPECT:
                c.update(label="Aspecto evaluado")
        r = self.client.put(f"/api/sources/{self.source.id}/", data={"columns": columns},
                            content_type="application/json")
        self.assertEqual(r.status_code, 200, r.content)
        self.source.refresh_from_db()
        saved = next(c for c in self.source.columns if c["name"] == ASPECT)
        self.assertEqual((saved["grid"], saved["role"]), ("Evaluación", "aspect"))
        schema = self.client.get(f"/api/sources/{self.source.id}/schema/").json()
        self.assertIn("Aspecto evaluado", schema["all_fields"])
        self.assertIn("Evaluación [Contenido]", schema["all_fields"])
        self.assertIn({"question": "Evaluación", "kind": "GRID", "aspect": "Aspecto evaluado", "value": VALUE},
                      schema["grid_fields"])
        self.assertTrue(schema["widget_manifest"]["dynamic_table"]["grid_fields"])
        self.assertFalse(schema["widget_manifest"]["correlation"]["grid_fields"])

    def test_filtro_del_tablero_sobre_una_virtual(self):
        df = sheets.load_source(self.source)
        service = WidgetService(self.dashboard, df, self.source)
        board = parse_conditions([{"field": VALUE, "op": "eq", "value": "Malo"},
                                  {"field": "Carrera", "op": "eq", "value": "Derecho"}])
        grid = Widget(type="bar", fields=fields(dimensions=[ASPECT], metrics=[count()]), style={})
        out = service.render(grid, board)
        self.assertNotIn("error", out)
        self.assertEqual(dict(zip(out["data"]["categories"], out["data"]["series"][0]["data"])),
                         {"Contenido": 1, "Profesor": 2})
        # Un widget sin la cuadrícula solo recibe el filtro de la columna real.
        plain = Widget(type="bar", fields=fields(dimensions=["Carrera"], metrics=[count()]), style={})
        out = service.render(plain, board)
        self.assertEqual(dict(zip(out["data"]["categories"], out["data"]["series"][0]["data"])), {"Derecho": 3})
