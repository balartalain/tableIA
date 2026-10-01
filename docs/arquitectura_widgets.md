# Arquitectura del backend de widgets

Cada widget de un tablero se guarda como dos JSON:

- **`data_spec`**: qué calcular (dimensiones, pivotes, filtros, métricas, orden, Top N). No depende del tipo de widget.
- **`view_spec`**: cómo mostrarlo (título, etiquetas, roles del KPI, apilado…). Lo construye el tipo de widget a partir del `data_spec` y de sus opciones de vista.

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
    spec.py                     # DataSpec, Sort, Limit y el JSON Schema del data_spec
    rules.py                    # Rule, Stage, bandas de prioridad y reglas genéricas
  engine/
    executor.py                 # run(spec, df, plan) y restrict_groups (having / Top N)
    plans/                      # PLANS: un archivo por forma de resultado
      base.py                   # ResultPlan[R], PlanResult, PlanInput, ResultTooLargeError
      scalar.py  flat.py  pivot_chart.py  pivot_table.py  rows.py   # cada plan con su *Result tipado
  widgets/
    base.py                     # WidgetType, DataCapabilities, ViewOptions, WIDGETS
    presentation.py             # humanize y claves de campos de la tabla
    chart.py                    # ChartWidget (base de bar y line)
    kpi.py  bar.py  line.py  donut.py  dynamic_table.py  table.py
  services/
    widget_service.py           # crear / editar data_spec / filtros del tablero / calcular
    ai_spec.py                  # genera specs con Gemini usando los registros
    sheets.py                   # lectura y caché de la hoja
```

Importar `sheets_reports.widgets` registra los cinco widgets; importar `sheets_reports.dsl.metrics` y `sheets_reports.engine.plans` registra las métricas y los planes.

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
| `PLANS` | formas de resultado (`scalar`, `flat`, `pivot_chart`, `pivot_table`, `rows`) | `engine/plans/` |
| `WIDGETS` | tipos de widget (`kpi`, `bar`, `line`, `donut`, `dynamic_table`, `table`) | `widgets/` |

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

### 2.4 `DataSpec`

```python
@dataclass(frozen=True)
class DataSpec:
    source: str
    dimensions: list[str]          # filas / eje X (agrupa)
    pivots: list[str]              # columnas / series (agrupa)
    metrics: list[Metric]
    columns: list[str]             # columnas que se muestran tal cual, sin agrupar (tabla de datos)
    filters: list[Condition]       # WHERE
    having: list[GroupCondition]   # grupos de la primera dimensión que se muestran
    sort: Sort | None
    limit: Limit | None            # Top N (+ «Otros»)
    trend_by: str | None           # serie de la sparkline

    DataSpec.schema(ctx, *, for_ai, dimensions, pivots, columns, metrics, metric_types) -> dict
    from_dict(raw) / to_dict()
    resolved(df)                   # relativos resueltos una vez ("el último año" es el de la hoja)
```

`DataSpec.schema` recibe los límites de quien lo pide (cada widget pasa sus capacidades como rangos `(mín, máx)`). La unión de métricas se limita a `metric_types`; con `metric_types` vacío, `metrics` debe ser `[]`. Con `for_ai=True` se omite `source` y las uniones van como `anyOf`, porque Gemini no soporta `if/then`.

### 2.5 Reglas semánticas (`dsl/rules.py`)

Cubren lo que el JSON Schema no puede expresar. Hay una clase por regla, y cada una declara **etapa**, **prioridad** y si es **bloqueante**:

```python
class Rule:
    stage: Stage = Stage.SEMANTIC     # PRE_SCHEMA (dict crudo) | SEMANTIC (DataSpec)
    priority: int = STRUCTURE         # menor = antes; empates: orden de rules()
    blocking: bool = False            # si falla, se devuelven solo sus errores
    def check(self, spec, widget, ctx) -> list[str]
```

Las reglas consultan `widget.capabilities` y `widget.label`; nunca preguntan por un widget concreto.

| Banda | Prioridad | Reglas |
|---|---|---|
| `BUSINESS` | 0 | `PivotMultiMetric`: etapa PRE_SCHEMA y bloqueante. "Con pivote solo se permite una métrica": el usuario elige, ni la IA ni el backend eligen por él. |
| `STRUCTURE` | 100 | `NoColumnRepeated` (dimensiones, pivotes y columnas), `PivotNeedsDimension`, y las que salen de las capacidades: `HavingAllowed`, `LimitAllowed`, `SortAllowed`, `TrendAllowed`. |
| `METRICS` | 200 | `ConditionsValid`, `MetricsValid` (delega en `metrics_errors`), `AliasNotColumn`. |
| `REFERENCES` | 300 | `HavingRefsWidgetMetrics`, `SortTargetExists` (dimensión, columna mostrada o métrica), `LimitNeedsMetricSort`. El KPI agrega `TrendNeedsTrendableMetric`. |

Que un widget no admita un tipo de métrica no es una regla: lo impide el schema del widget, que limita la unión de métricas a sus `metric_types`.

---

## 3. Capa `engine/`: ejecución

`engine.executor.run(spec, df, plan)` es el único lugar donde se hacen sumas, promedios y conteos reales. La IA no participa aquí. Los pasos son:

1. Guardar el **universo** (la hoja con los filtros del tablero), que es el denominador de los porcentajes.
2. `spec.resolved(universe)`: resolver los valores relativos.
3. Aplicar los filtros del widget.
4. Si el plan agrega (`plan.aggregates`) y hay dimensión, aplicar `restrict_groups`: `having` y Top N sobre la primera dimensión, recortando las filas, con «Otros» si se pidió.
5. `plan.run(spec, PlanInput(df, universe, has_others))`.

El ejecutor no conoce widgets ni tipos de métrica.

Cada plan está registrado en `PLANS` y devuelve su propio resultado tipado:

| Plan (`key`) | Resultado | Contenido |
|---|---|---|
| `scalar` | `ScalarResult(values, trend)` | `{as: valor}` y, con `trend_by`, `Trend(categories, series)` en orden cronológico (números, meses, fechas o texto en orden natural; máx. 60 puntos) |
| `flat` | `FlatResult(dimension, rows, totals)` | `rows` es un DataFrame con una fila por grupo y una columna por métrica; `totals` es el total general calculado desde los datos |
| `pivot_chart` | `PivotChartResult(...)` | cruce dimensión × pivote para una métrica, con totales por fila, columna y general |
| `pivot_table` | `PivotTableResult(dimensions, pivots, metrics, column_keys, rows: list[PivotRow], grand: PivotBlock)` | tabla dinámica con filas y columnas anidadas y subtotales; las métricas derivadas se calculan por celda; tope de 20 000 celdas (`ResultTooLargeError`) |
| `rows` | `RowsResult(columns, rows, total_rows)` + `truncated` | las filas de la hoja tal cual (con los filtros), solo con `columns`, ordenadas por `sort` si lo hay; no agrega (`aggregates = False`); tope de 5 000 filas (`MAX_ROWS`), con el total real en `total_rows` |

---

## 4. Capa `widgets/`: los tipos de widget

### 4.1 `DataCapabilities`

Declara qué `data_spec` admite un widget. De aquí salen su JSON Schema y las reglas de capacidad:

```python
@dataclass(frozen=True)
class DataCapabilities:
    dimensions: tuple[int, int]                     # (mín, máx) de columnas que agrupan filas
    pivots: tuple[int, int]                         # (mín, máx) de columnas que agrupan columnas
    columns: tuple[int, int] = (0, 0)               # (mín, máx) de columnas que se muestran tal cual
    metrics: tuple[int, int] = (1, 5)
    metric_types: frozenset = {"agg", "calc"}       # lo declara el widget, no la métrica
    multi_metric_with_pivot: bool = False
    having: bool = True
    sort: bool = True
    limit: bool = True
    trend: bool = False
```

### 4.2 `ViewOptions`: opciones de vista

Es una dataclass inmutable por widget. La base trae `title`, `labels` y `display`; cada widget agrega las suyas (`KpiOptions`, `BarOptions`). Tiene tres orígenes:

- `from_request(data, previous)`: desde el body del builder o la respuesta de la IA. Lo que no viene se toma de `previous`, las opciones actuales del widget al editar.
- `from_view(view_spec)`: desde lo guardado, para calcular.
- `ai_properties()` / `ai_required()`: lo que el widget agrega a `view_options` en la tool de Gemini.

**Política vista → datos.** Las opciones que nombran algo del `data_spec` se corrigen con `reconcile(spec)` y nunca invalidan un spec:

| Opción | Si los datos no la sostienen |
|---|---|
| `primary` inexistente | pasa a la primera métrica |
| `compare` inexistente o no numérica | `None` |
| `compare_mode` sin `compare` | `None` |
| `target` (alias) inexistente | `None` |
| `status` por `target_pct` sin `target` | `None` |
| `stacked` sin pivote | `False` |
| `labels` de columnas o aliases que ya no existen | se descartan |

`trend_by` (datos: el eje de la sparkline) y `compare` (vista: contra qué métrica se compara) son independientes. "Este año vs el anterior" se arma con dos métricas `agg` con filtros relativos y `compare` apuntando a la segunda.

### 4.3 `WidgetType`

```python
class WidgetType(Generic[R, V]):           # R: resultado del plan; V: sus ViewOptions
    key, label, capabilities, options_cls, plan_key

    # data_spec
    data_schema(ctx, *, for_ai=False) -> dict    # DataSpec.schema con sus capacidades
    rules() -> list[Rule]                        # genéricas (+ las propias)
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
1. Reglas PRE_SCHEMA sobre el dict crudo; una bloqueante que falla corta aquí.
2. JSON Schema del widget, con mensajes legibles (`dsl/errors.py`).
3. `DataSpec.from_dict` y luego las reglas SEMANTIC, ordenadas por `(stage, priority)`.

`build_view` guarda en el `view_spec`: `widget`, lo derivado del spec (`data_view`), los campos de las opciones ya reconciliadas, `percent`, `title` (o el título por defecto), `labels` y `display`.

### 4.4 Los widgets

| Widget | Dimensiones | Pivotes | Métricas | Tipos | Otras capacidades | Plan |
|---|---|---|---|---|---|---|
| `kpi` (`KpiWidget`) | 0 | 0 | ≤ 4 | agg, calc, grouped | sin having/sort/limit; con tendencia | `scalar` |
| `bar` (`BarWidget`) | 1 | 0–1 | ≤ 5 | agg, calc | `stacked` | `flat` o `pivot_chart` (según haya pivote) |
| `line` (`LineWidget`) | 1 | 0–1 | ≤ 5 | agg, calc | — | `flat` o `pivot_chart` |
| `donut` (`DonutWidget`) | 1 | 0 | 1 | agg, calc | — | `flat` |
| `dynamic_table` (`DynamicTableWidget`) | 1–3 | 0–2 | ≤ 5 | agg, calc | varias métricas con pivote | `pivot_table` |
| `table` (`TableWidget`) | 0 | 0 | 0 | — | 1–50 `columns`; filtros y orden por columna; sin having/Top N | `rows` |

`bar` y `line` heredan de `ChartWidget`, que elige el plan según el spec y compila tanto `FlatResult` como `PivotChartResult`.

**Las dos tablas.** `dynamic_table` siempre agrupa por al menos una fila y resume con métricas, como una tabla dinámica de Sheets. `table` muestra las filas de la hoja tal cual: solo se eligen las columnas (y su orden), los filtros y el orden. Su `compile` devuelve `{"columns": [{"header", "field", "numeric"}], "rows", "total_rows", "truncated"?}`, donde la cabecera es el nombre exacto de la columna en la hoja.

---

## 5. Servicios, vistas, IA y modelo

- **`WidgetService(dashboard, df)`** (`services/widget_service.py`): recibe la hoja ya cargada, no hace I/O.
  - `create(widget_type, payload)` y `update_spec(widget, payload)`: arman el `data_spec` con los valores por defecto del builder, validan con el widget, guardan `spec.to_dict()` y reconstruyen el `view_spec`. Nunca llaman a la IA.
  - `board_filters(filters)`: valida los filtros del tablero con las mismas reglas que las condiciones.
  - `render(widget, board_filters)`: aplica los filtros del tablero (que definen el universo) y llama a `widget.definition.render(...)`. Un error en un widget no tumba el tablero: devuelve `{"error": ...}`.
- **`views.py`**: es un adaptador HTTP. Carga la hoja (cacheada), llama al servicio y responde 422 con `SpecValidationError`.
- **IA (`services/ai_spec.py`)**:
  - `build_tool_parameters(ctx, widget_type)` arma la tool de Gemini desde los registros. Los tipos de widget salen de `WIDGETS`. El `data_spec` sale de `widget.data_schema(ctx, for_ai=True)` si el tipo está fijado, o de `DataSpec.schema` si no. `view_options` se arma con `ai_properties()` de cada `ViewOptions`.
  - `generate_widget_spec(prompt, widget_type, ctx)` valida con `widget.errors`. Si falla, reintenta una vez pasando los errores. Si el error es de `PivotMultiMetric`, no reintenta.
- **Modelo**: `Widget.type` toma sus choices de `WIDGETS` (`widget_type_choices`), y `Widget.definition` devuelve su `WidgetType`. `data_spec` y `view_spec` siguen siendo `JSONField`.
- **Frontend**:
  - Cada widget tiene su clase registrada en `WidgetRegistry`, con el mismo `type` que su `key` en el backend. Sus capacidades (`supportsDimension`, `supportsPivot`, `supportsMetrics`, `usesColumns`, `maxColumns`…) deciden qué bloques muestra el builder.
  - `TableWidget` (`table-widget.js`) extiende `DynamicTableWidget` (`dynamic-table-widget.js`) para reutilizar formatos, paginación, orden de columnas y descarga CSV.
  - `metricAliases` (`dashboard-store.js`) resuelve los alias de los cálculos en orden de dependencias. La métrica agrupada usa `innerHaving` ↔ `inner_having`.

### Flujos

```
Builder (crear/editar)                 Render del tablero                    IA
POST/PUT ─► WidgetService              GET ─► WidgetService.board_filters    prompt ─► Gemini (tool desde registros)
  WIDGETS.get(type).validate(raw, ctx)    por cada widget:                     WIDGETS.get(type).errors(raw, ctx)
  build_view(spec, options(payload))      definition.render(data, view, df)    reintento con errores (1 vez)
  guarda data_spec + view_spec              execute: engine.run(plan)          build_view(spec, options(ia))
  render                                    compile: formato del frontend
```

---

## 6. Cómo agregar algo nuevo

| Quiero agregar | Qué toco |
|---|---|
| Widget nuevo **con una forma de datos que ya existe** (ej. `area` = `flat`/`pivot_chart`, `funnel` = `flat`, `scatter` = `rows` con dos columnas) | `widgets/<nuevo>.py`: una subclase con `capabilities`, `options_cls` (si tiene opciones propias), `plan_key` (o `plan()`), `compile` y `@WIDGETS.register`, importada en `widgets/__init__.py`. Más su componente en el frontend. Nada más en el backend: el schema, la IA, los choices del modelo y la validación lo toman de los registros. |
| Widget nuevo **con una forma de datos nueva** (ej. `histogram`: conteo de filas por intervalos de una columna numérica) | Lo anterior, más una segunda pieza: `engine/plans/<forma>.py` con un `ResultPlan` registrado en `PLANS` y su `PlanResult`, importado en `engine/plans/__init__.py`. Esto sí es lógica de procesamiento de datos, pero queda aislada en su clase. No se edita `executor.py` ni otro plan. |
| Tipo de métrica (ej. `running_total`) | `dsl/metrics/<tipo>.py` con una subclase de `Metric` registrada en `METRICS`, importada en `dsl/metrics/__init__.py`. Después se agrega su `key` a `metric_types` de los widgets que la admitan. |
| Agregación (ej. `stddev`) | Una clase `Aggregation` en `dsl/aggregations.py`. |
| Operador de filtro (ej. `starts_with`) | Una clase `FilterOperator` en `dsl/conditions.py`. |
| Valor relativo (ej. `last_30_days`) | Una clase `RelativeValue` en `dsl/conditions.py`. |
| Operación de cálculo | Una clase `CalcOp` en `dsl/calc_ops.py`. |
| Regla de negocio | Una clase `Rule` con su `stage` y `priority`. Va en `DEFAULT_RULES` si es genérica, o en `rules()` del widget si es propia. Su posición en la lista no importa. |

### Ejemplo: widget con una forma existente

```python
# widgets/area.py
from sheets_reports.widgets.base import WIDGETS
from sheets_reports.widgets.chart import ChartWidget


@WIDGETS.register
class AreaWidget(ChartWidget):          # hereda capacidades, plan (flat/pivot_chart) y compile
    key = "area"
    label = "Gráfico de Áreas"
```

Después se agrega `area` al import de `widgets/__init__.py` y se crea `area-widget.js` en el frontend.

### Ejemplo: widget con una forma nueva

```python
# engine/plans/bins.py
@dataclass(frozen=True)
class BinsResult(PlanResult):
    edges: list
    counts: list


@PLANS.register
class BinsPlan(ResultPlan[BinsResult]):
    key = "bins"
    aggregates = False                  # trabaja con las filas, no con grupos

    def run(self, spec, data):
        counts, edges = np.histogram(data.df[spec.columns[0]].dropna(), bins=10)
        return BinsResult(edges=edges.tolist(), counts=counts.tolist())


# widgets/histogram.py
@WIDGETS.register
class HistogramWidget(WidgetType[BinsResult, ViewOptions]):
    key = "histogram"
    label = "Histograma"
    capabilities = DataCapabilities(dimensions=(0, 0), pivots=(0, 0), columns=(1, 1),
                                    metrics=(0, 0), metric_types=frozenset(),
                                    having=False, sort=False, limit=False)
    plan_key = "bins"

    def default_title(self, spec, options):
        return f"Distribución de {spec.columns[0]}"

    def compile(self, result, options, spec):
        labels = [f"{a:g}–{b:g}" for a, b in zip(result.edges, result.edges[1:])]
        return {"categories": labels, "series": [{"name": "Filas", "data": result.counts}]}
```

La tabla de datos (`table`) se agregó así: un plan nuevo (`engine/plans/rows.py`) y un widget nuevo (`widgets/table.py`), más el campo `columns` en `DataSpec` porque ningún widget anterior mostraba columnas sin agrupar.

`tests/test_architecture.py` hace exactamente esto con un widget, una métrica y un plan de prueba, y comprueba que funcionan de punta a punta (validación, tool de la IA, choices del modelo y `WidgetService`) sin tocar ningún otro módulo.

---

## 7. Pruebas

`python manage.py test sheets_reports` corre 213 tests, organizados por capa:

| Archivo | Qué cubre |
|---|---|
| `tests/dsl/test_validation.py` | schemas, condiciones, métricas (referencias, ciclos, agrupadas), capacidades por widget |
| `tests/engine/test_plans.py` | cálculos de cada plan, porcentajes, relativos, having/Top N, tendencia, orden de evaluación de las métricas |
| `tests/widgets/test_compile.py` | formato que recibe el frontend en cada widget |
| `tests/widgets/test_rule_order.py` | el orden de los errores lo deciden etapa y prioridad, no la posición en `rules()` |
| `tests/widgets/test_view_options.py` | cada fila de la política vista → datos |
| `tests/widgets/test_table.py` | tabla de datos: validación, plan `rows`, compilación y creación por la API |
| `tests/test_architecture.py` | extensión por registro de punta a punta y reglas de capas |
| `tests/test_views.py`, `tests/test_ai_spec.py` | endpoints HTTP y generación con IA (con Gemini simulado) |

`tests/fixtures.py` ofrece los helpers comunes: `spec`, `agg`, `calc`, `errors_for`, `view`, `compiled` y `execute`.
