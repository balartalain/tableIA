# Respuestas de Google Forms: pipeline tidy

Una hoja de respuestas de Google Forms guarda cada pregunta en su propia
columna y cada aspecto de una cuadrícula en «Pregunta [Aspecto]». Ese
marco ancho es difícil de contar con el motor (una casilla guarda
varias opciones en una celda, separadas por comas). TableIA puede leer
estas hojas en un **modelo tidy**: una fila por opción seleccionada.

## Elegir la hoja

Al seleccionar una pestaña (crear un tablero, agregar una fuente o
cambiar la hoja de una fuente), el selector pregunta:

> ¿La pestaña … proviene de un Google Forms?

- **Sí** → la fuente se guarda con `DataSource.is_form_response = true`
  y el servidor aplica la transformación tidy.
- **No** → la fuente se lee como cualquier hoja, sin transformación.

En el editor de una fuente existente («Columnas») el flag se conserva
y se reenvía al guardar; para cambiarlo, se usa «Cambiar fuente» y se
vuelve a preguntar.

## Modelo de salida

El DataFrame de la fuente tiene, en orden:

1. `ID` — identifica la **respuesta** (fila original de la hoja). Si la
   hoja tiene una columna «ID» (sin mayúsculas) se usa tal cual; si no,
   se genera `1..n`. **Se repite en cada fila explosionada**:
   `COUNT(DISTINCT ID)` = respuestas únicas, `COUNT(*)` = selecciones.
2. `marca_tiempo` — solo si existe «Marca de tiempo»/«Timestamp».
3. Columnas de contexto — las clasificadas `SCALAR` y
   `FREE_TEXT_WITH_COMMAS`, con su nombre de encabezado.
4. `Pregunta | Aspecto | Respuesta` — una fila por opción seleccionada;
   `Aspecto = ""` para preguntas planas.

Se eliminan filas con `Respuesta` nula/vacía: un respondente que no
contestó ninguna pregunta compleja desaparece del frame.

Incluir, excluir, renombrar (`label`) y tipar esas columnas se hace
con la configuración de columnas de la fuente, igual que en una hoja
normal: la edición de una fuente de formulario muestra las columnas
del modelo tidy, no las de la hoja ancha.

## Tipos de pregunta (clasificación automática)

La detección (`services/form_tidy.analyze`) corre sobre el frame **crudo**
(`dtype=str`), antes de cualquier coerción numérica — así «1, 2» de una
casilla sigue siendo dos opciones y no el número 12 — y es **automática y
definitiva**: no se guarda ni se edita por columna.

| Tipo | Cómo se detecta |
|---|---|
| `GRID` | ≥ 2 columnas «Pregunta [Aspecto]» con el mismo padre (por el último corchete; se limpian los sufijos de duplicado de pandas, «Eval [A].1» → «Eval [A]») y celdas de un solo valor. |
| `CHECKBOX_GRID` | Como `GRID`, pero las celdas son multi-valor. |
| `CHECKBOX` | Celdas multi-valor: `pct_commas ≥ 0.2` + `cardinality_ratio < 0.4` + `avg_token_length < 35` (confianza alta); o `0.05 ≤ pct_commas < 0.2` con `repetition_rate ≥ 0.9` y vocabulario ≤ 50 (confianza media). |
| `FREE_TEXT_WITH_COMMAS` | Tiene comas pero no parece casilla: **columna de contexto, nunca se explosiona**. Los que cumplieron parcialmente se marcan con baja confianza para revisión manual. |
| `SCALAR` | El resto: columna de contexto. |

`ID` y la marca de tiempo quedan fuera de la detección. Los umbrales son
constantes de módulo (tunables) en `services/form_tidy.py`.

## Caché

- `GET /api/sources/{id}/columns/?headers=1&refresh=1` devuelve, para una
  fuente de formulario, las columnas del modelo tidy con lo guardado de
  cada una (tipo, incluir, nombre a mostrar): se editan como las de
  cualquier hoja.
- `PUT /api/sources/{id}/` acepta `is_form_response` (y columnas
  claveadas por los encabezados del modelo tidy).
- El selector de hojas pide columnas con `?form=1` cuando se respondió
  «sí» al popup: desde el inicio las columnas son las del tidy.
- La transformación va en caché aparte, con clave
  `sheet_df:{sheet_id}:{gid}:{int(headers)}:tidy`: la clasificación
  depende solo de los datos, y los ajustes de columnas se aplican
  fuera de esta caché (`apply_column_config` en cada lectura). Al
  «Actualizar datos» (`refresh_sheet`) la fecha base cambia y el tidy
  se recalcula solo.

## Reglas de uso para widgets

El motor ya soporta el modelo tidy; la diferencia es qué agregación y
qué columnas elegir (el «Conteo» cuenta filas distintas sobre `ID`,
igual que en cualquier hoja):

- **Respuestas únicas** (¿cuántas personas…?): el **Conteo** (sin
  columna), o `count_distinct` sobre `ID`.
- **Personas que eligieron cada opción**: el Conteo con dimensiones
  `Pregunta` (y `Aspecto` para grids) y, para una opción concreta,
  un filtro sobre `Respuesta`.
- **Selecciones** (¿cuántas opciones se eligieron en total?): las
  filas del tidy; un campo calculado agregado
  `SUM(IF([Pregunta] = "Sabores", 1, 0))`.
- **Filtros cruzados**: las columnas de contexto (Nombre, marca de
  tiempo, `FREE_TEXT_WITH_COMMAS`…) se filtran directamente junto a
  `Pregunta`/`Aspecto`/`Respuesta`, como cualquier columna.
- Un `count_distinct` sobre `Respuesta` cuenta opciones distintas, no
  respuestas; para eso está `ID`.
