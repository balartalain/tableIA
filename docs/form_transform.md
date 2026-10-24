# Respuestas de Google Forms: pipeline de transformación

Una hoja de respuestas de Google Forms guarda cada pregunta en su propia
columna y cada aspecto de una cuadrícula en «Pregunta [Aspecto]». Las preguntas
de tipo casilla (varias opciones separadas por comas en una celda) se transforman
expandiendo cada valor en su propia columna binaria (`pregunta_valor`).

## Elegir la hoja

Al seleccionar una pestaña (crear un tablero, agregar una fuente o
cambiar la hoja de una fuente), el selector pregunta:

> ¿La pestaña … proviene de un Google Forms?

- **Sí** → la fuente se guarda con `DataSource.is_form_response = true`
  y el servidor aplica la transformación de casillas.
- **No** → la fuente se lee como cualquier hoja, sin transformación.

En el editor de una fuente existente («Columnas») el flag se conserva
y se reenvía al guardar; para cambiarlo, se usa «Cambiar fuente» y se
vuelve a preguntar.

## Modelo de salida

El DataFrame mantiene el formato ancho (1 fila por respuesta `_ID`):

1. `_ID` — identifica la **respuesta** (fila original de la hoja).
2. Columnas escalares, de fecha y de cuadrículas simples (`GRID`) — se conservan con su encabezado original de la hoja.
3. Columnas de casillas (`CHECKBOX` y `CHECKBOX_GRID`) — se expanden en columnas binarias `{pregunta}_{valor}` con valor `1` si fue seleccionada y `0` si no.

Incluir, excluir, renombrar (`label`) y tipar esas columnas se hace
con la configuración de columnas de la fuente, igual que en una hoja normal.

## Cuadrículas como columnas virtuales

Una cuadrícula llega ancha: una columna por aspecto («Evaluación [Contenido]», «Evaluación
[Profesor]»…). Para cruzarla como Aspecto × Respuesta (tabla dinámica, barras), la fuente
suma dos **columnas virtuales** por pregunta: `«<pregunta> · Aspecto»` y `«<pregunta> ·
Respuesta»`. Una pregunta de casillas (`CHECKBOX`) recibe el mismo trato: ya expandida es una
cuadrícula en ancho (una columna Sí/No por opción), con `«<pregunta> · Opción»` y
`«<pregunta> · Respuesta»` (Sí/No). Opción × Respuesta con «% de la fila» da el % que marcó
cada opción; Opción con el filtro Respuesta = Sí, cuántos la marcaron. No están en el frame: existen solo dentro de la consulta de un widget que las usa
(`engine/steps/grid.py`).

- `transform` anota cada cuadrícula y cada pregunta de casillas en `df.attrs["grids"]`
  (`{pregunta: {kind, aspects}}`, con la columna de cada aspecto; en una cuadrícula de casillas,
  la de cada opción; en una pregunta de casillas, cada opción es un aspecto con su columna Sí/No). `grid_long` la pasa a
  formato largo: una fila por respuesta y aspecto (en una de casillas, por opción marcada), sin
  las celdas vacías.
- `sheets.load_source` (`attach_grids`) deja en `attrs["grid_store"]` las cuadrículas con el
  nombre a mostrar, tipo, formato, escala y alias de cada virtual. El almacén no se copia en
  cada operación de pandas (se comparte).
- La consulta (`engine/steps/__init__.run_steps`) sigue este orden:
  1. filtros sobre columnas reales, en ancho (una fila por respuesta, sin duplicados);
  2. `expand_grid` desdobla la cuadrícula, solo con las columnas que usa la consulta;
  3. filtros sobre las virtuales;
  4. agregación, ventanas y orden, sin cambios.
- **Conteo:** al desdoblar, el ID pasa a ser `id·aspecto`. Así cada celda Aspecto × Respuesta
  cuenta respuestas y el «% de la fila» es sobre las respuestas a ese aspecto. En una cuadrícula
  de casillas una persona tiene varias filas en el mismo aspecto con el mismo ID: el % de la
  fila es sobre las personas y puede sumar más de 100 %.
- **Límites:**
  - Un widget usa las virtuales de **una** cuadrícula; dos serían un producto cartesiano y se
    rechaza.
  - Dispersión, correlación, V de Cramér y filtros no las aceptan (`BaseWidget.grid_fields`).
  - Una fórmula no las lee.
  - Lo que compara aspectos de la misma persona («quienes dieron Malo al Profesor, ¿cómo
    evaluaron el Contenido?», «cuántos marcaron dos opciones a la vez», correlación entre
    aspectos) se hace con las columnas anchas, que tienen una fila por respuesta y sí las lee una
    fórmula.
- **Filtros del tablero:** los de una virtual se suman a los filtros de los widgets que usan esa
  cuadrícula; a los demás no les aplican.

**En el editor de la fuente** las virtuales son filas de la tabla de columnas, justo antes de
sus columnas anchas, con el distintivo «Cuadrícula · Aspecto/Respuesta» o «Casillas ·
Opción/Respuesta»; las anchas llevan «En cuadrícula» o «En casillas».
- Se guardan en `source.columns` con `grid` (la pregunta) y `role` (`aspect` | `value`), y se
  renombran, excluyen y tipan como cualquier columna.
- Para dejar fuera un aspecto de los resultados, se filtra en el widget (ej. Aspecto «no está
  en» […]).
- **Por defecto:** entran incluidas tanto las virtuales como las columnas anchas. Una columna
  ancha excluida no se ofrece en los widgets ni a la IA, pero sigue alimentando su cuadrícula.

**En el panel del widget** van en su propio grupo, «Cuadrículas y casillas», solo en los widgets que las
aceptan. Al elegir «· Aspecto» como dimensión con el pivote vacío, el pivote pasa a ser
«· Respuesta».

## Tipos de pregunta (clasificación automática)

La detección (`services/form_tidy.analyze`) corre sobre el frame **crudo**
(`dtype=str`) y determina qué columnas son de tipo casilla:

| Tipo | Transformación |
|---|---|
| `CHECKBOX` | Se expande en columnas binarias `{pregunta}_{valor}` (Sí/No), que también se cruzan con sus virtuales Opción y Respuesta. |
| `CHECKBOX_GRID` | Se expande en columnas binarias `{pregunta}_{valor}` por cada aspecto. |
| `GRID` | Cuadrícula simple de opción única: se conserva como columna ancha. |
| `FREE_TEXT_WITH_COMMAS` | Texto libre con comas: se conserva como columna ancha. |
| `SCALAR` | El resto: se conserva como columna ancha. |
