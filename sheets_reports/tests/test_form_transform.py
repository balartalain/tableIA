"""Pipeline de transformación para respuestas de Google Forms (`services/form_transform.py`):
clasificación de columnas (`analyze`), transformación al modelo ancho con casillas expandidas
(`transform`) e integración con la fuente (`DataSource.is_form_response`)."""
import json
from unittest import mock

import pandas as pd
from django.contrib.auth import get_user_model
from django.core.cache import caches
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils.timezone import now

from sheets_reports.models import Dashboard
from sheets_reports.services import sheets
from sheets_reports.services.form_transform import analyze, transform, to_tidy
from sheets_reports.tests.fixtures import make_board

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"},
          "sheets": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache",
                     "LOCATION": "sheets-tidy-tests", "TIMEOUT": None}}


def form_df() -> pd.DataFrame:
    """Una hoja de respuestas de Google Forms (cruda, `dtype=str`): casillas,
    casilla con opciones numéricas, grid de opción múltiple (con un
    encabezado duplicado «.1»), grid de casillas y texto libre con comas."""
    return pd.DataFrame({
        "Marca de tiempo": [f"2026-10-01 1{i}:00" for i in range(6)],
        "Nombre": ["Ana", "Luis", "Eva", "Pablo", "Sara", "Nico"],
        "Edad": ["30", "25", "40", "35", "28", "33"],
        # Casillas: muchas celdas con comas, pocas opciones, tokens cortos.
        "Sabores favoritos": ["Chocolate, Vainilla", "Vainilla",
                               "Chocolate, Fresa, Vainilla", "Chocolate",
                               "Vainilla, Fresa", "Chocolate"],
        # Casilla con opciones numéricas: «1, 2» son dos opciones, no el 12.
        "Puntuación": ["1, 2", "3", "2, 3", "1", "2", "3, 1"],
        # Grid de opción múltiple (una columna por aspecto; «.1» es un
        # encabezado duplicado de la hoja: pandas lo renombra así).
        "Evaluación [Limpieza]": ["Buena", "Excelente", "Regular", "Buena",
                                   "Excelente", "Regular"],
        "Evaluación [Precio]": ["Regular", "Alta", "Baja", "Regular", "Alta", "Baja"],
        "Evaluación [Precio].1": ["Alta", "Baja", "Regular", "Alta", "Baja", "Regular"],
        # Grid de casillas.
        "Actividades [Deporte]": ["Fútbol, Natación", "Fútbol", "Natación",
                                   "Fútbol, Baloncesto", "Natación", "Fútbol"],
        "Actividades [Música]": ["Piano", "Guitarra, Piano", "Guitarra",
                                  "Piano", "Guitarra, Piano", "Piano"],
        # Texto libre con comas: contexto, nunca se explosiona.
        "Comentarios": ["Me gustó, aunque caro", "Bien", "Muy bien, muy recomendable",
                        "Genial", "Regular, pero bien", "Perfecto"],
    })


def form_with_id_df() -> pd.DataFrame:
    """Respuestas con columna `ID` propia, celdas vacías en las preguntas
    complejas y una respondente (R4) que solo contestó las de contexto."""
    return pd.DataFrame({
        "_ID": ["R1", "R2", "R3", "R4"],
        "Marca de tiempo": [f"2026-10-01 1{i}:00" for i in range(4)],
        "Nombre": ["Ana", "Luis", "Eva", "Pablo"],
        "Sabores favoritos": ["Chocolate, Vainilla", "Chocolate, Vainilla",
                               "Vainilla, Chocolate", None],
        "Evaluación [Limpieza]": ["Buena", None, "Regular", None],
        "Evaluación [Precio]": [None, "Alta", None, None],
    })


class AnalyzeTests(SimpleTestCase):
    """Clasificación de columnas sobre el frame crudo."""

    def test_clasifica_casillas_y_texto_libre(self):
        report = {a["name"]: a for a in analyze(form_df())}
        self.assertEqual(report["Sabores favoritos"]["inferred_type"], "CHECKBOX")
        self.assertEqual(report["Puntuación"]["inferred_type"], "CHECKBOX")
        self.assertEqual(report["Comentarios"]["inferred_type"], "FREE_TEXT_WITH_COMMAS")
        self.assertEqual(report["Nombre"]["inferred_type"], "SCALAR")
        self.assertEqual(report["Edad"]["inferred_type"], "SCALAR")

    def test_detecta_grid_y_grid_de_casillas(self):
        report = {a["name"]: a for a in analyze(form_df())}
        self.assertEqual(report["Evaluación [Limpieza]"]["inferred_type"], "GRID")
        self.assertEqual(report["Evaluación [Precio]"]["inferred_type"], "GRID")
        self.assertEqual(report["Actividades [Deporte]"]["inferred_type"], "CHECKBOX_GRID")
        self.assertEqual(report["Actividades [Música]"]["inferred_type"], "CHECKBOX_GRID")

    def test_encabezados_duplicados_se_agrupan_al_padre(self):
        report = {a["name"]: a for a in analyze(form_df())}
        duplicated = report["Evaluación [Precio].1"]["details"]
        self.assertEqual((duplicated["parent"], duplicated["aspect"]),
                         ("Evaluación", "Precio"))
        self.assertEqual(report["Evaluación [Precio]"]["details"]["parent"],
                         "Evaluación")

    def test_confianza_y_marca_de_revision(self):
        report = {a["name"]: a for a in analyze(form_df())}
        checkbox = report["Sabores favoritos"]
        self.assertGreater(checkbox["confidence"], 0)
        self.assertLessEqual(checkbox["confidence"], 1)
        # Texto libre con comas que cumplió parcialmente: revisión manual.
        self.assertTrue(report["Comentarios"]["details"]["needs_review"])
        self.assertGreaterEqual(report["Comentarios"]["details"]["pct_commas"], 0.05)

    def test_id_queda_fuera(self):
        report = {a["name"] for a in analyze(form_df())}
        self.assertNotIn("_ID", report)

    def test_separador_personalizado(self):
        df = pd.DataFrame({"N": ["a", "b"],
                           "Pregunta {A]": ["x", "y"],
                           "Pregunta {B]": ["z", "w"]})
        report = {a["name"]: a for a in analyze(df, custom_separator=" {")}
        self.assertEqual(report["Pregunta {A]"]["inferred_type"], "GRID")
        self.assertEqual(report["Pregunta {A]"]["details"]["parent"], "Pregunta")


class ToTidyTests(SimpleTestCase):
    """Transformación al modelo ancho con casillas expandidas."""

    def test_orden_y_columnas_del_modelo(self):
        tidy = to_tidy(form_df())
        esperadas = [
            "_ID", "Marca de tiempo", "Nombre", "Edad",
            "Sabores favoritos_Chocolate", "Sabores favoritos_Vainilla", "Sabores favoritos_Fresa",
            "Puntuación_1", "Puntuación_2", "Puntuación_3",
            "Evaluación [Limpieza]", "Evaluación [Precio]", "Evaluación [Precio].1",
            "Actividades [Deporte]_Fútbol", "Actividades [Deporte]_Natación", "Actividades [Deporte]_Baloncesto",
            "Actividades [Música]_Piano", "Actividades [Música]_Guitarra",
            "Comentarios",
        ]
        self.assertEqual(list(tidy.columns), esperadas)

    def test_filas_se_mantiene_una_por_respuesta(self):
        tidy = to_tidy(form_df())
        self.assertEqual(len(tidy), 6)
        self.assertEqual(sorted(tidy["_ID"].unique()), [1, 2, 3, 4, 5, 6])

    def test_id_existente_se_reutiliza(self):
        tidy = to_tidy(form_with_id_df())
        self.assertEqual(sorted(tidy["_ID"].unique()), ["R1", "R2", "R3", "R4"])
        self.assertEqual(len(tidy), 4)

    def test_una_coma_entre_parentesis_no_separa_opciones(self):
        from sheets_reports.services.form_transform import _split_tokens
        self.assertEqual(
            _split_tokens("Investigación, Comunicación (WhatsApp, correo, etc.), Trabajo"),
            ["Investigación", "Comunicación (WhatsApp, correo, etc.)", "Trabajo"])
        self.assertEqual(_split_tokens("A [x, y] , B,, C"), ["A [x, y]", "B", "C"])
        # Un paréntesis sin cerrar no se traga el resto de la celda más allá de lo inevitable.
        self.assertEqual(_split_tokens("A), B"), ["A)", "B"])
        self.assertEqual(_split_tokens(None), [])

    def test_las_casillas_valen_si_o_no(self):
        tidy = to_tidy(form_df())
        self.assertEqual(marks(tidy["Sabores favoritos_Chocolate"]), [1, 0, 1, 1, 0, 1])
        self.assertEqual(marks(tidy["Sabores favoritos_Vainilla"]), [1, 1, 1, 0, 1, 0])
        self.assertEqual(marks(tidy["Sabores favoritos_Fresa"]), [0, 0, 1, 0, 1, 0])
        self.assertEqual(set(tidy["Sabores favoritos_Fresa"]), {"Sí", "No"})

    def test_opciones_numericas_se_expanden_como_columnas(self):
        tidy = to_tidy(form_df())
        self.assertIn("Puntuación_1", tidy.columns)
        self.assertIn("Puntuación_2", tidy.columns)
        self.assertIn("Puntuación_3", tidy.columns)
        self.assertEqual(marks(tidy["Puntuación_1"]), [1, 0, 0, 1, 0, 1])

    def test_grid_se_mantiene_como_columna_ancha(self):
        tidy = to_tidy(form_df())
        self.assertIn("Evaluación [Limpieza]", tidy.columns)
        self.assertIn("Evaluación [Precio]", tidy.columns)
        self.assertEqual(tidy["Evaluación [Limpieza]"].tolist(),
                         ["Buena", "Excelente", "Regular", "Buena", "Excelente", "Regular"])

    def test_grid_de_casillas_se_expande_en_binarias(self):
        tidy = to_tidy(form_df())
        self.assertIn("Actividades [Deporte]_Fútbol", tidy.columns)
        self.assertIn("Actividades [Deporte]_Natación", tidy.columns)
        self.assertEqual(marks(tidy["Actividades [Deporte]_Fútbol"]), [1, 1, 0, 1, 0, 1])

    def test_texto_libre_con_comas_es_contexto(self):
        tidy = to_tidy(form_df())
        self.assertIn("Comentarios", tidy.columns)

    def test_marca_de_tiempo_se_conserva(self):
        tidy = to_tidy(form_df())
        self.assertEqual(tidy["Marca de tiempo"].unique().tolist()[:2],
                         ["2026-10-01 10:00", "2026-10-01 11:00"])


def marks(series: pd.Series) -> list:
    """Una columna de casilla («Sí»/«No») como unos y ceros, para leer los tests de un vistazo."""
    return [{"Sí": 1, "No": 0}[v] for v in series]


def seed_sheet_cache(sheet_id: str, gid: str, raw: pd.DataFrame,
                     headers: bool = True):
    """Deja la hoja cruda en el caché, como si se acabara de leer de Google."""
    entry = {"raw_df": raw, "fetched_at": now()}
    caches["sheets"].set(sheets._cache_key(sheet_id, gid, headers), entry, None)
    caches["sheets"].set(sheets._stamp_key(sheet_id, gid, headers), entry["fetched_at"], None)


@override_settings(CACHES=LOCMEM)
class TidySourceIntegrationTests(TestCase):
    """`load_source`, la caché tidy y los endpoints de la fuente."""

    def setUp(self):
        self.user = get_user_model().objects.create_superuser("admin", "a@a.com", "x")
        self.client.force_login(self.user)
        caches["sheets"].clear()

    def test_load_source_sirve_el_frame_tidy(self):
        seed_sheet_cache("form1", "0", form_df())
        _, source = make_board(self.user, sheet_id="form1", is_form_response=True)
        df = sheets.load_source(source)
        self.assertIn("Sabores favoritos_Chocolate", df.columns)
        self.assertIn("Evaluación [Limpieza]", df.columns)
        self.assertEqual(df["_ID"].nunique(), 6)
        self.assertEqual(len(df), 6)

    def test_el_transformado_convierte_los_numeros_despues_de_expandir(self):
        seed_sheet_cache("form1", "0", form_df())
        _, source = make_board(self.user, sheet_id="form1", is_form_response=True)
        df = sheets.load_source(source)
        self.assertTrue(pd.api.types.is_numeric_dtype(df["Edad"]))
        # La casilla «1, 2» se expandió como dos opciones antes de convertir: no es el 12.
        self.assertIn("Puntuación_1", df.columns)
        self.assertNotIn("Puntuación", df.columns)

    def test_una_casilla_se_agrupa_y_se_cuenta_como_una_pregunta_de_si_o_no(self):
        from sheets_reports.engine.context import SheetContext
        from sheets_reports.services.ai_spec import columns_context
        from sheets_reports.services.sheets import get_field_samples
        from sheets_reports.tests.fixtures import execute, fields
        seed_sheet_cache("form1", "0", form_df())
        _, source = make_board(self.user, sheet_id="form1", is_form_response=True)
        df = sheets.load_source(source)
        column = "Sabores favoritos_Chocolate"
        self.assertFalse(pd.api.types.is_numeric_dtype(df[column]))   # texto: no se suma
        # Reparto: agrupar por la opción y contar.
        out = execute(df, fields(dimensions=[column], metrics=[{"agg": "count", "alias": "respuestas"}]))
        self.assertEqual({row[column]: row["respuestas"] for row in out["data"]}, {"Sí": 4, "No": 2})
        # Cuántos la marcaron: conteo con la condición «es igual a Sí».
        out = execute(df, fields(dimensions=[], metrics=[
            {"agg": "count", "alias": "marcaron", "filters": [{"field": column, "op": "eq", "value": "Sí"}]},
            {"agg": "count", "alias": "respuestas"}]))
        self.assertEqual(out["data"]["values"], {"marcaron": 4, "respuestas": 6})
        # La IA la ve como texto con sus dos valores, escritos una sola vez para todas las opciones.
        context = columns_context(SheetContext.from_dataframe(df, "0", samples=get_field_samples(df)))
        self.assertEqual(context.count('["Sí", "No"]'), 1)
        self.assertIn(f'"{column}" (texto) — valores de ejemplo: los de V', context)

    def test_el_crudo_solo_se_lee_para_derivar(self):
        seed_sheet_cache("form1", "0", form_df())
        with mock.patch.object(sheets, "_base", side_effect=sheets._base) as base:
            sheets.get_transformed_dataframe("form1", "0")
            sheets.get_sheet_dataframe("form1", "0")
            self.assertEqual(base.call_count, 2)   # una vez por derivado
            sheets.get_transformed_dataframe("form1", "0")
            sheets.get_sheet_dataframe("form1", "0")
            self.assertEqual(base.call_count, 2)   # ya en caché: el crudo no se toca

    def test_fuente_normal_no_se_transforma(self):
        seed_sheet_cache("form1", "0", form_df())
        _, source = make_board(self.user, sheet_id="form1")
        df = sheets.load_source(source)
        self.assertIn("Sabores favoritos", df.columns)
        self.assertNotIn("Sabores favoritos_Chocolate", df.columns)

    def test_la_columna_de_envio_queda_marcada_para_la_ia(self):
        from sheets_reports.engine.context import SheetContext
        from sheets_reports.services.ai_spec import SUBMISSION_NOTE, columns_context
        from sheets_reports.services.ai_suggestions import _sheet_preview
        seed_sheet_cache("form1", "0", form_df())
        _, source = make_board(self.user, sheet_id="form1", is_form_response=True)
        df = sheets.load_source(source)
        self.assertEqual(df.attrs["submission_field"], "Marca de tiempo")
        context = columns_context(SheetContext.from_dataframe(df, "0"))
        marked = [line for line in context.splitlines() if SUBMISSION_NOTE in line]
        self.assertEqual(len(marked), 1)
        self.assertIn('"Marca de tiempo"', marked[0])
        self.assertIn(f'- "Marca de tiempo" ({SUBMISSION_NOTE})', _sheet_preview(df))

        # Con otro nombre a mostrar la sigue; excluida, no hay columna de envío.
        source.columns = [{"name": "Marca de tiempo", "type": "date", "include": True, "label": "Enviado"}]
        source.save()
        self.assertEqual(sheets.load_source(source).attrs["submission_field"], "Enviado")
        source.columns = [{"name": "Marca de tiempo", "type": "date", "include": False}]
        source.save()
        self.assertIsNone(sheets.load_source(source).attrs["submission_field"])

    def test_una_hoja_normal_no_tiene_columna_de_envio(self):
        seed_sheet_cache("form1", "0", form_df())
        _, source = make_board(self.user, sheet_id="form1")
        self.assertIsNone(sheets.load_source(source).attrs.get("submission_field"))

    def test_el_tidy_se_cachea_por_pestaña(self):
        seed_sheet_cache("form1", "0", form_df())
        _, source = make_board(self.user, sheet_id="form1", is_form_response=True)
        with mock.patch.object(sheets.form_transform, "transform",
                               side_effect=sheets.form_transform.transform) as tr:
            sheets.get_transformed_dataframe("form1", "0")
            sheets.get_transformed_dataframe("form1", "0")
            self.assertEqual(tr.call_count, 1)  # segunda vez, caché
            source.columns = [{"name": "Nombre", "type": "text",
                               "include": False, "label": "N. completo"}]
            source.save()
            sheets.get_transformed_dataframe("form1", "0")
            self.assertEqual(tr.call_count, 1)

    def test_actualizar_datos_recalcula_el_tidy(self):
        seed_sheet_cache("form1", "0", form_df())
        _, source = make_board(self.user, sheet_id="form1", is_form_response=True)
        with mock.patch.object(sheets.form_transform, "transform",
                               side_effect=sheets.form_transform.transform) as tr, \
             mock.patch.object(sheets, "fetch_sheet_dataframe",
                               return_value=form_df()):
            sheets.get_transformed_dataframe("form1", "0")
            sheets.refresh_sheet("form1", "0")
            sheets.get_transformed_dataframe("form1", "0")
            self.assertEqual(tr.call_count, 2)

    def test_la_config_de_columnas_manda_sobre_el_tidy(self):
        """Incluir, excluir y renombrar se aplica al tidy, igual que a una hoja."""
        seed_sheet_cache("form1", "0", form_df())
        _, source = make_board(self.user, sheet_id="form1", is_form_response=True,
                               columns=[
                                   {"name": "Comentarios", "type": "text",
                                    "include": False},
                                   {"name": "Nombre", "type": "text",
                                    "include": True, "label": "Nombre completo"},
                               ])
        df = sheets.load_source(source)
        self.assertNotIn("Comentarios", df.columns)
        self.assertIn("Nombre completo", df.columns)
        self.assertNotIn("Nombre", df.columns)
        self.assertIn("Sabores favoritos_Chocolate", df.columns)

    def test_columnas_de_edicion_son_las_del_tidy(self):
        seed_sheet_cache("form1", "0", form_df())
        _, source = make_board(self.user, sheet_id="form1", is_form_response=True)
        data = self.client.get(f"/api/sources/{source.id}/columns/").json()
        self.assertIn("Sabores favoritos_Chocolate", [c["name"] for c in data["columns"]])
        self.assertIn("Evaluación [Limpieza]", [c["name"] for c in data["columns"]])
        self.assertEqual(data["rows"], 6)
        for column in data["columns"]:
            self.assertNotIn("question_type", column)
            self.assertNotIn("confidence", column)

    def test_endpoint_de_columnas_sirve_el_tidy_con_form(self):
        seed_sheet_cache("form1", "0", form_df())
        data = self.client.get(
            "/api/sources/google/spreadsheets/form1/tabs/0/columns/?form=1").json()
        names = [c["name"] for c in data["columns"]]
        self.assertIn("Sabores favoritos_Chocolate", names)
        self.assertIn("Evaluación [Limpieza]", names)
        self.assertNotIn("Sabores favoritos", names)  # se reemplazó
        # Sin «form» se sirven las columnas de la hoja ancha.
        data = self.client.get(
            "/api/sources/google/spreadsheets/form1/tabs/0/columns/").json()
        self.assertIn("Sabores favoritos",
                      [c["name"] for c in data["columns"]])

    def test_schema_de_formulario_sirve_el_tidy(self):
        seed_sheet_cache("form1", "0", form_df())
        _, source = make_board(self.user, sheet_id="form1", is_form_response=True)
        schema = self.client.get(f"/api/sources/{source.id}/schema/").json()
        self.assertIn("Sabores favoritos_Chocolate", schema["all_fields"])
        self.assertIn("Evaluación [Limpieza]", schema["all_fields"])
        self.assertIn("Nombre", schema["all_fields"])  # contexto
        self.assertNotIn("Sabores favoritos", schema["all_fields"])  # se reemplazó

    def test_put_guarda_is_form_response_y_columnas_del_tidy(self):
        seed_sheet_cache("form1", "0", form_df())
        _, source = make_board(self.user, sheet_id="form1")
        r = self.client.put(f"/api/sources/{source.id}/", json.dumps({
            "is_form_response": True,
            "columns": [{"name": "Comentarios", "type": "text",
                         "include": False},
                        {"name": "Nombre", "type": "text",
                         "include": True, "label": "Nombre completo"}],
            # Se valida sobre el modelo tidy: «ID» existe allí.
            "calculated_fields": [{"id": "c1", "name": "Respuestas",
                                   "formula": "COUNT_DISTINCT([_ID])",
                                   "format": "number"}]}),
            content_type="application/json")
        self.assertEqual(r.status_code, 200, r.content)
        source.refresh_from_db()
        self.assertTrue(source.is_form_response)
        self.assertTrue(r.json()["is_form_response"])
        df = sheets.load_source(source)
        self.assertNotIn("Comentarios", df.columns)
        self.assertIn("Nombre completo", df.columns)
        self.assertIn("Respuestas", df.attrs["aggregated_fields"])

    def test_vista_previa_de_formula_sobre_el_tidy(self):
        seed_sheet_cache("form1", "0", form_df())
        _, source = make_board(self.user, sheet_id="form1", is_form_response=True)
        data = self.client.post(f"/api/sources/{source.id}/formula/",
                                json.dumps({"formula": "COUNT_DISTINCT([_ID])"}),
                                content_type="application/json").json()
        self.assertEqual(data["kind"], "aggregated")
        self.assertEqual(data["values"], [6])

    def test_crear_tablero_con_is_form_response(self):
        r = self.client.post("/api/dashboards/", json.dumps({
            "nombre": "Encuesta", "sheet_id": "form1", "sheet_gid": "0",
            "is_form_response": True}), content_type="application/json")
        self.assertEqual(r.status_code, 201, r.content)
        source = Dashboard.objects.get(id=r.json()["id"]).sources.get()
        self.assertTrue(source.is_form_response)
