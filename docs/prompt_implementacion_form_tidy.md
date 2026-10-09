# PROMPT DE IMPLEMENTACIÓN (v2) — Pipeline Tidy para Google Forms en TableIA

## 0. Contexto y convenciones
- App Django `sheets_reports` en `/Users/innovacion/Projects/tableIA`. Leer primero
  `docs/arquitectura_backend.md` y `sheets_reports/services/sheets.py`.
- Docstrings y comentarios en español (estilo del código existente). Sin nuevas dependencias
  (pandas ya está). Tests en `sheets_reports/tests/`, ejecutar con `python manage.py test`.
- Capas: `services/` = lógica pura sobre DataFrames; `views.py` = adaptadores HTTP.
  **No tocar** `engine/`, `widgets/` ni los contratos `fields`/`style`.

## 1. Modelo de salida (tidy híbrido con contexto)
El DataFrame resultante tiene, en orden:
1. `ID` — primera columna. Si la hoja tiene una columna llamada "ID" (case-insensitive),
   se usa tal cual; si no, se genera secuencial `1..n` sobre las filas del DataFrame.
   **Semántica:** identifica la RESPUESTA (fila original) y se repite en cada fila
   explosionada. `COUNT(DISTINCT ID)` = respuestas únicas; `COUNT(*)` = selecciones.
2. `marca_tiempo` — solo si existe una columna "Marca de tiempo"/"Timestamp"
   (case-insensitive); se conserva como columna de contexto.
3. Columnas de contexto — las clasificadas `SCALAR` y `FREE_TEXT_WITH_COMMAS`,
   con su `label` de configuración si existe.
4. `Pregunta | Aspecto | Respuesta` — una fila por opción seleccionada (modelo
   "solo seleccionadas"). `Aspecto = ""` para preguntas planas.

Reglas:
- Se eliminan filas con `Respuesta` nula/vacía (los respondentes que no contestaron
  ninguna pregunta compleja desaparecen: comportamiento aceptado).
- Columnas con `include: false` se excluyen antes de transformar.

## 2. Modelo de datos
- `DataSource.is_form_response = models.BooleanField(default=False)` + migración.
- `DataSource.columns[i]` gana `question_type` opcional (dentro del JSONField existente,
  sin migración): `SCALAR | CHECKBOX | GRID | CHECKBOX_GRID | FREE_TEXT_WITH_COMMAS`.

## 3. Nuevo `sheets_reports/services/form_tidy.py` (módulo puro, pandas)

### `analyze(df, custom_separator=" [") -> list[dict]`
Reporte `[{name, inferred_type, confidence, details}]`. **Debe correr sobre el frame
crudo (`dtype=str`), antes de cualquier coerción numérica.**
- Excluir de la detección las columnas `ID` y `marca_tiempo`.
- **Grid:** regex `^(.*)<sep>(.*)$` (separador configurable, default `" ["`, cierre `"]"`);
  extraer la pregunta padre por el ÚLTIMO corchete; limpiar sufijos de duplicados de
  pandas (`Eval [A].1` → `Eval [A]`) al agrupar; requerir **≥2 columnas** con el mismo padre.
- **Multi-valor (casillas):** sobre celdas no nulas, `str.split(r"\s*,\s*")`; métricas:
  `pct_commas`, `cardinality_ratio` (únicos/total), `avg_token_length`, `repetition_rate`
  (fracción de tokens presentes en ≥2 celdas) y tamaño de vocabulario.
  - `pct_commas >= 0.2` Y `cardinality_ratio < 0.4` Y `avg_token_length < 35` → `CHECKBOX`
    (confianza alta).
  - Banda `0.05 <= pct_commas < 0.2`: `CHECKBOX` solo si además `repetition_rate >= 0.9`
    Y vocabulario ≤ 50 tokens (confianza media).
  - Si no → `FREE_TEXT_WITH_COMMAS` (columna de contexto; **nunca** se explosiona).
- **Tipo de grid:** si las celdas de las columnas del padre son multi-valor (misma regla
  de vocabulario) → `CHECKBOX_GRID`; si no → `GRID`.
- `confidence`: fórmula explícita y documentada (combinación normalizada de `pct_commas`,
  `cardinality_ratio` y `repetition_rate`). Marcar con baja confianza los
  `FREE_TEXT_WITH_COMMAS` que cumplieron parcialmente, para revisión manual.
- Umbrales como constantes de módulo (tunables).

### `to_tidy(df, column_configs, custom_separator=" [") -> pd.DataFrame`
- `ID` primero (existente o generado), luego `marca_tiempo` si existe, luego columnas
  de contexto, luego `Pregunta | Aspecto | Respuesta`.
- `GRID`: `melt` con `id_vars` = columnas de contexto; extraer `Pregunta`/`Aspecto`
  del encabezado.
- `CHECKBOX`: `split` + `explode`; `Pregunta` = nombre (o label) de la columna,
  `Aspecto = ""`.
- `CHECKBOX_GRID`: melt primero, luego `split` + `explode` sobre la celda resultante.
- Concatenar los frames por pregunta (**no** producto cruzado entre preguntas).
- Drop de `Respuesta` nula/vacía; `reset_index(drop=True)`.

## 4. Integración en `services/sheets.py`
- La detección y la transformación deben ver el frame **crudo**: hacer que
  `fetch_sheet_dataframe` permita conservar el frame sin `_coerce_numeric_columns`
  (parámetro o guardar `raw_df` en la entrada de caché junto a `df`).
- `load_source(source)`: si `source.is_form_response` → aplicar config de columnas
  (include/label) sobre el crudo y luego `to_tidy`; si no, el camino actual.
- **Caché:** clave `sheet_df:{sheet_id}:{gid}:{int(headers)}:tidy:{hash_config}`, donde
  `hash_config` es un hash del JSON de `question_type` (para invalidar al cambiar la
  clasificación). Mantener coherente en `_store`, `get_sheet_dataframe`, `refresh_sheet`
  y `fetched_at`.
- `/api/sources/{id}/schema/` y las vistas previas de IA sirven el frame tidy cuando la
  fuente es de formulario.

## 5. Endpoints (alineados a los existentes)
- `GET /api/sources/{id}/columns/?headers=1&refresh=1` → incluye `question_type` inferido
  y confianza por columna cuando `is_form_response` está activo.
- `PUT /api/sources/{id}/` → acepta `is_form_response` y `question_type` por columna;
  al guardar, invalidar la caché tidy.

## 6. Reglas de uso para widgets (documentación; el motor ya las soporta)
- Respuestas únicas: agregación `count_distinct` sobre `ID`.
- Selecciones de casillas: `count` sobre `ID` (o conteo de filas).
- Filtros cruzados: las columnas de contexto se filtran directamente junto a
  `Pregunta`/`Aspecto`/`Respuesta`.
- Añadir breve sección en `docs/` y ejemplos en `ai_doc` de widgets donde aplique.

## 7. Tests (`sheets_reports/tests/test_form_tidy.py`)
Fixture en memoria con: casillas, grid MC, grid casillas, texto libre largo con comas,
casilla con opciones numéricas (`"1, 2"`), encabezados duplicados, caso con columna ID
existente y caso sin ID, y marca de tiempo. Validar:
1. Clasificación correcta `CHECKBOX` vs `FREE_TEXT_WITH_COMMAS`.
2. Despivote de `GRID` y `CHECKBOX_GRID` (valores correctos de `Pregunta`/`Aspecto`).
3. `ID` existente se reutiliza; si no, se genera `1..n`; se repite por fila explosionada.
4. `COUNT(DISTINCT ID)` == número de respondentes.
5. `"1, 2"` **no** se convierte en `12` (transformación antes de coerción).
6. Encabezados `.1` se agrupan con su padre.
7. `marca_tiempo` se conserva; filas con `Respuesta` vacía se eliminan; respondente sin
   preguntas complejas desaparece.
8. Reconciliación: `filas_salida == Σ tokens de casillas + Σ (respondentes × aspectos)`.

## 8. No hacer
- No tocar `engine/`, `widgets/` ni el contrato `fields`/`style`.
- No añadir dependencias.
- No explosionar `FREE_TEXT_WITH_COMMAS`.
