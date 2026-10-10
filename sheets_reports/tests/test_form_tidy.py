"""Pipeline tidy para respuestas de Google Forms (`services/form_tidy.py`):
clasificación de columnas (`analyze`), transformación al modelo tidy
(`to_tidy`) e integración con la fuente (`DataSource.is_form_response`)."""
import json
from unittest import mock

import pandas as pd
from django.contrib.auth import get_user_model
from django.core.cache import caches
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils.timezone import now

from sheets_reports.models import Dashboard
from sheets_reports.services import sheets
from sheets_reports.services.form_tidy import analyze, to_tidy
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
        "ID": ["R1", "R2", "R3", "R4"],
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

    def test_id_y_marca_de_tiempo_quedan_fuera(self):
        report = {a["name"] for a in analyze(form_df())}
        self.assertNotIn("Marca de tiempo", report)
        self.assertNotIn("ID", report)

    def test_separador_personalizado(self):
        df = pd.DataFrame({"N": ["a", "b"],
                           "Pregunta {A]": ["x", "y"],
                           "Pregunta {B]": ["z", "w"]})
        report = {a["name"]: a for a in analyze(df, custom_separator=" {")}
        self.assertEqual(report["Pregunta {A]"]["inferred_type"], "GRID")
        self.assertEqual(report["Pregunta {A]"]["details"]["parent"], "Pregunta")


class ToTidyTests(SimpleTestCase):
    """Transformación al modelo tidy."""

    def test_orden_y_columnas_del_modelo(self):
        tidy = to_tidy(form_df())
        self.assertEqual(list(tidy.columns),
                         ["ID", "marca_tiempo", "Nombre", "Edad",
                          "Comentarios", "Pregunta", "Aspecto", "Respuesta"])

    def test_id_generado_se_repite_por_fila_explosionada(self):
        tidy = to_tidy(form_df())
        self.assertEqual(sorted(tidy["ID"].unique()), [1, 2, 3, 4, 5, 6])
        # Cada respondente aparece en varias filas (sus selecciones).
        self.assertGreater(len(tidy), tidy["ID"].nunique())

    def test_id_existente_se_reutiliza(self):
        tidy = to_tidy(form_with_id_df())
        self.assertEqual(sorted(tidy["ID"].unique()), ["R1", "R2", "R3"])
        # Se repite por fila explosionada: R1 tiene 2 sabores + 1 evaluación.
        self.assertEqual(len(tidy[tidy["ID"] == "R1"]), 3)

    def test_cuenta_distinct_id_son_los_respondentes(self):
        self.assertEqual(to_tidy(form_df())["ID"].nunique(), 6)
        # R4 no contestó ninguna pregunta compleja: desaparece.
        self.assertEqual(to_tidy(form_with_id_df())["ID"].nunique(), 3)

    def test_opciones_numericas_no_se_convierten_en_numero(self):
        tidy = to_tidy(form_df())
        puntuacion = tidy[tidy["Pregunta"] == "Puntuación"]["Respuesta"]
        self.assertEqual(sorted(puntuacion.unique()), ["1", "2", "3"])
        self.assertNotIn("12", puntuacion.unique())
        self.assertTrue((puntuacion.astype(str) == puntuacion).all())

    def test_despivote_de_grid(self):
        tidy = to_tidy(form_df())
        evaluacion = tidy[tidy["Pregunta"] == "Evaluación"]
        self.assertEqual(sorted(evaluacion["Aspecto"].unique()), ["Limpieza", "Precio"])
        # Limpieza: 6 respuestas; Precio: 6 + 6 (el «.1» duplicado).
        self.assertEqual(len(evaluacion[evaluacion["Aspecto"] == "Limpieza"]), 6)
        self.assertEqual(len(evaluacion[evaluacion["Aspecto"] == "Precio"]), 12)
        self.assertEqual(evaluacion["Respuesta"].tolist()[:6],
                         ["Buena", "Excelente", "Regular", "Buena", "Excelente", "Regular"])

    def test_despivote_de_grid_de_casillas(self):
        tidy = to_tidy(form_df())
        actividades = tidy[tidy["Pregunta"] == "Actividades"]
        self.assertEqual(sorted(actividades["Aspecto"].unique()), ["Deporte", "Música"])
        # Deporte: 2+1+1+2+1+1 = 8; Música: 1+2+1+1+2+1 = 8.
        self.assertEqual(len(actividades[actividades["Aspecto"] == "Deporte"]), 8)
        self.assertEqual(len(actividades[actividades["Aspecto"] == "Música"]), 8)

    def test_texto_libre_con_comas_es_contexto(self):
        tidy = to_tidy(form_df())
        self.assertIn("Comentarios", tidy.columns)
        self.assertNotIn("Me gustó", tidy["Pregunta"].unique())

    def test_marca_de_tiempo_se_conserva(self):
        tidy = to_tidy(form_df())
        self.assertEqual(tidy["marca_tiempo"].unique().tolist()[:2],
                         ["2026-10-01 10:00", "2026-10-01 11:00"])

    def test_filas_vacias_se_eliminan(self):
        tidy = to_tidy(form_with_id_df())
        self.assertFalse(tidy["Respuesta"].isna().any())
        self.assertFalse((tidy["Respuesta"].astype(str).str.strip() == "").any())
        # R4 solo contestó contexto: desaparece del todo.
        self.assertNotIn("R4", tidy["ID"].unique())

    def test_reconciliacion_de_filas(self):
        """filas_salida == Σ tokens de casillas + Σ celdas de grids."""
        for df in (form_df(), form_with_id_df()):
            report = {a["name"]: a["inferred_type"] for a in analyze(df)}
            esperadas = 0
            for name, kind in report.items():
                cells = [v for v in df[name].dropna() if str(v).strip()]
                if kind in ("CHECKBOX", "CHECKBOX_GRID"):
                    esperadas += sum(len([t for t in str(v).split(",") if t.strip()])
                                     for v in cells)
                elif kind == "GRID":
                    esperadas += len(cells)
            self.assertEqual(len(to_tidy(df)), esperadas)

    def test_sin_preguntas_complejas_devuelve_frame_vacio(self):
        df = pd.DataFrame({"Nombre": ["Ana"], "Edad": ["30"]})
        tidy = to_tidy(df)
        self.assertEqual(len(tidy), 0)
        self.assertEqual(list(tidy.columns),
                         ["ID", "Nombre", "Edad", "Pregunta", "Aspecto", "Respuesta"])


def seed_sheet_cache(sheet_id: str, gid: str, raw: pd.DataFrame,
                     headers: bool = True):
    """Deja la hoja (cruda y con coerción) en el caché, como si se
    acabara de leer de Google."""
    entry = {"raw_df": raw, "df": sheets._coerce_numeric_columns(raw.copy()),
             "fetched_at": now()}
    caches["sheets"].set(sheets._cache_key(sheet_id, gid, headers), entry, None)


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
        self.assertEqual(list(df.columns),
                         ["ID", "marca_tiempo", "Nombre", "Edad",
                          "Comentarios", "Pregunta", "Aspecto", "Respuesta"])
        self.assertEqual(df["ID"].nunique(), 6)

    def test_fuente_normal_no_se_transforma(self):
        seed_sheet_cache("form1", "0", form_df())
        _, source = make_board(self.user, sheet_id="form1")
        df = sheets.load_source(source)
        self.assertIn("Sabores favoritos", df.columns)
        self.assertNotIn("Pregunta", df.columns)

    def test_el_tidy_se_cachea_por_pestaña(self):
        seed_sheet_cache("form1", "0", form_df())
        _, source = make_board(self.user, sheet_id="form1", is_form_response=True)
        with mock.patch.object(sheets.form_tidy, "to_tidy",
                               side_effect=sheets.form_tidy.to_tidy) as to_tidy:
            sheets.get_tidy_dataframe("form1", "0")
            sheets.get_tidy_dataframe("form1", "0")
            self.assertEqual(to_tidy.call_count, 1)  # segunda vez, caché
            # Los ajustes de columnas se aplican fuera del caché tidy:
            # cambiar la configuración no lo recalcula.
            source.columns = [{"name": "Nombre", "type": "text",
                               "include": False, "label": "N. completo"}]
            source.save()
            sheets.get_tidy_dataframe("form1", "0")
            self.assertEqual(to_tidy.call_count, 1)

    def test_actualizar_datos_recalcula_el_tidy(self):
        seed_sheet_cache("form1", "0", form_df())
        _, source = make_board(self.user, sheet_id="form1", is_form_response=True)
        with mock.patch.object(sheets.form_tidy, "to_tidy",
                               side_effect=sheets.form_tidy.to_tidy) as to_tidy, \
             mock.patch.object(sheets, "fetch_sheet_dataframe",
                               return_value=form_df()):
            sheets.get_tidy_dataframe("form1", "0")
            sheets.refresh_sheet("form1", "0")
            sheets.get_tidy_dataframe("form1", "0")
            self.assertEqual(to_tidy.call_count, 2)

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
        self.assertIn("Pregunta", df.columns)

    def test_columnas_de_edicion_son_las_del_tidy(self):
        seed_sheet_cache("form1", "0", form_df())
        _, source = make_board(self.user, sheet_id="form1", is_form_response=True)
        data = self.client.get(f"/api/sources/{source.id}/columns/").json()
        self.assertEqual([c["name"] for c in data["columns"]],
                         ["ID", "marca_tiempo", "Nombre", "Edad",
                          "Comentarios", "Pregunta", "Aspecto", "Respuesta"])
        self.assertEqual(data["rows"], 53)
        for column in data["columns"]:
            self.assertNotIn("question_type", column)
            self.assertNotIn("confidence", column)

    def test_endpoint_de_columnas_sirve_el_tidy_con_form(self):
        seed_sheet_cache("form1", "0", form_df())
        data = self.client.get(
            "/api/sources/google/spreadsheets/form1/tabs/0/columns/?form=1").json()
        names = [c["name"] for c in data["columns"]]
        self.assertIn("Pregunta", names)
        self.assertIn("Respuesta", names)
        self.assertNotIn("Sabores favoritos", names)  # se explosiona
        # Sin «form» se sirven las columnas de la hoja ancha.
        data = self.client.get(
            "/api/sources/google/spreadsheets/form1/tabs/0/columns/").json()
        self.assertIn("Sabores favoritos",
                      [c["name"] for c in data["columns"]])

    def test_schema_de_formulario_sirve_el_tidy(self):
        seed_sheet_cache("form1", "0", form_df())
        _, source = make_board(self.user, sheet_id="form1", is_form_response=True)
        schema = self.client.get(f"/api/sources/{source.id}/schema/").json()
        self.assertIn("Pregunta", schema["all_fields"])
        self.assertIn("Respuesta", schema["all_fields"])
        self.assertIn("Nombre", schema["all_fields"])  # contexto
        self.assertNotIn("Sabores favoritos", schema["all_fields"])  # se explosiona

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
                                   "formula": "COUNT_DISTINCT([ID])",
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
                                json.dumps({"formula": "COUNT_DISTINCT([ID])"}),
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
