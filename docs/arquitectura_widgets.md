# Arquitectura orientada a objetos del backend de widgets (propuesta)

## Contexto
Hoy cada widget tiene un JSON `data_spec`, que dice qué calcular, y un JSON `view_spec`, que dice cómo mostrarlo. Esos JSON están bien. El problema es que la lógica de cada tipo de widget está repartida por condicionales en 5 módulos:

| Qué | Dónde está hoy |
|---|---|
| Restricciones por tipo (KPI sin dimensión, dona sin pivote…) | `spec_validation.build_envelope_schema` (if/then) + `_semantic_errors` (`if widget_type == "kpi"`) + `PIVOT_MULTIMETRIC_TYPES` |
| Tipos de métrica (`agg`/`calc`/`grouped`) | schema en `spec_validation`, validación en `_metric_errors`, ejecución en `query_engine._scalar_metrics/_flat_table/...` (`if m["type"] == ...`) |
| Operadores de filtro, valores relativos, agregaciones, ops de calc | listas en `spec_validation` + dicts/ifs en `query_engine` (`_COMPARATORS`, `_AGG_FUNCS`, `_CALC_FUNCS`, `_condition_errors`) |
| view_spec | `build_view_spec` con if/elif por tipo, `kpi_view` |
| Forma del resultado / compilación | `views._render_widget` (`layout="table"`), `run_data_spec`, `apex_compiler._COMPILERS` |
| Opciones de vista para la IA | `ai_spec.build_tool_parameters` (KPI hardcodeado) + `_normalize` |

Por eso, agregar un widget o una métrica obliga a tocar 4 o 5 archivos y a acordarse de cada `if`. **Objetivo:** que cada concepto (widget, métrica, operador, agregación) sea una clase que declare su esquema, su validación, su ejecución y su presentación, y que se registre en un registro. El resto del sistema recorre el registro y ya no pregunta por tipos concretos.

Restricciones: los specs se siguen guardando como JSON. La IA necesita un JSON Schema plano para Gemini, sin if/then. Las columnas válidas de los enums se calculan en cada llamada a partir de la hoja. No hace falta compatibilidad hacia atrás (ver memoria): se puede rediseñar el formato si conviene.

---

## 1. Estructura de paquetes

```
sheets_reports/
  dsl/                          # el lenguaje: piezas reutilizables, sin conocer widgets
    registry.py                 # Registry genérico (register, unregister, get, keys)
    context.py                  # SheetContext
    values.py                   # to_python, to_key, percent, sort_key
    errors.py                   # SpecValidationError + mensajes legibles de jsonschema
    schema.py                   # helpers JSON Schema (enum, union, límites globales)
    conditions.py               # FilterOperator, RelativeValue, Condition (+ registros)
    aggregations.py             # Aggregation (+ registro)
    calc_ops.py                 # CalcOp (+ registro)
    groups.py                   # GroupCondition (having / inner_having)
    metrics/
      base.py                   # Metric (ABC), METRICS, evaluation_order, metrics_errors
      evaluation.py             # scalar_values, flat_table
      agg.py  calc.py  grouped.py   # grouped.py trae también GroupResult (+ GROUP_RESULTS)
    spec.py                     # DataSpec, Sort, Limit + su JSON Schema
    rules.py                    # Rule, Stage, bandas y reglas genéricas
  engine/
    executor.py                 # run(spec, df, plan): filtros, universo, Top N/having
    plans/                      # PLANS; un archivo por forma de resultado
      base.py                   # ResultPlan[R], PlanResult, PlanInput
      scalar.py  flat.py  pivot_chart.py  pivot_table.py   # plan + su *Result tipado
  widgets/
    base.py                     # WidgetType, DataCapabilities, ViewOptions, WIDGETS
    presentation.py             # humanize, claves de campos de la tabla
    chart.py                    # ChartWidget (base de bar y line)
    kpi.py  bar.py  line.py  donut.py  table.py
  services/
    widget_service.py           # casos de uso: create / update_spec / board_filters / render
    ai_spec.py                  # arma la tool de Gemini desde los registros
    sheets.py                   # sin cambios
```

Los módulos anteriores `spec_validation.py`, `query_engine.py` y `apex_compiler.py` se repartieron entre `dsl/`, `engine/` y `widgets/` y se eliminaron.

---

## 2. Piezas base

### SheetContext
Reemplaza el dict `schema` que hoy se pasa a todas las funciones.
```python
@dataclass(frozen=True)
class SheetContext:
    source: str                    # gid
    fields: tuple[str, ...]
    numeric_fields: frozenset[str]
    samples: dict[str, list] = field(default_factory=dict)   # para la IA
    for_ai: bool = False           # el schema se genera sin if/then

    @classmethod
    def from_dataframe(cls, df, source, **kw): ...
    def is_numeric(self, col) -> bool: ...
```

### Registry genérico
```python
class Registry(Generic[T]):
    def __init__(self, kind: str): ...
    def register(self, cls):            # se usa como decorador; la clave es cls.key
    def get(self, key) -> T             # lanza error legible si la clave no existe
    def keys(self) -> list[str]         # alimenta los enum del JSON Schema
    def __iter__(self)
```
Hay un registro por concepto: `FILTER_OPS`, `RELATIVE_VALUES`, `AGGREGATIONS`, `CALC_OPS`, `METRICS`, `WIDGETS`. **Los enums del JSON Schema se generan siempre con `registry.keys()`**, así que las listas `AGGS`, `FILTER_OPS`, etc. desaparecen.

---

## 3. Vocabulario del DSL: estrategias pequeñas

Cada operación es una clase con su regla y su implementación en el mismo lugar. Hoy esas dos mitades están separadas entre `spec_validation` y `query_engine`.

```python
class FilterOperator(ABC):
    key: str
    value_kind: ValueKind          # NONE | SCALAR | LIST | RANGE | TEXT
    numeric_only: bool = False
    allows_relative: bool = False
    def validate(self, cond, ctx, path) -> list[SpecError]   # por defecto, reglas de value_kind
    @abstractmethod
    def mask(self, series: pd.Series, value) -> pd.Series

@FILTER_OPS.register
class Between(FilterOperator):
    key, value_kind, numeric_only = "between", ValueKind.RANGE, True
    def mask(self, s, v): return s.between(*v)
```

```python
class Aggregation(ABC):            # sum, avg, count, count_distinct, min, max, median
    key: str
    needs_field = True             # count -> False
    numeric_only = True            # count_distinct -> False
    empty_value = None             # sum/count -> 0
    def by_group(self, grouped, field) -> pd.Series
    def total(self, df, field)
```

```python
class CalcOp(ABC):                 # add, sub, mul, div, ratio_pct, diff_pct
    key: str; divides = False; is_percent = False
    def apply(self, left, right)   # funciona tanto con escalares como con Series
```

```python
class RelativeValue(ABC):          # current_year, max, second_max…
    key: str
    def resolve(self, series, today) -> Any
```

**Agregar un operador, una agregación o un valor relativo** = escribir una clase de unas 5 líneas y registrarla. Sin tocar el schema ni el engine.

---

## 4. Métricas (polimorfismo en lugar de `if m["type"]`)

```python
class Metric(ABC):
    key: ClassVar[str]                       # "agg" | "calc" | "grouped"
    # Sin referencias a widgets: qué widget admite qué métrica lo declara el widget
    # (DataCapabilities.metric_types), nunca la métrica.

    # --- esquema y parseo
    @classmethod
    @abstractmethod
    def schema(cls, ctx: SheetContext, *, nested=False) -> dict
    @classmethod
    def parse(cls, raw: dict) -> "Metric"    # dict validado -> objeto

    alias: str                               # "as"

    # --- validación semántica (después del schema)
    def validate(self, ctx, scope: "MetricScope", path) -> list[SpecError]
    def depends_on(self) -> set[str]         # calc: refs; el engine evalúa en orden topológico

    # --- presentación
    def is_percent(self) -> bool
    def is_numeric(self) -> bool             # grouped top/bottom -> False

    # --- ejecución
    def scalar(self, ev: "EvalContext"): ...             # KPI
    def by_group(self, ev: "EvalContext", keys) -> pd.Series   # gráficos/tablas
    def supports_trend(self) -> bool
```
- `AggMetric(alias, aggregation: Aggregation, field, show_as, filters)`: delega en `Aggregation`.
- `CalcMetric(alias, op: CalcOp, left, right)`: delega en `CalcOp`. Sus `depends_on()` son `left` y `right`.
- `GroupedMetric(alias, group_by, inner: list[Metric], inner_having, result: GroupResult, value)`: `GroupResult` es otra estrategia registrada (count, pct_groups, top…). Su filtro de grupos se llama **`inner_having`** y no `having`: tiene otro alcance que `DataSpec.having` (ver §5).

`EvalContext` lleva `df` (ya filtrado), `universe` (para los %), `values` (las métricas ya calculadas, para los calc) y `ctx`.

**Orden de evaluación ≠ orden de presentación.** El engine no evalúa en el orden de la lista. Evalúa en orden topológico según `depends_on()`, con `evaluation_order(metrics)` en `dsl/metrics/base.py`:
```python
for m in evaluation_order(spec.metrics):   # calc después de sus referencias
    values[m.alias] = m.scalar(ev)
```
El orden de `spec.metrics` solo decide el orden de columnas, series y leyenda. Así, reordenar métricas arrastrándolas en el drawer (`dashboard-store.js`, `metricsSortable`) **nunca puede romper un cálculo**, y no hace falta validar nada al soltar. La regla `MetricReferencesBackward` se reemplaza por dos reglas:
- `MetricReferencesExist`: `left` y `right` apuntan a un alias del mismo nivel.
- `NoMetricCycles`: `a = b + 1` y `b = a * 2` no pueden coexistir.

La validación de tipo (que un `calc` no use una métrica `top`/`bottom`) consulta el alias por nombre, no por posición.

`METRICS.schema_union(ctx)` arma la unión discriminada: `if/then` para jsonschema, `anyOf` cuando `ctx.for_ai`. Reemplaza a `metric_schema` y `_union`.

**Agregar un tipo de métrica** = una clase `Metric` con schema, validate y scalar/by_group, registrada.

---

## 5. DataSpec: objeto de valor tipado

```python
@dataclass
class DataSpec:
    source: str
    dimensions: list[str]
    pivots: list[str]
    filters: list[Condition]
    metrics: list[Metric]
    having: list[GroupCondition]
    sort: Sort | None
    limit: Limit | None
    trend_by: str | None

    @classmethod
    def from_dict(cls, raw) -> "DataSpec"   # solo después de validar el schema
    def to_dict(self) -> dict               # lo que se guarda en Widget.data_spec
    def metric(self, alias) -> Metric
    @property
    def aliases(self) -> list[str]
```
El JSON guardado conserva su forma, con un único cambio: `metrics[].having` de las métricas agrupadas pasa a llamarse `inner_having`. Lo demás es igual; la diferencia es que el código trabaja con objetos en lugar de dicts.

**Dos filtros de grupos, dos nombres.** Comparten el tipo `GroupCondition` (`{left, op, right}`), pero no el alcance ni la validación:

| Campo | Filtra | Sus referencias apuntan a | Dónde se valida |
|---|---|---|---|
| `DataSpec.having` | grupos de la primera dimensión del widget (filas del resultado) | aliases de `spec.metrics` | regla `HavingRefsWidgetMetrics` |
| `GroupedMetric.inner_having` | grupos de `group_by` dentro de una sola métrica KPI | aliases de `metric.inner` | `GroupedMetric.validate` |

Las dos usan `GroupCondition.validate(names=..., path=...)` y reciben el conjunto de nombres de forma explícita. Nunca se infiere del contexto.

---

## 6. WidgetType: el núcleo

Cada widget declara **capacidades** (datos), **opciones de vista** (presentación), **reglas propias** (validación), **plan de ejecución** y **compilación**. El esquema envolvente y las reglas genéricas se derivan de las capacidades, así que desaparecen los `if/then` hardcodeados de `build_envelope_schema`.

```python
@dataclass(frozen=True)
class DataCapabilities:
    dimensions: tuple[int, int]          # (min, max)  KPI (0,0), gráficos (1,1), tabla (1,3)
    pivots: tuple[int, int]              # dona (0,0), gráficos (0,1), tabla (0,2)
    max_metrics: int                     # KPI 4, dona 1, resto 5
    metric_types: frozenset[str]         # KPI {"agg","calc","grouped"}; resto {"agg","calc"}
    multi_metric_with_pivot: bool        # solo la tabla
    having: bool; sort: bool; limit: bool; trend: bool   # KPI: trend sí; resto: having/sort/limit

class ViewOptions(ABC):                  # dataclass por widget
    title: str; labels: dict[str, str]; display: dict
    @classmethod
    def schema(cls, spec_or_ctx, *, for_ai=False) -> dict   # opciones de la tool de IA
    @classmethod
    def parse(cls, raw, spec: DataSpec, previous: dict | None) -> Self   # sanea + merge
    def reconcile(self, spec: DataSpec) -> Self   # ver "Referencias de la vista a los datos"
    def to_view(self, spec: DataSpec) -> dict     # lo que se guarda en Widget.view_spec

R = TypeVar("R", bound=PlanResult)      # ScalarResult, FlatResult, ...
V = TypeVar("V", bound=ViewOptions)

class WidgetType(ABC, Generic[R, V]):
    key: ClassVar[str]
    label: ClassVar[str]
    capabilities: ClassVar[DataCapabilities]
    options_cls: ClassVar[type[V]]
    plan_key: ClassVar[str]             # clave en PLANS ("scalar", "flat", ...), no una clase importada

    # --- data_spec
    def data_schema(self, ctx) -> dict              # schema base + límites de capabilities
    def rules(self) -> list[Rule]                   # genéricas + específicas del widget
    def validate(self, raw, ctx) -> DataSpec        # lanza SpecValidationError

    # --- view_spec
    def build_view(self, spec: DataSpec, options: V) -> dict   # options.reconcile(spec).to_view(spec)
    def default_title(self, spec, options) -> str

    # --- ejecución y presentación
    def plan(self, spec: DataSpec) -> ResultPlan[R]    # por defecto PLANS.get(self.plan_key)
    def execute(self, spec: DataSpec, df) -> R          # engine.run(spec, df, self.plan(spec))
    @abstractmethod
    def compile(self, result: R, options: V) -> dict    # hoy está en apex_compiler._compile_*
```

`metric_types` invierte la dependencia que antes estaba en `Metric.widget_scope`: la capa `dsl/` no conoce ningún widget. Es el widget el que dice qué tipos de métrica acepta. Con eso, `data_schema` puede limitar la unión de métricas a `METRICS.schema_union(ctx, only=capabilities.metric_types)`. La IA ni siquiera ve `grouped` cuando el widget no lo admite. Si mañana otro widget necesita métricas agrupadas, se toca solo `widgets/<nuevo>.py`.

`compile` recibe las opciones ya tipadas (`V`), no el dict `view_spec`. El dict es solo el formato con que se guarda: `options_cls.from_view(view_spec)` lo reconstruye al renderizar.

`validate` es un *template method*, igual para todos los widgets:
1. Reglas de la etapa `PRE_SCHEMA`, sobre el dict crudo. Si una devuelve errores, se corta aquí (ver más abajo).
2. `jsonschema` contra `self.data_schema(ctx)`. Los mensajes legibles salen de `_readable`, que pasa a `dsl/errors.py`.
3. `DataSpec.from_dict`.
4. Reglas de la etapa `SEMANTIC`, ordenadas por prioridad.

**Reglas** (`dsl/rules.py`), una clase por regla. Cada una sale de `_semantic_errors` / `_metric_errors`.

**El orden de las reglas está declarado; no depende de dónde se agreguen a la lista.** Cada regla dice en qué etapa corre, con qué prioridad y si corta la validación:
```python
class Stage(IntEnum):
    PRE_SCHEMA = 0     # sobre el dict crudo, antes de jsonschema
    SEMANTIC = 1       # sobre DataSpec

class Rule(ABC):
    stage: ClassVar[Stage] = Stage.SEMANTIC
    priority: ClassVar[int] = 100       # menor = antes; empates: orden de rules()
    blocking: ClassVar[bool] = False    # True: si falla, se devuelven solo sus errores
    @abstractmethod
    def check(self, spec, widget, ctx) -> list[SpecError]

# en WidgetType.validate (orden estable: sorted conserva el orden de rules() en los empates)
rules = sorted(self.rules(), key=lambda r: (r.stage, r.priority))
```
Bandas de prioridad (constantes en `dsl/rules.py`):

| Banda | Prioridad | Reglas |
|---|---|---|
| `BUSINESS` | 0 | Reglas con mensaje de negocio para el usuario, como `PivotMultiMetric`: stage `PRE_SCHEMA`, `blocking=True`. Hoy esto es `_pivot_multimetric_error`, que corre antes del schema y devuelve solo su mensaje. |
| `STRUCTURE` | 100 | Columnas, dimensiones y pivotes: `NoColumnRepeated`, `PivotNeedsDimension`, `AliasNotColumn`. Capacidades: `HavingAllowed`, `SortAllowed`, `LimitAllowed`, `TrendAllowed`, `MetricTypeAllowed`. |
| `METRICS` | 200 | `UniqueAliases`, `MetricsValid` (delega en `metric.validate`), `MetricReferencesExist`, `NoMetricCycles`, `ConditionsValid`. |
| `REFERENCES` | 300 | Lo que apunta a métricas ya validadas: `HavingRefsWidgetMetrics`, `SortTargetExists`, `LimitNeedsMetricSort`. |

Las reglas específicas se agregan sobrescribiendo `rules()`. Por ejemplo, el KPI agrega `TrendNeedsNonGroupedMetric` (banda `REFERENCES`). La regla trae su prioridad, así que su posición en la lista no importa.

Un test dedicado (`tests/widgets/test_rule_order.py`) fija el invariante: con un spec que viola a la vez `PivotMultiMetric` y otra regla, el único error es el de negocio. Para cada widget, `rules()` ordenadas cumplen que las bandas no bajan. Este test sustituye el chequeo implícito de hoy, que depende de que `validate_widget_spec` llame primero a `_pivot_multimetric_error`.

### Referencias de la vista a los datos

Algunas opciones de vista nombran cosas del `data_spec`: `KpiOptions.primary`, `compare`, `target`; `labels`; `ChartOptions.stacked`, que necesita pivote. **La política es explícita: la vista se reconcilia con los datos y nunca invalida un spec.** Editar datos no debe fallar por una preferencia de presentación. `ViewOptions.reconcile(spec)` aplica estas reglas de forma determinista:

| Opción | Si los datos no la sostienen | Hoy |
|---|---|---|
| `primary` inexistente | → primera métrica | igual (`kpi_view`) |
| `compare` inexistente o no numérica | → `None` | igual |
| `compare_mode` con `compare=None` | → `None` (antes quedaba `"pct"` aunque no hubiera comparación) | **cambia** |
| `target` (alias) inexistente | → `None` | igual |
| `status.basis="target_pct"` sin `target` | → `status=None` | igual |
| `stacked` sin pivote | → `False` | igual |
| `labels` de aliases o columnas que ya no existen | se descartan | **cambia** (hoy se conservan) |

Cada fila tiene un test unitario en `tests/widgets/test_view_options.py`.

**Aclaración sobre `trend_by` y `compare`.** No son dos mitades de la misma función ni una está en la capa equivocada:
- `DataSpec.trend_by` es el eje de la *sparkline*: la serie de la métrica principal por esa columna. Decide **qué se calcula**, así que pertenece a los datos.
- `KpiOptions.compare` es **contra qué métrica** se muestra la variación ▲/▼. «Este año vs el anterior» se arma con **dos métricas `agg`** con filtros relativos (`current_year` / `previous_year`) y `compare` apuntando a la segunda. No usa `trend_by`.

Por eso no hace falta ninguna regla de consistencia entre los dos. Un KPI puede tener tendencia sin comparación, comparación sin tendencia, ambas o ninguna, y los cuatro estados son válidos. La única dependencia real es `compare_mode` → `compare`, que es interna a la vista y está en la tabla de arriba.

### Widgets concretos
```python
@WIDGETS.register
class KpiWidget(WidgetType[ScalarResult, KpiOptions]):
    key, label = "kpi", "Tarjeta KPI"
    capabilities = DataCapabilities(dimensions=(0,0), pivots=(0,0), max_metrics=4,
                                    metric_types=frozenset({"agg", "calc", "grouped"}),
                                    multi_metric_with_pivot=False,
                                    having=False, sort=False, limit=False, trend=True)
    options_cls = KpiOptions        # primary, compare, compare_mode, target, higher_is_better, status
    plan_key = "scalar"
    def rules(self): return [*super().rules(), TrendNeedsNonGroupedMetric()]
    def compile(self, result: ScalarResult, options: KpiOptions) -> dict: ...   # hoy: _compile_kpi

class ChartWidget(WidgetType[FlatResult | PivotChartResult, ChartOptions]):   # base de bar y line
    capabilities = DataCapabilities(dimensions=(1,1), pivots=(0,1), max_metrics=5,
                                    metric_types=frozenset({"agg", "calc"}), ...)
    options_cls = ChartOptions
    def plan(self, spec):                   # la forma depende del spec: con o sin pivote
        return PLANS.get("pivot_chart" if spec.pivots else "flat")
    def build_view(...): {"x", "seriesBy", "metrics", ...}
    def compile(self, result, options):     # hoy: _compile_chart
        match result:                       # unión cerrada: el type checker avisa si falta un caso
            case FlatResult(): ...
            case PivotChartResult(): ...

@WIDGETS.register
class BarWidget(ChartWidget):  key = "bar";  options_cls = BarOptions  # + stacked
@WIDGETS.register
class LineWidget(ChartWidget): key = "line"
@WIDGETS.register
class DonutWidget(WidgetType): ...          # pivots (0,0), max_metrics 1
@WIDGETS.register
class TableWidget(WidgetType): ...          # dims (1,3), pivots (0,2), multi_metric_with_pivot=True, PivotTablePlan
```

---

## 7. Engine: planes de resultado

`QueryEngine.run(spec, df, plan)` reúne los pasos comunes que hoy están en `run_data_spec`: resolver los valores relativos, guardar el universo y aplicar los filtros. Después delega en el plan. El plan también es un **registro** (`PLANS`), igual que `WIDGETS`. Así, un widget con una forma de datos nueva agrega su plan en un archivo propio y nunca edita `engine/executor.py`.

```python
class PlanResult(ABC): ...              # marcador; cada plan devuelve su propio tipo

class ResultPlan(ABC, Generic[R]):
    key: ClassVar[str]
    aggregates: ClassVar[bool] = True   # False: filas crudas (no aplica Top N/having)
    @abstractmethod
    def run(self, spec: DataSpec, ev: EvalContext) -> R

PLANS: Registry[ResultPlan] = Registry("plan")
```

Cada plan está emparejado con una **clase de resultado tipada** (dataclasses congeladas). `compile` deja de recibir `Any`:

| Plan (`key`) | Resultado | Hoy |
|---|---|---|
| `scalar` | `ScalarResult(values: dict[str, Scalar \| Ranked], trend: Trend \| None)` | dict con la clave mágica `"__trend"` |
| `flat` | `FlatResult(dimension, rows: pd.DataFrame, totals: dict)` | DataFrame con `attrs["totals"]` |
| `pivot_chart` | `PivotChartResult(dimension, pivot, metric, dimension_values, pivot_values, rows, row_totals, column_totals, grand_totals)` | dict anidado documentado en un docstring |
| `pivot_table` | `PivotTableResult(...)` | dict de `_run_pivot_table` |

Con esto se van la clave `"__trend"` mezclada con los aliases y el `attrs["totals"]` escondido en el DataFrame. Los dos son contratos implícitos de hoy entre `query_engine` y `apex_compiler`.

`_restrict_groups` (Top N y `having`) se aplica solo cuando `plan.aggregates` es `True`. Un plan de filas crudas, como un scatter, se lo salta.

El engine no conoce widgets ni tipos de métrica; solo llama a métodos polimórficos. Se mantiene la garantía de que no se evalúa texto: todo pasa por estrategias registradas.

---

## 8. Servicios, vistas, IA y modelo

- **`WidgetService`** reúne lo que hoy está en `views.py` (`_builder_data_spec`, `_view_options`, `_render_widget`):
  ```python
  class WidgetService:
      def __init__(self, dashboard): self.ctx, self.df = load_sheet(dashboard)
      def create(self, widget_type, payload) -> Widget
      def update_spec(self, widget, payload) -> Widget
      def render(self, widget, board_filters=()) -> dict   # aísla errores por widget
  ```
  Las vistas quedan como adaptadores HTTP: parsean el request, llaman al servicio y serializan.
- **IA (`ai_spec`)**: `build_tool_parameters` arma la tool con `WIDGETS.keys()`, `widget.data_schema(ctx.for_ai)` y la unión de `options_cls.schema(for_ai=True)`. Así el bloque `kpi` deja de estar hardcodeado. `_normalize` pasa a `widget.options_cls.parse(...)`. El bucle de reintento no cambia y llama a `widget.validate`.
- **Modelo**: `Widget.type` usa `choices=WIDGETS.choices()`, y se agrega una propiedad `Widget.definition -> WIDGETS.get(self.type)`. `data_spec` y `view_spec` siguen siendo JSONField.

---

## 9. Cómo queda agregar algo nuevo

| Quiero agregar | Qué toco |
|---|---|
| Widget nuevo **con una forma de datos que ya existe** (ej. `area` = `flat`/`pivot_chart`, `funnel` = `flat`) | `widgets/<nuevo>.py`: subclase con `capabilities`, `options_cls`, `plan_key` (o `plan()`), `compile` y `@WIDGETS.register`. Más su componente en el frontend. Nada más en el backend: el schema, la IA, los choices y la validación lo toman de los registros. |
| Widget nuevo **con una forma de datos nueva** (ej. `scatter`: un punto por fila cruda, dos métricas como x/y) | Lo anterior, más **una segunda pieza**: `engine/plans/<forma>.py` con un `ResultPlan` registrado en `PLANS` y su clase `PlanResult`. Esto **sí es lógica de procesamiento de datos**, pero queda aislada en su clase con una interfaz fija. No se edita `executor.py` ni otro plan. |
| Tipo de métrica (ej. `running_total`) | `dsl/metrics/running_total.py` con una subclase de `Metric`, registrada. |
| Agregación (ej. `stddev`) | Una clase `Aggregation` de unas 5 líneas. |
| Operador de filtro (ej. `starts_with`) | Una clase `FilterOperator`. |
| Valor relativo (ej. `last_30_days`) | Una clase `RelativeValue`. |
| Regla de negocio | Una clase `Rule`, en `rules()` del widget o en las genéricas. |

---

## 10. Migración incremental (los ~170 tests como red de seguridad)
1. `dsl/registry.py` y `SheetContext`. Convertir operadores, agregaciones, ops de calc y valores relativos en estrategias. `spec_validation` y `query_engine` empiezan a usarlas por dentro. Los tests no cambian.
2. Clases `Metric` y `DataSpec`. El engine deja de usar `if m["type"]` y evalúa en orden topológico (`evaluation_order`). Se renombra `having` → `inner_having` en las métricas agrupadas, en estos lugares: schema, `SYSTEM_PROMPT` de `ai_spec`, `dashboard-store.js` y una migración de datos que reescribe los widgets guardados. Sin shim de compatibilidad.
3. `WidgetType` con capacidades (`metric_types` incluido), reglas con `Stage`/`priority`/`blocking` y `ViewOptions.reconcile`. `validate_widget_spec`, `build_view_spec` y `compile_view` quedan como fachadas finas que delegan en `WIDGETS.get(type)`.
4. Registro `PLANS` con resultados tipados (`ScalarResult`, `FlatResult`, …). Mover `apex_compiler._compile_*` a `widget.compile(result: R, options: V)`.
5. `WidgetService`, adelgazar `views.py` y conectar `ai_spec` a los registros.
6. Borrar las fachadas y los módulos viejos. Reordenar los tests por paquete (`tests/dsl/`, `tests/widgets/`, `tests/engine/`).

Después de cada paso: `python manage.py test sheets_reports`.

## Verificación
- Toda la suite pasa en cada paso (`python manage.py test sheets_reports`).
- Test nuevo de registro: agregar en el test un `DummyWidget` y un `DummyMetric` y comprobar que aparecen en el schema de la IA, en la validación y en el render sin tocar otros módulos. Agregar también un `DummyPlan` con un `DummyResult` propio, para probar el caso de la forma de datos nueva. Esto prueba que el diseño escala.
- Test de capas: un test recorre los imports de `dsl/` y falla si alguno importa de `widgets/` o contiene un `key` de widget. Así la regla de capas no depende de la disciplina de nadie.
- Test de orden de métricas: un `calc` colocado antes de las métricas que referencia da el mismo resultado que en el orden natural. Un ciclo da error de validación.
- Tests de orden de reglas y de `ViewOptions.reconcile` (ver §6).
- Prueba manual: crear y editar widgets de cada tipo desde el builder y con la IA en `board_editor`, y aplicar filtros del tablero en `board_view`.

---

## 11. Revisión del diseño (decisiones)

| # | Observación | Decisión | Dónde |
|---|---|---|---|
| 1 | La §9 prometía «nada más en el backend» incluso para formas de datos nuevas | Aceptada. `PLANS` pasa a ser un registro y la §9 separa «forma existente» de «forma nueva», que sí suma un `ResultPlan` aislado. | §7, §9 |
| 2 | `GroupedMetric.widget_scope = {"kpi"}` hacía que `dsl/` conociera widgets | Aceptada. Se invierte la dependencia con `DataCapabilities.metric_types`, y un test de capas impide que vuelva. | §4, §6 |
| 3 | `trend_by` y `compare` sin regla de consistencia | Aceptada en parte. No son la misma función: `trend_by` es la sparkline (datos) y `compare` es la métrica contra la que se compara (vista). Pueden combinarse libremente. Lo que sí faltaba era declarar la política general vista→datos: `ViewOptions.reconcile` corrige sin fallar, y `compare_mode` sin `compare` pasa a `None`. | §6 |
| 4 | Reordenar métricas arrastrando rompe los `calc` | Aceptada, y se resuelve en el diseño en vez de validar al soltar. La evaluación sigue el orden topológico de `depends_on()`, así que el orden de la lista solo afecta la presentación. Las reglas pasan a ser «la referencia existe» y «no hay ciclos». | §4 |
| 5 | `execute -> Any` / `compile(result, view: dict)` sin tipos | Aceptada. Cada `ResultPlan[R]` tiene su `PlanResult` (dataclass) y `WidgetType[R, V]` tipa `compile(result: R, options: V)`. | §6, §7 |
| 6 | El orden de las reglas decidía el mensaje sin estar declarado | Aceptada. `Rule.stage`, `priority` en bandas y `blocking`, más un test dedicado al invariante. | §6 |
| 7 | `having` con dos alcances distintos | Aceptada. `GroupedMetric.having` → `inner_having`. Comparte el tipo `GroupCondition`, pero los nombres válidos se pasan siempre de forma explícita. | §4, §5, §10 |

---

## 12. Implementación (estado actual)

El diseño está implementado. Suite: **201 tests** (`python manage.py test sheets_reports`), organizados por capa: `tests/dsl/`, `tests/engine/`, `tests/widgets/`, más `test_architecture.py`, `test_views.py` y `test_ai_spec.py`.

Diferencias con el boceto de las secciones anteriores, decididas al implementar:

| Boceto | Implementado | Por qué |
|---|---|---|
| `SpecError(path, message)` | Los errores son `str` con la ruta como prefijo (`"metrics[0].as: …"`) | Es lo que consumen el builder y el reintento de la IA; no hacía falta otro tipo. |
| `SheetContext.for_ai` | `for_ai` es un argumento de los métodos `schema(...)` | El contexto describe la hoja; para quién es el schema lo decide quien lo pide. |
| `QueryEngine.run` | Función `engine.executor.run(spec, df, plan)` | No tiene estado; una clase solo agregaba ceremonia. |
| `compile(result: R, options: V)` | `compile(result: R, options: V, spec: DataSpec)` | Los porcentajes y el orden de las métricas salen del spec, no del view_spec guardado. |
| Regla `MetricTypeAllowed` | El schema de cada widget limita la unión de métricas a `capabilities.metric_types` | Error más temprano, y la IA ni siquiera ve los tipos que el widget no admite. |
| Reglas `MetricReferencesExist` / `NoMetricCycles` | `CalcMetric.validate` (la referencia existe y es numérica) + `metrics_errors` (ciclos) | Viven donde está la semántica de la métrica; también aplican a las internas de `grouped`. |
| KPI → `TrendNeedsNonGroupedMetric` | `TrendNeedsTrendableMetric` (usa `Metric.supports_trend()`) | Un `grouped` con `count` sí tiene tendencia; solo top/bottom no. |
| `widgets/registry.py` | `WIDGETS` vive en `widgets/base.py`; `widgets/__init__.py` importa los widgets para registrarlos | Un módulo menos. |
| `WidgetService(dashboard)` carga la hoja | `WidgetService(dashboard, df)` | El servicio no hace I/O; las vistas cargan la hoja (cacheada) y la pasan. |
| `ViewOptions.parse` | `from_request(data, previous)` / `from_view(view_spec)` / `reconcile(spec)` | Cubre los tres orígenes: builder (con merge sobre lo guardado), IA y view_spec. |

Además:
- **Frontend.** `metricAliases` (`dashboard-store.js`) resuelve primero las métricas que no son cálculos y después los cálculos en orden de dependencias. Arrastrar un cálculo por encima de sus operandos ya no lo deja sin alias. La métrica agrupada usa `innerHaving` ↔ `inner_having`.
- **Migración `0003`.** Los choices de `Widget.type` salen de `WIDGETS` (`widget_type_choices`) y los `having` de las métricas agrupadas guardadas se renombran a `inner_having`.
- **Garantías con test.** `test_architecture.py` registra un widget, una métrica y un plan de prueba y los usa de punta a punta sin tocar otros módulos. También verifica por AST que `dsl/` no importa `widgets/`, `engine/` ni `services/` y no nombra widgets concretos, y que `engine/` no importa `widgets/`.
