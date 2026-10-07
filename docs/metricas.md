# Métricas

Una **métrica** es un valor que el widget calcula resumiendo las filas de la hoja: «Suma de
Gasto_Real», «Conteo», «% Ejecución Presupuestaria». Se calcula por cada grupo que arman las
dimensiones y pivotes del widget; sin agrupación, sobre todas las filas (un solo número).

Cada métrica (`fields.metrics[]`) lleva:

| Clave | Qué es |
|---|---|
| `field` | Columna de la hoja (o campo calculado agregado) que se resume. `count` no la lleva. |
| `agg` | Cómo se resume: ver [Agregaciones](#agregaciones). |
| `alias` | Nombre interno único (snake_case), ej. `total_gasto_real`. Lo usan `sort_by` y los roles del KPI. |
| `label` | Opcional. Nombre a mostrar; sin él se muestra la agregación y la columna («Suma Gasto real»). |
| `filters` | Opcional. Condiciones solo para esa métrica: ver [Condiciones por métrica](#condiciones-por-métrica). |
| `window` | Opcional. Cómo se muestra respecto a los demás valores: ver [Mostrar como](#mostrar-como-ventanas). |
| `format` | Opcional. `number`, `currency`, `percent` o `progress` (barra 0-100). Sin él, «automático»: ver [Formato](#formato). |

## Formato

Cómo se muestran los valores, en este orden de prioridad:

1. **Métrica**: `format`, elegido en el panel.
2. **Automático**: el `format` del campo calculado agregado o, si la agregación conserva la
   unidad (`sum`, `avg`, `median`, `min`, `max`, `std`), el de la columna en la fuente
   (`currency` / `percent`, en el editor de la fuente). `count` y `count_distinct` salen como número.
   «Mostrar como» un % siempre se muestra con «%».

El motor deja `metric_formats` ({alias: formato}) en los metadatos; la tabla dinámica lo envía
como `formats` (con pivote, expandido a cada columna del pivote y al total), la tabla con el
formato de sus columnas y el KPI antepone «$» o agrega «%».

## Agregaciones

Definidas en `sheets_reports/engine/steps/aggregation.py` (`AGGREGATIONS`) y en el select del
editor (`AGG_OPTIONS` en `dashboard-store.js`).

| `agg` | En el editor | Qué calcula | Columnas |
|---|---|---|---|
| `sum` | Suma | Total de los valores. | Numéricas |
| `avg` | Promedio | Media aritmética. | Numéricas |
| `median` | Mediana | Valor del medio (no lo mueven los extremos). | Numéricas |
| `min` | Mínimo | Valor más bajo. | Numéricas |
| `max` | Máximo | Valor más alto. | Numéricas |
| `std` | Desviación estándar | Cuánto se dispersan los valores respecto al promedio. | Numéricas |
| `count` | Conteo | Cantidad de filas. No lleva `field`. | — |
| `count_distinct` | Valores distintos | Cantidad de valores distintos de la columna. | Cualquiera |
| `auto` | Automática | Usa un **campo calculado agregado**: la agregación ya está en su fórmula. | Solo campos calculados agregados |

### Campos calculados

Se definen en la fuente de datos (pestaña «Campos calculados») o los propone la IA, y quedan
disponibles para todo el tablero. Hay dos tipos, y lo decide la fórmula:

- **Por fila**: la fórmula no usa agregaciones (ej. `[Presupuesto_Asignado] - [Gasto_Real]`).
  Se calcula en cada fila y queda como una columna más: se usa en `field` con cualquier `agg`
  (y también como dimensión o filtro).
- **Agregado** (`auto`): se explica a continuación.

### Campos calculados agregados (`auto`)

Sirven para cálculos **entre totales**: cocientes, %, diferencias, márgenes, participaciones.

- Cada columna va dentro de una agregación: `SUM`, `AVG`, `COUNT`, `COUNT_DISTINCT`, `MIN`, `MAX`.
- Admiten `+ - * / ( )`, comparaciones, `AND OR NOT` e `IF(condición, sí, no)` dentro de las
  agregaciones. No se mezclan columnas sueltas con agregaciones (`SUM([a]) / [b]` no vale).
- `format`: `percent` si el resultado es un % (la fórmula multiplica por 100), si no `number`.

Ejemplos:

| Nombre | Fórmula |
|---|---|
| % Ejecución Presupuestaria | `SUM([Gasto_Real]) / SUM([Presupuesto_Asignado]) * 100` |
| Saldo disponible | `SUM([Presupuesto_Asignado]) - SUM([Gasto_Real])` |
| Participación de Hogar | `SUM(IF([categoria] = "Hogar", [ventas], 0)) / SUM([ventas]) * 100` |
| % de respuestas «Sí» | `AVG(IF([Respuesta] = "Sí", 1, 0)) * 100` |

Al agrupar, el valor de cada grupo es el cociente **de los totales del grupo**, no el promedio
de los cocientes fila por fila.

## Condiciones por métrica

`filters` dentro de una métrica: solo esas filas entran a esa métrica (el resto del widget no
cambia). Sirve para poner lado a lado «Ventas 2026» y «Ventas 2025», o «Ventas de Hogar» junto
al total. Solo en widgets que admiten **más de una métrica**; con una sola, se usan los filtros
del widget.

Además de valores fijos, aceptan valores relativos: `current_year`, `previous_year`,
`current_month` (según la fecha de hoy) y `latest`, `previous`, `earliest` (el periodo más
reciente, el anterior o el más antiguo de una columna de tiempo).

## Mostrar como (ventanas)

`window.type` transforma la métrica ya agregada comparándola con los demás valores
(`sheets_reports/engine/steps/window.py`).

| `type` | En el editor | Qué muestra |
|---|---|---|
| `percent_of_total` | % del total de la columna | Qué % del total representa cada valor. |
| `percent_of_row` | % del total de la fila | Con pivotes: qué % de su fila es cada celda (cada fila suma 100 %). **Necesita pivotes.** |
| `running_total` | Acumulado | Suma acumulada en el orden de las filas (cronológico si la dimensión es de tiempo). |
| `pct_change` | Variación vs anterior | % de cambio respecto a la fila anterior. |

Con pivotes solo valen `percent_of_total` (cada celda sobre el total de su columna) y
`percent_of_row`.

## Qué métricas admite cada widget

Límites en `capabilities` de cada clase en `sheets_reports/widgets/`. Todos los widgets con
métricas aceptan todas las agregaciones, incluida `auto`.

| Widget | Métricas | Dimensiones | Pivotes | Condiciones por métrica | Mostrar como |
|---|---|---|---|---|---|
| Tarjeta KPI (`kpi`) | 1 – 4 | 0 | 0 | Sí | — |
| Gráfico de Barras (`bar`) | 1 – 5 (1 con pivote) | 1 | 0 – 1 | Sí | % total, % fila, acumulado, variación |
| Gráfico de Líneas (`line`) | 1 – 5 (1 con pivote) | 1 | 0 – 1 | Sí | % total, acumulado, variación |
| Gráfico de Dona (`donut`) | 1 | 1 | 0 | No | — |
| Tabla Dinámica (`dynamic_table`) | 1 – 5 | 1 – 3 | 0 – 2 | Sí | % total, % fila |
| Tabla (`table`) | 0 | 0 | 0 | — | — |
| Filtros (`filter`) | 0 | 0 – 50 (columnas del filtro) | 0 | — | — |

### Notas por widget

- **Tarjeta KPI**: un solo número por métrica, sin agrupación, por eso no tiene «Mostrar como».
  Las métricas cumplen roles en el estilo: `primary` (número principal; por defecto la
  primera), `compare` (con qué se compara: % o absoluto) y `targetMetric` (meta: otra métrica o
  un valor fijo en `target`). Opcional `trend_by`: mini tendencia por una columna (ej. mes).
  - Participación → campo calculado agregado (ej. «Participación de Hogar» arriba).
  - Variación frente al periodo anterior → dos métricas con condiciones `latest` y `previous`
    sobre la columna de tiempo, y `compare` con el alias de la anterior.
- **Barras / Líneas**: con pivote (series), una sola métrica.
- **Dona**: una métrica repartida entre los valores de la dimensión; ya muestra la proporción
  de cada parte.
- **Tabla Dinámica**: las métricas van como columnas de valores; con pivotes, se repiten bajo
  cada valor del pivote. Necesita al menos una dimensión (para un solo número, un KPI).
- **Tabla**: no resume: muestra columnas de la hoja tal cual (`columns`), sin métricas.
- **Filtros**: controles de filtro para el tablero; no calcula métricas.
