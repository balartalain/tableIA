# Arquitectura del backend de widgets

Cada widget de un tablero se guarda como dos JSON:

- **`data_spec`**: qué calcular (dimensiones, pivotes, filtros, métricas, orden, Top N). No depende del tipo de widget.
- **`view_spec`**: cómo mostrarlo (título, etiquetas, roles del KPI, apilado, líneas de referencia…). Lo construye el tipo de widget a partir del `data_spec` y de sus opciones de vista.

Ninguno de los dos contiene código: solo valores que se validan contra un JSON Schema cerrado, generado en cada llamada a partir de las columnas reales de la hoja.

El backend está organizado en tres capas. Cada concepto (widget, tipo de métrica, operador, agregación, forma de resultado) es una **clase registrada**. El resto del sistema recorre los registros en vez de preguntar `if widget_type == ...` o `if m["type"] == ...`.

```
            ┌──────────────────────────────────────────────────────────┐
 views.py → │ services/widget_service.py   services/ai_spec.py         │  casos de uso
            └──────────────┬───────────────────────────────────────────┘
                           ▼
            ┌──────────────────────────────────────────────────────────┐
            │ widgets/   WidgetType: capacidades, opciones de vista,    │  qué admite y cómo
            │            reglas propias, plan y compilación             │  se presenta cada tipo
            └──────┬───────────────────────────────┬───────────────────┘
                   ▼                               ▼
            ┌──────────────────────┐        ┌──────────────────────────┐
            │ engine/  run + PLANS │ ─────▶ │ dsl/  el lenguaje:       │
            │ (formas de resultado)│        │ métricas, condiciones,   │
            └──────────────────────┘        │ DataSpec, reglas         │
                                            └──────────────────────────┘
```

Reglas de capas, verificadas por test (`tests/test_architecture.py`):
- `dsl/` no importa `engine/`, `widgets/` ni `services/`, y no nombra ningún widget concreto.
- `engine/` no importa `widgets/` ni `services/`.

---

## 1. Estructura de paquetes

```
sheets_reports/
  dsl/                          # el lenguaje: piezas reutilizables, sin conocer widgets
    registry.py                 # Registry genérico (register, unregister, get, keys)
    context.py                  # SheetContext
    values.py                   # to_python, to_key, percent, sort_key, series_dict
    ordering.py                 # chronological, sorted_table y OTHERS_LABEL
    errors.py                   # SpecValidationError + mensajes legibles de jsonschema
    schema.py                   # helpers de JSON Schema (enum, union, nullable) y límites globales
    conditions.py               # FilterOperator, RelativeValue, Condition (+ FILTER_OPS, RELATIVE_VALUES)
    aggregations.py             # Aggregation (+ AGGREGATIONS)
    calc_ops.py                 # CalcOp (+ CALC_OPS)
    groups.py                   # GroupCondition (having / inner_having)
    metrics/
      base.py                   # Metric, METRICS, evaluation_order, metrics_errors, metric_union
      evaluation.py             # scalar_values, flat_table
      agg.py  calc.py  grouped.py   # grouped.py trae también GroupResult (+ GROUP_RESULTS)
    parts/                      # SPEC_PARTS: las piezas del data_spec
      base.py                   # SpecPart, Rows, UnsupportedPart, ColumnListPart
      columns.py                # Dimensions, Pivots, Columns
      metrics.py                # Metrics (+ PivotMultiMetric, MetricsValid, AliasNotColumn, ShowAsAllowed)
      filtering.py              # Filters, Having, OrderBy (Sort), TopN (Limit)
      trend.py                  # TrendBy
    spec.py                     # DataSpec, with_parts, union_schema y GroupedSpec / ScalarSpec / RowsSpec
    rules.py                    # Rule, Stage y bandas de prioridad
  engine/
    executor.py                 # run(spec, df, plan): relativos, pasos de las piezas y plan
    plans/                      # PLANS: un archivo por forma de resultado
      base.py                   # ResultPlan[R], PlanResult, PlanInput, ResultTooLargeError
      scalar.py  flat.py  pivot_chart.py  pivot_table.py  rows.py  column_values.py   # cada plan con su *Result tipado
  widgets/
    base.py                     # WidgetType, ViewOptions, WIDGETS
    presentation.py             # humanize y claves de campos de la tabla
    chart.py                    # ChartWidget y ChartOptions (base de bar y line)
    kpi.py  bar.py  line.py  donut.py  dynamic_table.py  table.py  filter.py
    ext/                        # widgets de extensión: un archivo por widget (ver §6)
  sdk.py                        # API estable que importan los widgets de extensión
  services/
    widget_service.py           # crear / editar data_spec / filtros del tablero / calcular
    ai_spec.py                  # genera specs con Gemini usando los registros
    sheets.py                   # lectura y caché de la hoja
```

Importar `sheets_reports.widgets` registra los siete widgets del core; importar `sheets_reports.dsl.metrics` y `sheets_reports.engine.plans` registra las métricas y los planes. Los widgets de `widgets/ext/` los carga la app al arrancar (`SheetsReportsConfig.ready`).

---

## 2. Capa `dsl/`: el lenguaje

### 2.1 Piezas base

- **`SheetContext`**: lo que el DSL necesita saber de la hoja. Tiene `source` (gid), `fields` (columnas en orden), `numeric_fields` y `samples` (valores de ejemplo, solo para la IA). Se construye con `SheetContext.from_dataframe(df, gid)`. De aquí salen los `enum` de columnas de todos los schemas.
- **`Registry`**: registro genérico por clave. `@REGISTRO.register` registra una clase (o una instancia, según el registro). `get(key)` lanza `UnknownKeyError` con un mensaje legible. `keys()` alimenta los `enum` del JSON Schema.

| Registro | Qué guarda | Dónde |
|---|---|---|
| `FILTER_OPS` | operadores de condición (`eq`, `in`, `between`, `contains`, `is_empty`…) | `dsl/conditions.py` |
| `RELATIVE_VALUES` | valores relativos (`current_year`, `max`, `second_max`…) | `dsl/conditions.py` |
| `AGGREGATIONS` | `sum`, `avg`, `count`, `count_distinct`, `min`, `max`, `median` | `dsl/aggregations.py` |
| `CALC_OPS` | `add`, `sub`, `mul`, `div`, `ratio_pct`, `diff_pct` | `dsl/calc_ops.py` |
| `GROUP_RESULTS` | resultados de una métrica agrupada (`count`, `pct_groups`, `sum`…, `top`, `bottom`) | `dsl/metrics/grouped.py` |
| `METRICS` | tipos de métrica (`agg`, `calc`, `grouped`) | `dsl/metrics/` |
| `PLANS` | formas de resultado (`scalar`, `flat`, `pivot_chart`, `pivot_table`, `rows`, `column_values`) | `engine/plans/` |
| `WIDGETS` | tipos de widget (`kpi`, `bar`, `line`, `donut`, `dynamic_table`, `table`, `filter`) | `widgets/` |
| `FILTER_CONTROLS` | tipos de control de la caja de filtros (`multi_select`) | `widgets/filter.py` |

### 2.2 Vocabulario: estrategias pequeñas

Cada estrategia reúne en una clase su regla y su implementación:

- **`FilterOperator`**: `validate(cond, ctx, path)` dice qué valor lleva el operador (ninguno, lista, rango, texto o escalar) y si exige columna numérica. `mask(series, value)` es la máscara vectorizada de pandas. Nunca se evalúa texto.
- **`RelativeValue`**: `resolve(series, today)` sale del reloj o de los valores de la columna.
- **`Aggregation`**: declara `needs_field`, `numeric_only` y `empty_value` (lo que vale un grupo sin filas). Implementa `by_group(grouped, field)` y `total(df, field)`.
- **`CalcOp`**: declara `divides` e `is_percent`. Implementa `scalar(left, right)` y `series(left, right)`, con `None`/`NaN` al dividir entre cero.
- **`Condition`** (`{field, op, value | relative}`) y **`GroupCondition`** (`{left, op, right}`) son dataclasses con `from_dict`/`to_dict`.

### 2.3 Métricas

`Metric` es la pieza polimórfica. Cada tipo es una dataclass inmutable registrada en `METRICS`:

```python
class Metric(ABC):
    key: ClassVar[str]            # "agg" | "calc" | "grouped"
    label: ClassVar[str]          # "«por grupo»", para los mensajes
    derived: ClassVar[bool]       # se calcula desde otras métricas (calc)
    alias: str                    # su "as"

    # schema y parseo
    schema(ctx, *, for_ai, nested) -> dict      # classmethod
    from_dict(raw) -> Metric                    # classmethod
    to_dict() -> dict

    # reglas
    validate(ctx, scope, path) -> list[str]     # scope: métricas del mismo nivel, por alias
    depends_on() -> set[str]

    # presentación
    is_percent() / is_numeric() / supports_trend()

    # preparación
    resolved(df)                    # valores relativos resueltos una vez sobre la hoja
    without_eq_filter_on(column)    # para los puntos de la tendencia

    # ejecución (cada forma de resultado usa la que necesita)
    scalar(ev: ScalarContext)                      # KPI
    flat(fc: FlatContext) -> (Series, total)       # por grupo de una columna
    aggregate(df, by) / grand_total(df)            # por celda (cruces y tabla dinámica)
    derive(values)                                 # solo métricas derived
```

| Tipo | Qué hace |
|---|---|
| `AggMetric` | Resume una columna (o cuenta filas) con una `Aggregation`, con condiciones propias opcionales y `show_as` (`value`, `pct_row`, `pct_column`, `pct_total`). |
| `CalcMetric` | `left <op> right` entre otras métricas del mismo nivel; `right` también puede ser un número. Es `derived`. |
| `GroupedMetric` | Agrupa por `group_by`, calcula sus métricas `inner`, filtra los grupos con **`inner_having`** y los resume con un `GroupResult`. `top`/`bottom` devuelven `{label, value}`, no un número. |

**Orden de evaluación ≠ orden de presentación.** `evaluation_order(metrics)` ordena las métricas topológicamente según `depends_on()` (es estable cuando no hay dependencias). El orden de la lista solo decide el orden de columnas, series y leyenda. Por eso arrastrar un cálculo por encima de las métricas que usa no lo rompe. `metrics_errors(metrics, ctx, path)` valida una lista del mismo nivel: alias únicos, `metric.validate` de cada una y que no haya ciclos entre cálculos. También se usa para las métricas internas de `grouped`.

**Dos filtros de grupos, dos nombres.** Comparten el tipo `GroupCondition`, pero sus referencias siempre se pasan de forma explícita:

| Campo | Filtra | Sus referencias apuntan a |
|---|---|---|
| `DataSpec.having` | grupos de la primera dimensión del widget | aliases de `spec.metrics` |
| `GroupedMetric.inner_having` | grupos de `group_by` dentro de una métrica KPI | aliases de `metric.inner` |

### 2.4 `DataSpec` y sus piezas

El `data_spec` de un widget es una **composición de piezas**. Cada pieza (`SpecPart`, `dsl/parts/`) es una clave del JSON con todo lo suyo, y está registrada en `SPEC_PARTS`:

```python
class SpecPart:
    key                                   # la clave en el JSON ("dimensions", "metrics"…)
    order = 100                           # orden de su paso en el pipeline
    describe_absent = False               # la ficha de la IA dice "sin <key>" si el widget no la tiene
    schema(ctx, *, for_ai) -> dict        # su JSON Schema
    parse(raw) / dump(value) / default()  # JSON <-> valor tipado; valor si no viene
    names(value)                          # columnas o aliases que introduce (reconcile de etiquetas)
    sort_targets(value)                   # por qué se puede ordenar gracias a ella
    resolve(value, df)                    # valores relativos ya concretos
    prepare(spec, rows, *, aggregates)    # su paso en el pipeline (filtrar filas, Top N…)
    rules() -> list[Rule]                 # sus reglas
    readable(error, path, ctx)            # mensaje legible de un error de su schema
    absent_hint(widget)                   # mensaje si viene con valor y el widget no la tiene
    manifest() / absent_manifest()        # lo que aporta al manifiesto, con o sin ella
    describe() / missing()                # lo que aporta a la ficha de la IA
    loose()                               # la pieza con los límites del lenguaje
```

| Pieza | Clave | Valor | Paso en el pipeline |
|---|---|---|---|
| `Dimensions(mín, máx)` | `dimensions` | columnas que agrupan filas / eje X | — |
| `Pivots(mín, máx)` | `pivots` | columnas que agrupan columnas / series | — |
| `Columns(mín, máx)` | `columns` | columnas que se muestran tal cual | — |
| `Metrics(mín, máx, types, show_as, multi_with_pivot)` | `metrics` | `list[Metric]`; `types`: claves de `METRICS` que admite | — |
| `Filters()` | `filters` | `list[Condition]` (WHERE) | 10: aplica las condiciones |
| `Having()` | `having` | `list[GroupCondition]` sobre los grupos de la primera dimensión | 20: deja las filas de los grupos que cumplen |
| `OrderBy()` | `sort` | `Sort(by, dir)` o `None` | — |
| `TopN()` | `limit` | `Limit(n, others)` o `None` | 30: deja los N primeros grupos (o junta el resto en «Otros») |
| `TrendBy()` | `trend_by` | columna de la sparkline o `None` | — |

`DataSpec` reúne las piezas que declara su clase:

```python
class DataSpec:
    parts = (Filters(),)                         # la base: solo filtros
    source: str
    spec.<clave>                                 # el valor de esa pieza (spec.dimensions, spec.sort…)
    get(key, default) / has(key) / keys() / part(key)
    names() / sort_targets() / metrics / aliases / metric(alias)

    schema(ctx, *, for_ai) -> dict               # la unión de los schemas de sus piezas
    normalize(raw)                               # sin las claves de otras piezas que vienen vacías
    with_defaults(raw)                           # con el valor por defecto de sus claves que faltan
    own(raw)                                     # sin las claves de otras piezas (para el schema)
    from_dict(raw) / to_dict()                   # to_dict: `source` y solo sus claves
    resolved(df)                                 # relativos resueltos una vez ("el último año" es el de la hoja)
```

El core trae tres formas, y un widget ajusta las cotas con `with_parts(Base, *piezas, without=(...))`, que reemplaza las piezas por su clave:

| Clase | Piezas | La usan |
|---|---|---|
| `GroupedSpec` | `Dimensions(1, 1)`, `Pivots(0, 1)`, `Filters`, `Metrics(1, 5)`, `Having`, `OrderBy`, `TopN` | `bar`, `line`; `donut` (sin `pivots`, `Metrics(1, 1, show_as=False)`); `dynamic_table` (`Dimensions(1, 3)`, `Pivots(0, 2)`, `Metrics(multi_with_pivot=True)`) |
| `ScalarSpec` | `Filters`, `Metrics(1, 4, types={agg, calc, grouped})`, `TrendBy` | `kpi` |
| `RowsSpec` | `Columns(1, 50)`, `Filters`, `OrderBy` | `table`; `filter` (`Columns(1, 10)`, sin `sort`) |

El `data_spec` guardado tiene `source` y las claves de su widget. El builder manda todas las claves que conoce: `normalize` descarta las de otras piezas que vienen vacías, y `with_defaults` completa las que no manda. `union_schema(clases, ctx)` es el schema que acepta el `data_spec` de cualquiera de esas clases, con cada clave en sus límites del lenguaje (`loose()`) y ninguna obligatoria: lo usa la tool de la IA cuando el tipo no viene fijado. Con `for_ai=True` se omite `source` y las uniones van como `anyOf`, porque Gemini no soporta `if/then`.

### 2.5 Reglas semánticas

Cubren lo que el JSON Schema no puede expresar. Hay una clase por regla, y cada una declara **etapa**, **prioridad** y si es **bloqueante** (`dsl/rules.py`):

```python
class Rule:
    stage: Stage = Stage.SEMANTIC     # PRE_SCHEMA (dict crudo) | SEMANTIC (DataSpec)
    priority: int = STRUCTURE         # menor = antes; empates: orden de rules()
    blocking: bool = False            # si falla, se devuelven solo sus errores
    def check(self, spec, widget, ctx) -> list[str]
```

Cada pieza trae sus reglas, y `WidgetType.rules()` las junta con `UnsupportedPart` y las propias del widget. Las reglas consultan las piezas del spec (`spec.get(...)`, `widget.spec_cls.part(...)`) y `widget.label`; nunca preguntan por un widget concreto.

| Banda | Prioridad | Reglas (pieza) |
|---|---|---|
| `BUSINESS` | 0 | `PivotMultiMetric` (`Metrics` sin `multi_with_pivot`): etapa PRE_SCHEMA y bloqueante. "Con pivote solo se permite una métrica": el usuario elige, ni la IA ni el backend eligen por él. |
| `STRUCTURE` | 100 | `UnsupportedPart` (core, PRE_SCHEMA): una clave de `SPEC_PARTS` que el widget no tiene y viene con valor; el mensaje es el `absent_hint` de su pieza («Tarjeta KPI» no agrupa; usa una métrica «por grupo»…, «Lista» no se ordena). `NoRepeatedColumn` (`Dimensions`, `Pivots`, `Columns`), `PivotNotDimension` y `PivotNeedsDimension` (`Pivots`), `ShowAsAllowed` (`Metrics(show_as=False)`). |
| `METRICS` | 200 | `ConditionsValid` (`Filters`), `MetricsValid` (delega en `metrics_errors`) y `AliasNotColumn` (`Metrics`). |
| `REFERENCES` | 300 | `HavingRefsWidgetMetrics` (`Having`), `SortTargetExists` (`OrderBy`: la unión de `sort_targets()` de las piezas), `LimitNeedsMetricSort` (`TopN`). El KPI agrega `TrendNeedsTrendableMetric`. |

Que un widget no admita un tipo de métrica no es una regla: lo impide su schema, que limita la unión de métricas a los `types` de su pieza `Metrics`. Los mensajes de los errores de schema los da la pieza dueña de la clave (`readable`); lo genérico (columnas de métricas y condiciones, tipos de métrica, `as` en snake_case) queda en `dsl/errors.py`.

---

## 3. Capa `engine/`: ejecución

`engine.executor.run(spec, df, plan)` es el único lugar donde se hacen sumas, promedios y conteos reales. La IA no participa aquí. Los pasos son:

1. Guardar el **universo** (la hoja con los filtros del tablero), que es el denominador de los porcentajes.
2. `spec.resolved(universe)`: resolver los valores relativos.
3. Correr `prepare` de cada pieza del spec, en su `order`: `Filters` aplica las condiciones del widget; si el plan agrega (`plan.aggregates`) y hay dimensión, `Having` deja las filas de los grupos que cumplen y `TopN` las de los N primeros (con «Otros» si se pidió, marcando `has_others`). Así todo lo que sigue (pivotes, subtotales, porcentajes) se calcula sobre los grupos que se muestran.
4. `plan.run(spec, PlanInput(df, universe, has_others))`.

El ejecutor no conoce widgets, tipos de métrica ni claves del `data_spec`.

Cada plan del core está registrado en `PLANS`, recibe el spec de su forma (`GroupedSpec`, `ScalarSpec` o `RowsSpec`) y devuelve su propio resultado tipado:

| Plan (`key`) | Resultado | Contenido |
|---|---|---|
| `scalar` | `ScalarResult(values, trend)` | `{as: valor}` y, con `trend_by`, `Trend(categories, series)` en orden cronológico (números, meses, fechas o texto en orden natural; máx. 60 puntos) |
| `flat` | `FlatResult(dimension, rows, totals)` | `rows` es un DataFrame con una fila por grupo y una columna por métrica; `totals` es el total general calculado desde los datos |
| `pivot_chart` | `PivotChartResult(...)` | cruce dimensión × pivote para una métrica, con totales por fila, columna y general |
| `pivot_table` | `PivotTableResult(dimensions, pivots, metrics, column_keys, rows: list[PivotRow], grand: PivotBlock)` | tabla dinámica con filas y columnas anidadas y subtotales; las métricas derivadas se calculan por celda; tope de 20 000 celdas (`ResultTooLargeError`) |
| `rows` | `RowsResult(columns, rows, total_rows)` + `truncated` | las filas de la hoja tal cual (con los filtros), solo con `columns`, ordenadas por `sort` si lo hay; no agrega (`aggregates = False`); tope de 5 000 filas (`MAX_ROWS`), con el total real en `total_rows` |
| `column_values` | `ColumnValuesResult(values, truncated)` | valores distintos no vacíos de cada columna de `columns` (opciones de un filtro), normalizados con `distinct_values` igual que los compara la condición `in`; tope de 5 000 por columna (`MAX_OPTIONS`); no agrega |

---

## 4. Capa `widgets/`: los tipos de widget

### 4.1 El `data_spec` de cada widget

Cada widget declara `spec_cls`: la clase de spec con las piezas que admite y sus cotas (§2.4). De ahí salen su JSON Schema, sus reglas, su manifiesto y su ficha para la IA. Un widget que necesita un dato que ninguna pieza tiene escribe su propia `SpecPart` y una subclase de `DataSpec` que la use (ver el histograma en §6).

### 4.2 `ViewOptions`: opciones de vista

Es una dataclass inmutable por widget. La base trae `title`, `labels` y `display`; cada widget agrega las suyas: `KpiOptions` (roles del KPI), `ChartOptions` (`reference_lines`, compartida por `bar` y `line`), `BarOptions` (`ChartOptions` + `stacked`) y `FilterOptions` (`controls`). Tiene tres orígenes:

- `from_request(data, previous)`: desde el body del builder o la respuesta de la IA. Lo que no viene se toma de `previous`, las opciones actuales del widget al editar.
- `from_view(view_spec)`: desde lo guardado, para calcular.
- `ai_properties()` / `ai_required()`: lo que el widget agrega a `view_options` en la tool de Gemini. `ai_doc` explica en el prompt las propiedades que agrega esa clase (no las heredadas), y `from_ai(view_options)` traduce la respuesta de la IA al body del builder: por defecto pasa `labels` y sus `ai_properties()`; `KpiOptions` lo redefine porque la IA da la meta como `target_metric` o `target_value`.

**Política vista → datos.** Las opciones que nombran algo del `data_spec` se corrigen con `reconcile(spec)` y nunca invalidan un spec:

| Opción | Si los datos no la sostienen |
|---|---|
| `primary` inexistente | pasa a la primera métrica |
| `compare` inexistente o no numérica | `None` |
| `compare_mode` sin `compare` | `None` |
| `target` (alias) inexistente | `None` |
| `status` por `target_pct` sin `target` | `None` |
| `stacked` sin pivote | `False` |
| línea de referencia sobre una métrica que no está (sin pivote) | se descarta |
| línea de referencia inválida (tipo desconocido, valor fijo sin número) | se descarta; un color que no es `#rrggbb` toma el de por defecto; como máximo 5 |
| `labels` de columnas o aliases que ya no existen | se descartan |

`trend_by` (datos: el eje de la sparkline) y `compare` (vista: contra qué métrica se compara) son independientes. "Este año vs el anterior" se arma con dos métricas `agg` con filtros relativos y `compare` apuntando a la segunda.

### 4.3 `WidgetType`

```python
class WidgetType(Generic[R, V]):           # R: resultado del plan; V: sus ViewOptions
    key, label, spec_cls, options_cls, plan_key
    board_filtered = True        # se calcula con los filtros del tablero aplicados
    max_per_dashboard = None     # cuántos admite un tablero (la caja de filtros: 1)
    ai_enabled = True            # la IA puede proponerlo
    ai_doc = ""                  # cuándo elegirlo (lo que admite sale de sus piezas)
    ai_examples = ()             # [(pedido, argumentos de create_widget)] para el prompt

    manifest() -> dict                           # lo que el editor necesita saber del tipo

    # data_spec
    data_schema(ctx, *, for_ai=False) -> dict    # spec_cls.schema
    rules() -> list[Rule]                        # UnsupportedPart + las de sus piezas (+ las propias)
    errors(raw, ctx) -> list[str]                # nunca lanza
    validate(raw, ctx) -> DataSpec               # lanza SpecValidationError

    # view_spec
    options(data, previous_view=None) -> V
    build_view(spec, options) -> dict
    data_view(spec) -> dict                      # lo que el view_spec deriva del spec (x, seriesBy…)
    default_title(spec, options) -> str

    # ejecución y presentación
    plan(spec) -> ResultPlan[R]                  # por defecto PLANS.get(plan_key)
    execute(spec, df) -> R
    compile(result: R, options: V, spec) -> dict # formato exacto que dibuja el frontend
    render(data_spec, view_spec, df) -> dict     # execute + compile de un widget guardado
```

`errors()` es un *template method* igual para todos los widgets:
0. `spec_cls.normalize`: sin las claves de otras piezas que vienen vacías.
1. Reglas PRE_SCHEMA sobre el dict crudo (`UnsupportedPart`, `PivotMultiMetric`); una bloqueante que falla corta aquí.
2. JSON Schema de su `spec_cls` sobre `own(raw)` (sin lo que ya reportó `UnsupportedPart`; una clave desconocida sigue siendo un error), con mensajes legibles de sus piezas.
3. `spec_cls.from_dict` y luego las reglas SEMANTIC, ordenadas por `(stage, priority)`.

`manifest()` resume el tipo para el editor: `data` es lo que aporta cada pieza de `SPEC_PARTS` (`manifest()` si el widget la tiene, `absent_manifest()` si no: cotas `[0, 0]`, `having`/`sort`/`limit`/`trend` en `false`), `view` los campos propios de su `options_cls` (sin `title`, `labels` ni `display`) y `max_per_dashboard`. Sale de las mismas declaraciones que usa el backend, así que una pieza o un campo nuevo queda publicado sin más:

```json
{"data": {"dimensions": [1, 1], "pivots": [0, 1], "columns": [0, 0], "metrics": [1, 5],
          "metric_types": ["agg", "calc"], "show_as": true, "sort": true, "...": "..."},
 "view": ["reference_lines", "stacked"],
 "max_per_dashboard": null}
```

`build_view` guarda en el `view_spec`: `widget`, lo derivado del spec (`data_view`), los campos de las opciones ya reconciliadas, `percent`, `title` (o el título por defecto), `labels` y `display`.

### 4.4 Los widgets

| Widget | Dimensiones | Pivotes | Métricas | Tipos | Otras capacidades | Plan |
|---|---|---|---|---|---|---|
| `kpi` (`KpiWidget`) | 0 | 0 | ≤ 4 | agg, calc, grouped | sin having/sort/limit; con tendencia | `scalar` |
| `bar` (`BarWidget`) | 1 | 0–1 | ≤ 5 | agg, calc | `stacked`; líneas de referencia | `flat` o `pivot_chart` (según haya pivote) |
| `line` (`LineWidget`) | 1 | 0–1 | ≤ 5 | agg, calc | eje X en orden cronológico ascendente si no hay `sort`; líneas de referencia | `flat` o `pivot_chart` |
| `donut` (`DonutWidget`) | 1 | 0 | 1 | agg, calc | sin `show_as`: manda valores y ApexCharts calcula los % | `flat` |
| `dynamic_table` (`DynamicTableWidget`) | 1–3 | 0–2 | ≤ 5 | agg, calc | varias métricas con pivote | `pivot_table` |
| `table` (`TableWidget`) | 0 | 0 | 0 | — | 1–50 `columns`; filtros y orden por columna; sin having/Top N | `rows` |
| `filter` (`FilterWidget`) | 0 | 0 | 0 | — | 1–10 `columns` (un control cada una); uno por tablero; no la filtra el tablero; la IA no lo propone | `column_values` |

`bar` y `line` heredan de `ChartWidget`, que elige el plan según el spec y compila tanto `FlatResult` como `PivotChartResult`. `LineWidget.compile` reordena antes las categorías con `dsl/ordering.chronological` (la misma regla que la sparkline del KPI): una línea muestra una evolución, así que sin un orden elegido el eje X va de lo más antiguo a lo más reciente y no en el orden de la hoja. «Otros» queda al final.

**Líneas de referencia.** `bar` y `line` dibujan líneas sobre el eje de valores: una meta fija o el promedio, máximo o mínimo de los datos. Viven en `view_spec.reference_lines` (`ChartOptions`):

```js
{"kind": "value" | "avg" | "max" | "min",
 "value": 50000,            // solo con "value"
 "series": "total_ventas",  // sin pivote: `as` de una métrica; con pivote: un valor del pivote; null = todas
 "label": "Meta",           // "" = el texto por defecto ("Promedio", "Máx.", "Mín.")
 "color": "#d97706"}
```

- `ChartWidget.compile` agrega `referenceLines` con `series` traducida al nombre de la serie que se dibuja (la etiqueta de la métrica, o el valor del pivote). Una serie que no está en el resultado (un valor del pivote que los filtros dejaron fuera) no se manda.
- El frontend calcula el promedio, máximo o mínimo con los datos dibujados (`BaseWidget.referenceValue`) y arma las `annotations` de ApexCharts (`BaseWidget.referenceAnnotations`): en `yaxis`, o en `xaxis` si las barras son horizontales, porque ahí el eje de valores es el X. El rango del eje se amplía para que una línea fuera de los datos no quede cortada.

**Las dos tablas.** `dynamic_table` siempre agrupa por al menos una fila y resume con métricas, como una tabla dinámica de Sheets. `table` muestra las filas de la hoja tal cual: solo se eligen las columnas (y su orden), los filtros y el orden. Su `compile` devuelve `{"columns": [{"header", "field", "numeric"}], "rows", "total_rows", "truncated"?}`, donde la cabecera es el nombre exacto de la columna en la hoja.

**La caja de filtros.** `filter` es una barra fija arriba del tablero, a todo el ancho. Tiene un control por columna, en el orden del panel, y se reordena arrastrando. Hoy el único control es el selector múltiple (`MultiSelectControl`); el rango de fecha está previsto en `FILTER_CONTROLS`.
- Su `view_spec` guarda `controls: {columna: tipo}`. `FilterOptions.reconcile` deja uno por columna y cambia un tipo desconocido por `multi_select`.
- `compile` devuelve `{"filters": [{"field", "label", "type", "options", "truncated"?}]}`.
- Lo que el usuario elige **no se guarda en el widget**: es la selección del que mira el tablero, viaja en la URL y se aplica como filtro del tablero (ver §5).

---

## 5. Servicios, vistas, IA y modelo

- **`WidgetService(dashboard, df)`** (`services/widget_service.py`): recibe la hoja ya cargada, no hace I/O.
  - `create(widget_type, payload)` y `update_spec(widget, payload)`: toman del body las claves de `SPEC_PARTS` (el resto son opciones de vista), completan las que faltan con `spec_cls.with_defaults`, validan con el widget, guardan `spec.to_dict()` y reconstruyen el `view_spec`. Nunca llaman a la IA.
  - `create` también respeta `max_per_dashboard`: una segunda caja de filtros responde 422.
  - `parse_board_filters(raw)`: lee `?filters=[{field, op, value | relative}]`, el único formato de filtros del tablero, que es lo que arma la caja de filtros.
    - Cada filtro se valida con las mismas reglas que las condiciones. Un `in` admite hasta 5 000 valores (`MAX_BOARD_IN_VALUES`).
    - Un filtro inválido (por ejemplo, una URL compartida con una columna que ya no existe) **se ignora** y se informa en `filter_errors` de la respuesta, sin tumbar el tablero. Solo un parámetro que no es una lista JSON responde 400.
  - `render(widget, board_filters)`: aplica los filtros del tablero, que definen el universo y los denominadores de los %, salvo en los widgets con `board_filtered = False` (la caja de filtros, para que sus opciones no se achiquen con su propia selección). Luego llama a `widget.definition.render(...)`. Un error en un widget no tumba el tablero: devuelve `{"error": ...}`.
- **`views.py`**: es un adaptador HTTP. Carga la hoja (cacheada), llama al servicio y responde 422 con `SpecValidationError`. `board_editor` publica en la página el manifiesto de todos los tipos (`{key: manifest()}`, con `json_script`), que el editor lee en `window.WIDGET_MANIFEST`.
- **IA (`services/ai_spec.py`)**: la tool, el prompt y la lectura de la respuesta salen de los registros; ningún widget está nombrado en este módulo.
  - `build_tool_parameters(ctx, widget_type)` arma la tool de Gemini. Los tipos de widget salen de `WIDGETS`, solo los que tienen `ai_enabled`. El `data_spec` sale de `widget.data_schema(ctx, for_ai=True)` si el tipo está fijado (solo sus claves), o de `union_schema` de los widgets con `ai_enabled` si no (ninguna clave obligatoria; el widget elegido completa las suyas con `with_defaults`). `view_options` se arma con `ai_properties()` de cada `ViewOptions` (por ejemplo `stacked`, `kpi` o `reference_lines`); sus `ai_required()` se exigen solo con el tipo fijado, porque sin fijar no aplican a todos.
  - `build_system_prompt()` arma el prompt. `CORE_PROMPT` explica el DSL (claves del `data_spec`, tipos de métrica, condiciones) sin nombrar widgets: dice que cada tipo admite solo algunas claves y que las demás van vacías. Después vienen:
    - **Tipos de widget:** una línea por widget con su `ai_doc` y lo que admite, escrito desde sus piezas (`capabilities_text`: `describe()` de las que tiene, «sin X» por las de `describe_absent` que no tiene y por sus `missing()`), por ejemplo «Admite: metrics 1–4 (agg, calc, grouped), trend_by; sin having, sort, limit».
    - **view_options:** `title` y `labels` (comunes) y el `ai_doc` de cada clase de opciones, con los widgets a los que aplica («(solo bar, line)»).
    - **Ejemplos:** los `ai_examples` de cada widget, en el orden del registro.
  - `_normalize` separa la respuesta en tipo, `data_spec` y opciones, y deja la traducción de las opciones al `from_ai` del `options_cls` del widget elegido.
  - `generate_widget_spec(prompt, widget_type, ctx)` valida con `widget.errors`. Si falla, reintenta una vez pasando los errores. Si el error es de `PivotMultiMetric`, no reintenta.
- **Modelo**: `Widget.type` toma sus choices de `WIDGETS` (`widget_type_choices`), y `Widget.definition` devuelve su `WidgetType`. `data_spec` y `view_spec` siguen siendo `JSONField`.
- **Frontend**:
  - Cada widget tiene su clase registrada en `WidgetRegistry`, con el mismo `type` que su `key` en el backend.
  - **Capacidades:** se declaran una sola vez, en el backend. Los getters de `BaseWidget` leen el manifiesto de su `type` y deciden qué bloques muestra el builder:
    - de `data`: `maxDimensions`/`supportsDimension`, `maxPivots`/`supportsPivot`, `maxMetrics`/`supportsMetrics`, `maxColumns`/`usesColumns`, `supportsShowAs`, `supportsSort`, `metricTypes`;
    - de `view`: `supportsView(key)`, con atajos como `supportsStacked` y `supportsReferenceLines`;
    - de `max_per_dashboard`: `singleton`.

    Las clases de los widgets declaran solo lo de interfaz: `placement`, textos del builder (`pivotLabel`, `columnsLabel`…), `columnControls`, `supportsLabels`, `supportsColumnLabels`, `supportsTotals` (los totales viven en `display`) y `supportsConditions`. Sin manifiesto (la vista compartida) rige `BaseWidget.DEFAULT_MANIFEST`.
  - **Builder:** `builderFromSpec` y `builderToPayload` (`dashboard-store.js`) son la ida y vuelta entre el `data_spec`/`view_spec` y el estado del panel, incluidas las opciones de vista que se editan en «Configurar» (`stacked`, roles del KPI, `reference_lines`). Las métricas se identifican por un `_id` interno y se traducen a su `as` al enviar.
  - `TableWidget` (`table-widget.js`) extiende `DynamicTableWidget` (`dynamic-table-widget.js`) para reutilizar formatos, paginación, orden de columnas y descarga CSV.
  - **Caja de filtros** (`filter-widget.js`):
    - **Montaje y unicidad:** `placement = 'header'` la monta en `#dashboard-filters`, fuera del grid. `singleton` (su `max_per_dashboard = 1`) impide soltar una segunda.
    - **Panel:** `columnControls` agrega el selector de tipo en cada fila del builder.
    - **Controles:** cada selector múltiple es un Virtual Select (`virtual-select-plugin@1.0.39`), con el desplegable dibujado en `<body>`.
  - **Selección del tablero** (`filters.js`): `store.boardFilters = {columna: [valores]}` se sincroniza con `?filters=` en la URL, se manda a `/render/`, va en el enlace de "Compartir" y se limpia al quitar un filtro o la caja.
  - `metricAliases` (`dashboard-store.js`) resuelve los alias de los cálculos en orden de dependencias. La métrica agrupada usa `innerHaving` ↔ `inner_having`.

### Flujos

```
Editor: board_editor ─► window.WIDGET_MANIFEST = {key: manifest()} ─► el panel muestra lo que admite cada tipo

Builder (crear/editar)                 Render del tablero                    IA
POST/PUT ─► WidgetService              GET ─► parse_board_filters (?filters=) prompt ─► Gemini (tool desde registros)
  WIDGETS.get(type).validate(raw, ctx)    por cada widget:                     WIDGETS.get(type).errors(raw, ctx)
  build_view(spec, options(payload))      definition.render(data, view, df)    reintento con errores (1 vez)
  guarda data_spec + view_spec              execute: engine.run(plan)          build_view(spec, options(ia))
  render                                    compile: formato del frontend
```

---

## 6. Cómo agregar algo nuevo

### 6.1 Core y extensiones

El paquete se divide en **core** (todo lo de §1 salvo `widgets/ext/`) y **extensiones**. Un widget nuevo es una extensión: **un único archivo** `widgets/ext/<nombre>.py` que no edita ningún archivo del core.

- **Carga.** `SheetsReportsConfig.ready` llama a `widgets.ext.load()`, que importa cada módulo del paquete; cada uno se registra con `@WIDGETS.register`. No hay lista de imports que mantener. Se cargan en `ready` y no al importar `widgets` porque importan el `sdk`, que a su vez importa `widgets`: en `ready` el core ya está completo.
- **API estable.** Una extensión importa **solo** de `sheets_reports.sdk`, que reexporta lo que un widget necesita: `WidgetType`, `ViewOptions`, `ChartWidget`/`ChartOptions`, `DataSpec` y sus formas (`GroupedSpec`, `ScalarSpec`, `RowsSpec`, `with_parts`), `SpecPart`/`SPEC_PARTS` y las piezas del core, `Rule`/`Stage` y sus prioridades, `ResultPlan`/`PlanResult`/`PlanInput` y los resultados del core, `METRICS`/`Metric`, `flat_table`/`scalar_values` y los helpers de valores y presentación. Lo que no está en el `sdk` es interno del core.
- **Sin imports entre extensiones.** Si dos extensiones comparten comportamiento, cada una lo tiene en su archivo; esa duplicación queda a la vista y se resuelve subiéndolo al core y publicándolo en el `sdk`.
- **Plan propio.** Una extensión con una forma de datos nueva define su `ResultPlan` y su `PlanResult` en el mismo archivo y los devuelve desde `plan()`, sin registrarlos en `PLANS`. `PLANS` es el registro de las formas que comparten los widgets del core.
- **Garantías** (`tests/test_architecture.py`): las extensiones solo importan el `sdk` (ni otro módulo del core ni otra extensión, ni imports relativos), el core no importa extensiones, el `sdk` exporta todo lo que declara, y un widget de extensión escrito en un único archivo, con su propia pieza del `data_spec`, funciona de punta a punta. `ready` rechaza una clave de widget más larga que `Widget.type` (20 caracteres).
- **Dato propio.** Una extensión que necesita un dato que ninguna pieza tiene define su `SpecPart` en el mismo archivo, la registra con `@SPEC_PARTS.register` y la usa en su subclase de `DataSpec`. Su schema, sus reglas, sus mensajes, su paso en el pipeline, el manifiesto y la tool de la IA salen de la pieza; los demás widgets la rechazan con su `absent_hint`.
- **Alcance.** El panel de «Configurar» edita las piezas del core; una sección nueva del panel (por ejemplo, para editar una pieza propia desde el builder) es un cambio del frontend del core. La IA conoce a la extensión por su `ai_doc`, sus `ai_examples`, sus capacidades y el `ai_doc`/`from_ai` de sus opciones, todo en el mismo archivo. Su componente del frontend (`<nombre>-widget.js` y su `<script>` en `board_editor.html` y `board_view.html`) se agrega como el de cualquier widget.

### 6.2 Qué se toca para cada cosa

| Quiero agregar | Qué toco |
|---|---|
| Widget nuevo **con una forma de datos que ya existe** (ej. `area` = `flat`/`pivot_chart`, `funnel` = `flat`, `scatter` = `rows` con dos columnas) | `widgets/ext/<nuevo>.py`: una subclase con `spec_cls` (una forma del core, o ajustada con `with_parts`), `options_cls` (si tiene opciones propias), `plan_key` (o `plan()`), `compile`, `ai_doc` y `ai_examples` (si la IA lo propone) y `@WIDGETS.register`, importando solo del `sdk`. Más su componente en el frontend: `type`, interfaz y `renderContent`, sin capacidades (las toma del manifiesto). Nada más en el backend: el schema, la IA, los choices del modelo, la validación y el manifiesto lo toman de los registros. |
| Widget nuevo **con una forma de datos nueva** (ej. `histogram`: conteo de filas por intervalos de una columna numérica) | El mismo archivo, con su `ResultPlan` y su `PlanResult`, devuelto desde `plan()`. Es lógica de procesamiento de datos, pero queda aislada en su clase. No se edita `executor.py` ni otro plan. |
| Widget nuevo **con un dato nuevo en el `data_spec`** (ej. `bins` del histograma) | El mismo archivo, con su `SpecPart` registrada en `SPEC_PARTS` y una subclase de `DataSpec` que la use. |
| Opción de vista (ej. `reference_lines`) | Backend: un campo en el `options_cls` con `_request_fields`, `_view_fields`, `reconcile` (si nombra algo del `data_spec`), `view_fields`, `ai_properties` y su línea en `ai_doc` (más `from_ai` si su forma para la IA es distinta); y en `compile`, si el frontend la necesita para dibujar. El manifiesto la publica sola. Frontend: su bloque en el panel, visible con `supportsView('<campo>')`; su ida y vuelta en `builderFromSpec`/`builderToPayload`; y el dibujo en `renderContent`. Para dársela a otro widget basta con su `options_cls` y su dibujo. |
| Tipo de métrica (ej. `running_total`) | `dsl/metrics/<tipo>.py` con una subclase de `Metric` registrada en `METRICS`, importada en `dsl/metrics/__init__.py`. Después se agrega su `key` a los `types` de la pieza `Metrics` de los widgets que la admitan. |
| Agregación (ej. `stddev`) | Una clase `Aggregation` en `dsl/aggregations.py`. |
| Operador de filtro (ej. `starts_with`) | Una clase `FilterOperator` en `dsl/conditions.py`. |
| Valor relativo (ej. `last_30_days`) | Una clase `RelativeValue` en `dsl/conditions.py`. |
| Operación de cálculo | Una clase `CalcOp` en `dsl/calc_ops.py`. |
| Regla de negocio | Una clase `Rule` con su `stage` y `priority`. Va en `rules()` de la pieza a la que pertenece, o en `rules()` del widget si es propia. Su posición en la lista no importa. |

Las piezas de las filas siguientes son del core: las usan todos los widgets.

### Ejemplo: widget con una forma existente

```python
# widgets/ext/area.py
from sheets_reports.sdk import WIDGETS, ChartWidget


@WIDGETS.register
class AreaWidget(ChartWidget):          # hereda su data_spec (GroupedSpec), plan (flat/pivot_chart) y compile
    key = "area"
    label = "Gráfico de Áreas"
```

En el backend no hay nada más que hacer; en el frontend se crea `area-widget.js`. Al heredar `ChartOptions`, el área ya tiene líneas de referencia: el panel las ofrece por el manifiesto, y `area-widget.js` solo las dibuja.

### Ejemplo: widget con una forma y un dato nuevos

```python
# widgets/ext/histogram.py — todo el widget en un archivo
from dataclasses import dataclass

import numpy as np

from sheets_reports.sdk import (
    SPEC_PARTS, WIDGETS, DataSpec, Filters, PlanResult, ResultPlan, SpecPart, ViewOptions,
    WidgetType, column_message,
)


@SPEC_PARTS.register
class Bins(SpecPart):                    # dato nuevo: {"column": columna numérica, "n": intervalos}
    key = "bins"

    def schema(self, ctx, *, for_ai=False):
        return {"type": "object", "additionalProperties": False, "required": ["column", "n"],
                "properties": {"column": {"enum": ctx.ordered_numeric_fields},
                               "n": {"type": "integer", "minimum": 2, "maximum": 30}}}

    def names(self, value):
        return [value["column"]] if value else []

    def readable(self, error, path, ctx):
        return column_message(error, path, ctx) if error.validator == "enum" else None

    def describe(self):
        return ["bins"]


class HistogramSpec(DataSpec):
    parts = (Bins(), Filters())


@dataclass(frozen=True)
class BinsResult(PlanResult):
    edges: list
    counts: list


class BinsPlan(ResultPlan[BinsResult]):  # propio del widget: no se registra en PLANS
    aggregates = False                  # trabaja con las filas, no con grupos

    def run(self, spec, data):
        counts, edges = np.histogram(data.df[spec.bins["column"]].dropna(), bins=spec.bins["n"])
        return BinsResult(edges=edges.tolist(), counts=counts.tolist())


@WIDGETS.register
class HistogramWidget(WidgetType[BinsResult, ViewOptions]):
    key = "histogram"
    label = "Histograma"
    spec_cls = HistogramSpec
    ai_doc = 'cómo se distribuye una columna numérica ("distribución de las ventas").'

    def plan(self, spec):
        return BinsPlan()

    def default_title(self, spec, options):
        return f"Distribución de {spec.bins['column']}"

    def compile(self, result, options, spec):
        labels = [f"{a:g}–{b:g}" for a, b in zip(result.edges, result.edges[1:])]
        return {"categories": labels, "series": [{"name": "Filas", "data": result.counts}]}
```

La tabla de datos (`table`), un widget del core, sigue el mismo esquema repartido en el core: su plan (`engine/plans/rows.py`, compartible por otros widgets), su widget (`widgets/table.py`) y la pieza `Columns` de `RowsSpec`, que usan los widgets que muestran columnas sin agrupar.

`tests/test_architecture.py` hace exactamente esto: escribe un histograma como único archivo en un paquete de extensiones temporal, lo carga con `ext.load` y comprueba que funciona de punta a punta (su pieza `bins` en el schema, los mensajes y la tool de la IA; validación, choices del modelo, manifiesto, `WidgetService` y su plan propio). También registra un widget, una métrica y un plan de prueba del core y comprueba lo mismo sin tocar ningún otro módulo.

---

## 7. Pruebas

`python manage.py test sheets_reports` corre 260 tests, organizados por capa:

| Archivo | Qué cubre |
|---|---|
| `tests/dsl/test_validation.py` | schemas, condiciones, métricas (referencias, ciclos, agrupadas), lo que admite cada widget (piezas y `UnsupportedPart`) |
| `tests/dsl/test_spec.py` | `DataSpec` por piezas: solo sus claves, `normalize`/`with_defaults`, `with_parts` y los mensajes de las claves ajenas |
| `tests/engine/test_plans.py` | cálculos de cada plan, porcentajes, relativos, having/Top N, tendencia, orden de evaluación de las métricas |
| `tests/widgets/test_compile.py` | formato que recibe el frontend en cada widget, incluidas las `referenceLines` |
| `tests/widgets/test_rule_order.py` | el orden de los errores lo deciden etapa y prioridad, no la posición en `rules()` |
| `tests/widgets/test_view_options.py` | cada fila de la política vista → datos y el manifiesto de cada tipo |
| `tests/widgets/test_table.py` | tabla de datos: validación, plan `rows`, compilación y creación por la API |
| `tests/widgets/test_filter.py` | caja de filtros: validación, opciones (`distinct_values`, `column_values`), una por tablero, la selección filtra los widgets pero no la caja, la IA no la propone |
| `tests/test_architecture.py` | extensión por registro y widgets de extensión de punta a punta (incluido el manifiesto del editor), reglas de capas y de imports de las extensiones |
| `tests/test_views.py`, `tests/test_ai_spec.py` | endpoints HTTP y generación con IA (con Gemini simulado); el prompt armado desde los registros y que los `ai_examples` de cada widget sean specs válidos |

`tests/fixtures.py` ofrece los helpers comunes: `spec`, `agg`, `calc`, `errors_for`, `view`, `compiled` y `execute`.
