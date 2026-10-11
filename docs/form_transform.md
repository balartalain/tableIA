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

## Tipos de pregunta (clasificación automática)

La detección (`services/form_tidy.analyze`) corre sobre el frame **crudo**
(`dtype=str`) y determina qué columnas son de tipo casilla:

| Tipo | Transformación |
|---|---|
| `CHECKBOX` | Se expande en columnas binarias `{pregunta}_{valor}`. |
| `CHECKBOX_GRID` | Se expande en columnas binarias `{pregunta}_{valor}` por cada aspecto. |
| `GRID` | Cuadrícula simple de opción única: se conserva como columna ancha. |
| `FREE_TEXT_WITH_COMMAS` | Texto libre con comas: se conserva como columna ancha. |
| `SCALAR` | El resto: se conserva como columna ancha. |
