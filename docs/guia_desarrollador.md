# Guía del desarrollador

Referencia de cada artefacto del proyecto: módulos, clases, métodos, funciones, constantes, plantillas y endpoints, en el backend y en el frontend. [`arquitectura_widgets.md`](arquitectura_widgets.md) explica **por qué** el sistema está dividido así y **cómo se agrega algo nuevo**; esta guía dice **qué hace cada pieza** y dónde está.

Convenciones:
- Las rutas son relativas a `sheets_reports/` salvo que se diga otra cosa.
- `ctx` es siempre un `SheetContext` (la hoja: columnas, numéricas, gid).
- *spec* es el `data_spec` (qué calcular); *view* es el `view_spec` (cómo mostrarlo).
- Los métodos marcados *(cls)* son `@classmethod`; *(static)*, `@staticmethod`; *(prop)*, `@property`.

---

## Índice

1. [Puesta en marcha](#1-puesta-en-marcha)
2. [Mapa mental en un minuto](#2-mapa-mental-en-un-minuto)
3. [Backend: `dsl/` (el lenguaje)](#3-backend-dsl-el-lenguaje)
4. [Backend: `engine/` (la ejecución)](#4-backend-engine-la-ejecución)
5. [Backend: `widgets/` (los tipos de widget)](#5-backend-widgets-los-tipos-de-widget)
6. [Backend: servicios, vistas, modelo y configuración](#6-backend-servicios-vistas-modelo-y-configuración)
7. [API HTTP](#7-api-http)
8. [Frontend: plantillas](#8-frontend-plantillas)
9. [Frontend: JavaScript](#9-frontend-javascript)
10. [Pruebas](#10-pruebas)
11. [Glosario](#11-glosario)

---

## 1. Puesta en marcha

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # completar los valores
python manage.py migrate
python manage.py createsuperuser   # la app usa el primer superusuario si no hay sesión
python manage.py runserver
python manage.py test sheets_reports
```

Variables de entorno (`.env`, leídas en `config/settings.py` con `python-decouple`):

| Variable | Para qué |
|---|---|
| `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS` | Django estándar. |
| `REPORT_PATH` | Prefijo de ruta en producción (ej. `/sheets-reports`). El frontend lo recibe como `window.SCRIPT_NAME` y arma las URLs con `apiUrl()`. |
| `DATABASE_URL` | Base de datos; vacío = SQLite local. |
| `GOOGLE_SHEETS_CREDENTIALS_PATH` | JSON de la service account. Vacío = solo hojas públicas por enlace. |
| `GEMINI_API_KEY`, `GEMINI_MODEL` | IA que propone specs (nunca calcula). |
| `AI_AUDIT_LOG_FILE` | Archivo donde se registra cada spec generado por IA con su prompt. |
| `SHEET_CACHE_TTL` | Segundos que se cachea cada hoja. |
| `WIDGET_REFRESH_MINUTES` | Refresco automático del tablero (sin IA). |

---

## 2. Mapa mental en un minuto

```
Google Sheets ──► services/sheets.py (DataFrame cacheado)
                        │
   data_spec (JSON) ──► dsl/  ── valida (JSON Schema + reglas) ──► DataSpec tipado
                        │
                    engine/  ── ejecuta piezas (filters, having, limit) + plan ──► PlanResult
                        │
                    widgets/ ── compile(result, opciones) ──► JSON que dibuja el frontend
                        │
   views.py (HTTP) ◄── services/widget_service.py (casos de uso)
                        │
   frontend: dashboard-store.js (estado Alpine) + panel.js + widgets/*.js (dibujo)
```

- **Un widget** = `type` + `data_spec` + `view_spec` + `position` (modelo `Widget`).
- **Todo es un registro**: operadores, agregaciones, métricas, piezas del spec, planes, widgets. El código recorre registros; casi nunca pregunta `if tipo == ...`.
- **La IA solo escribe specs**. Los números los calcula `engine/` con pandas.

---

## 3. Backend: `dsl/` (el lenguaje)

`dsl/` no conoce widgets. Define el vocabulario (operadores, agregaciones, métricas) y las piezas del `data_spec`.

### 3.1 `dsl/registry.py`

| Artefacto | Descripción |
|---|---|
| `class UnknownKeyError(ValueError)` | La clave no está registrada (ej. un tipo de widget inexistente). |
| `class Registry(Generic[T])` | Registro genérico de piezas con `key`. |
| `Registry.__init__(label, *, instantiate=True)` | `label` nombra la pieza en los mensajes. Con `instantiate`, guarda una instancia (piezas sin estado); si no, la clase (métricas, piezas del spec). |
| `.register(cls)` | Decorador: registra por `cls.key`. Rechaza claves repetidas. |
| `.unregister(key)` | Quita una clave (lo usan los tests de extensiones). |
| `.get(key)` | Devuelve la pieza o lanza `UnknownKeyError`. |
| `key in registry`, `.keys()`, `.values()`, `iter(registry)` | Acceso estándar, en orden de registro. |

### 3.2 `dsl/context.py`

| Artefacto | Descripción |
|---|---|
| `@dataclass(frozen) SheetContext` | Lo que el DSL sabe de la hoja: `source` (gid), `fields` (columnas en orden), `numeric_fields` (frozenset), `samples` (valores de ejemplo, solo para la IA). |
| `from_dataframe(df, source, samples=None)` *(cls)* | Construye el contexto desde un DataFrame. |
| `is_numeric(column)` | ¿La columna es numérica? |
| `ordered_numeric_fields` *(prop)* | Numéricas en el orden de la hoja (enums estables). |
| `with_samples(samples)` | Copia con otros valores de ejemplo. |

### 3.3 `dsl/schema.py` — piezas comunes del JSON Schema

| Artefacto | Descripción |
|---|---|
| `AS_PATTERN`, `REF`, `REF_OR_NUMBER`, `SCALAR` | Patrón de alias (`^[a-z][a-z0-9_]{0,62}$`) y schemas reutilizables. |
| `MAX_METRICS=5`, `MAX_FILTERS=20`, `MAX_IN_VALUES=200`, `MAX_BOARD_IN_VALUES=5000`, `MAX_HAVING=5`, `MAX_INNER_METRICS=3`, `MAX_LIMIT=100`, `MAX_DIMENSIONS=3`, `MAX_PIVOTS=2`, `MAX_COLUMNS=50` | Límites del lenguaje. |
| `field_enum(fields)` | `{"enum": [...]}` con las columnas reales. |
| `enum_of(registry)` | Enum con las claves de un registro. |
| `union(branches, *, for_ai)` | Unión discriminada por `type`: `if/then` para jsonschema (solo errores de la rama correcta), `anyOf` para Gemini. |
| `nullable(schema)` | El schema o `null`. |

### 3.4 `dsl/errors.py` — errores legibles

| Artefacto | Descripción |
|---|---|
| `class SpecValidationError(Exception)` | Lleva `errors: list[str]`. Las vistas responden 422 con él. |
| `unique(messages)` | Sin repetidos, conservando el orden. |
| `schema_errors(schema, instance, ctx, parts)` | Valida con jsonschema y traduce cada error a español. Las piezas (`parts`) dan los mensajes de sus claves (`SpecPart.readable`). |
| `_leaf_errors`, `_readable`, `_path` | Internos: bajan por los `anyOf` y arman el mensaje con la ruta (`metrics[0].field`). |

### 3.5 `dsl/rules.py` — reglas semánticas

| Artefacto | Descripción |
|---|---|
| `class Stage(IntEnum)` | `PRE_SCHEMA` (sobre el dict crudo) o `SEMANTIC` (sobre el `DataSpec`). |
| `BUSINESS=0`, `STRUCTURE=100`, `METRICS=200`, `REFERENCES=300` | Prioridades: deciden el orden de los mensajes. |
| `class Rule` | `stage`, `priority`, `blocking` (si falla, corta y devuelve solo sus errores). |
| `Rule.check(spec, widget, ctx) -> list[str]` | Los errores encontrados. |

### 3.6 `dsl/values.py` — valores de pandas a JSON

| Función | Descripción |
|---|---|
| `to_python(value)` | numpy/pandas → tipo nativo; `NaN` → `None`. |
| `to_key(value)` | Como `to_python`, pero `2026.0` → `2026` (claves de agrupación). |
| `sort_key(value)` | Clave de orden: números antes que textos (no compara tipos distintos). |
| `percent(part, whole)` | `part/whole*100` con 2 decimales; `None` sin total. |
| `is_number(value)` | `int` o `float` que no sea `bool`. |
| `series_dict(series)` | `{clave de grupo: valor}`; claves compuestas como tuplas. |

### 3.7 `dsl/ordering.py` — orden

| Artefacto | Descripción |
|---|---|
| `MONTH_ORDER` | Nombre de mes (es/en, largo/corto) → 1..12. |
| `chronological(values)` | De lo más antiguo a lo más reciente: números, meses, fechas en texto, y orden natural para el resto (`"2025-2"` antes de `"2025-10"`). |
| `OTHERS_LABEL = "Otros"` | Etiqueta del grupo del Top N. |
| `sorted_table(table, sort)` | Tabla plana ordenada por una columna; vacíos al final. |
| `_plain`, `_natural_key` | Internos: normalizan texto y comparan números dentro del texto. |

### 3.8 `dsl/conditions.py` — condiciones sobre filas

Las condiciones `{field, op, value | relative}` son los filtros del widget, de una métrica y del tablero.

**Valores relativos** (registro `RELATIVE_VALUES`):

| Clase | `key` | Valor |
|---|---|---|
| `RelativeValue` | — | Base: `resolve(series, today)`. |
| `_ClockValue` | — | Del reloj: `from_today(today)`. |
| `CurrentYear`, `PreviousYear`, `CurrentMonth` | `current_year`, `previous_year`, `current_month` | Año actual, anterior, mes (1-12). |
| `_DataValue` | — | De los valores distintos de la columna, ordenados: `pick(values)`. |
| `MaxValue`, `SecondMaxValue`, `MinValue` | `max`, `second_max`, `min` | Último, penúltimo y primer valor. |
| `resolve_relative(series, relative, today=None)` | — | Valor concreto; `None` si la columna no tiene valores. |

**Operadores** (registro `FILTER_OPS`):

| Clase | `key` | Notas |
|---|---|---|
| `FilterOperator` | — | Base: `validate(cond, ctx, path)`, `check_value(...)`, `mask(raw, value)`. |
| `IsEmpty`, `NotEmpty` | `is_empty`, `not_empty` | Sin valor (`_NoValue`). |
| `In`, `NotIn` | `in`, `not_in` | Lista de valores (`_ListOperator._isin`). |
| `Between` | `between` | `[desde, hasta]`, columna numérica. |
| `Contains` | `contains` | Texto contenido (sin distinguir mayúsculas). |
| `Eq`, `Ne` | `eq`, `ne` | Escalar o relativo (`_Comparison`). |
| `Lt`, `Lte`, `Gt`, `Gte` | `lt`, `lte`, `gt`, `gte` | Solo columnas numéricas (`_OrderComparison`). |

**Condición y funciones**:

| Artefacto | Descripción |
|---|---|
| `@dataclass(frozen) Condition` | `field`, `op` (un `FilterOperator`), `value`, `relative`. |
| `Condition.from_dict(raw)` *(cls)* / `.to_dict()` | JSON ↔ objeto. |
| `.resolved(df, today=None)` | Copia con el relativo ya concreto. |
| `.mask(df)` | Máscara booleana de pandas. |
| `condition_schema(ctx, max_in_values)`, `conditions_schema(ctx, ...)` | JSON Schema de una condición y de la lista. |
| `parse_conditions(raw)` | Lista de dicts → lista de `Condition`. |
| `condition_errors(filters, ctx, path, max_in_values)` | Valida una lista cruda (schema + reglas del operador). La usa el filtro del tablero. |
| `conditions_errors(conditions, ctx, path)` | Reglas de cada operador sobre condiciones ya parseadas. |
| `apply_filters(df, conditions)` | El DataFrame con las condiciones aplicadas. |
| `distinct_values(series)` | Valores distintos normalizados igual que los compara una condición (opciones de la caja de filtros). |
| `_coerce_value`, `_comparable`, `_text_key`, `_blank` | Internos: adaptan tipos ("2026" vs 2026) y detectan vacíos. |

### 3.9 `dsl/aggregations.py` — agregaciones (registro `AGGREGATIONS`)

| Artefacto | Descripción |
|---|---|
| `class Aggregation` | `key`, `pandas_method`, `needs_field`, `numeric_only`, `empty_value`. |
| `.by_group(grouped, field)` | Valor por grupo de un `DataFrameGroupBy`. |
| `.total(df, field)` | Valor sobre todas las filas. |
| `Sum`, `Avg`, `Count`, `CountDistinct`, `Min`, `Max`, `Median` | `count` cuenta filas y no lleva columna; `count_distinct` acepta cualquier columna. |

### 3.10 `dsl/calc_ops.py` — operaciones de cálculo (registro `CALC_OPS`)

| Artefacto | Descripción |
|---|---|
| `class CalcOp` | `key`, `function`, `divides`, `is_percent`. |
| `.scalar(left, right)` | Escalar; `None` si falta un lado o se divide entre cero. |
| `.series(left, right)` | Vectorizado; NaN donde no se puede. |
| `Add`, `Sub`, `Mul`, `Div` | Aritmética. |
| `RatioPct` (`ratio_pct`) | `left / right × 100` (margen, % de cumplimiento). |
| `DiffPct` (`diff_pct`) | `(left − right) / right × 100` (variación). |

### 3.11 `dsl/groups.py` — condiciones sobre grupos

Se usan en `having` (grupos de la primera dimensión) y en `inner_having` (métrica «por grupo»).

| Artefacto | Descripción |
|---|---|
| `COMPARATORS` | `eq/ne/lt/lte/gt/gte` → `operator.*`. |
| `group_condition_schema()`, `group_conditions_schema()` | Schemas `{left, op, right}`. |
| `@dataclass(frozen) GroupCondition` | `left` (alias), `op`, `right` (alias o número). |
| `.from_dict` *(cls)*, `.to_dict()` | JSON ↔ objeto. |
| `.refs()` | `[(lado, alias)]` referenciados. |
| `.validate(names, path)` | Errores si referencia un alias inexistente. |
| `.mask(table)` | Máscara sobre la tabla de métricas por grupo. |
| `parse_group_conditions`, `group_conditions_errors`, `having_mask` | Lista, validación y máscara combinada (AND). |

### 3.12 `dsl/metrics/` — tipos de métrica (registro `METRICS`)

**`metrics/base.py`**

| Artefacto | Descripción |
|---|---|
| `class UnsupportedEvaluation(ValueError)` | La métrica no se puede calcular en esa forma (salvaguarda). |
| `@dataclass ScalarContext` | Evaluación a un número: `df`, `universe` (sin las condiciones del widget, para los %), `values` ya calculados. |
| `@dataclass FlatContext` | Evaluación por grupo: `df`, `dimension`, `keys`, `columns` y `totals` ya calculados. |
| `class Metric(ABC)` | Base: `key`, `label` (para mensajes), `derived` (se calcula con otras), `alias`, `show_as`, `empty_value`. |
| `schema(ctx, *, for_ai, nested)` *(cls, abstracto)* | Su JSON Schema. `nested`: dentro de otra métrica, sin filtros propios. |
| `from_dict(raw)` *(cls, abstracto)* / `to_dict()` | JSON ↔ objeto. |
| `validate(ctx, scope, path)` | Reglas propias; `scope` = métricas del mismo nivel por alias. |
| `depends_on()` | Aliases de los que depende. |
| `is_percent()`, `is_numeric()`, `supports_trend()` | Características (un top/bottom no es numérico ni tiene tendencia). |
| `resolved(df)` | Copia con relativos resueltos. |
| `without_eq_filter_on(column)` | Copia sin condiciones `eq` sobre esa columna (tendencia del KPI). |
| `scalar(ev)` *(abstracto)* | Valor sobre todas las filas (KPI). |
| `flat(fc)` | `(serie por grupo, total)`. |
| `aggregate(df, by)`, `grand_total(df)` | Valor crudo por grupo y total (antes de `show_as`). |
| `derive(values)` | Solo `derived`: valor desde `{as: valor}` de las demás. |
| `parse_metric(raw)` | Dict → instancia del tipo registrado. |
| `metric_union(ctx, *, only, nested, for_ai)` | Schema de la unión de tipos (o de los de `only`). |
| `class MetricCycleError(ValueError)` | Ciclo entre cálculos (`aliases`). |
| `evaluation_order(metrics)` | Orden topológico estable (cada una después de sus dependencias). |
| `metrics_errors(metrics, ctx, path)` | Alias únicos + reglas de cada métrica + sin ciclos. |

**`metrics/agg.py`**

| Artefacto | Descripción |
|---|---|
| `SHOW_AS = ['value', 'pct_row', 'pct_column', 'pct_total']` | «Mostrar como». |
| `AggMetric` (`agg`) | Resume una columna con una `Aggregation`, con condiciones propias (`filters`, como CALCULATE) y `show_as`. Campos: `alias`, `agg`, `field`, `show_as`, `filters`. |
| `.frame(df)` | Filas que resume: `df` recortado por sus condiciones. |
| `.aggregate`, `.grand_total`, `.scalar`, `.flat` | Cálculo en cada forma. |

**`metrics/calc.py`**

| Artefacto | Descripción |
|---|---|
| `CalcMetric` (`calc`, `derived`) | `left <op> right` entre métricas del mismo nivel (`right` puede ser número). |
| `.refs()`, `.depends_on()` | Referencias. |
| `._operand(values, ref)`, `.derive(values)` | Toma los valores ya mostrados y aplica el `CalcOp`. |

**`metrics/grouped.py`**

| Artefacto | Descripción |
|---|---|
| `class GroupResult` (registro `GROUP_RESULTS`) | Cómo se resumen los grupos que cumplen: `needs_value`, `ranking`, `is_percent`, `summarize(matching, table, metric)`. |
| `CountGroups`, `PctGroups` | Cuántos grupos cumplen / qué %. |
| `SumGroups`, `AvgGroups`, `MinGroups`, `MaxGroups` (`_Summary`) | De la métrica interna `value` entre los que cumplen. |
| `TopGroup`, `BottomGroup` (`_Ranking`) | El grupo con el mayor/menor `value`: `{"label", "value"}`. |
| `GroupedMetric` (`grouped`) | Agrupa por `group_by`, calcula `inner` (agg/calc), filtra con `inner_having` y resume con `result`. Solo la admite el KPI. |

**`metrics/evaluation.py`**

| Función | Descripción |
|---|---|
| `scalar_values(df, universe, metrics)` | `{as: valor}` en orden de evaluación; los % contra el universo. |
| `flat_table(df, dimension, metrics)` | Una fila por valor de la dimensión y una columna por métrica, con `show_as`. Base de gráficos, `having` y `limit`. |

### 3.13 `dsl/parts/` — piezas del `data_spec` (registro `SPEC_PARTS`)

Cada clave del `data_spec` es una `SpecPart`, que reúne su schema, sus reglas, su paso del pipeline, su manifiesto y su control en el panel.

**`parts/base.py`**

| Artefacto | Descripción |
|---|---|
| `@dataclass(frozen) PanelColumns` | Columnas que ofrecen los selects del panel: `all`, `numeric`, `dimension`. Vacías si el manifiesto se arma sin leer la hoja. |
| `choices(values, labels=None)` | `[{value, label}]` para un select del panel. |
| `@dataclass Rows` | Estado del pipeline: `df` y `has_others` (el Top N agrupó «Otros»). |
| `class SpecPart` | Base de las piezas. Atributos: `key`, `order` (orden de `prepare`), `describe_absent`. |
| `schema(ctx, *, for_ai)` | Su JSON Schema. |
| `parse(raw)` / `dump(value)` / `default()` | JSON ↔ valor tipado; valor cuando falta. |
| `is_empty(raw)` *(static)* | `None`, `[]` o `{}`. |
| `loose()` *(cls)* | La pieza con los límites del lenguaje (tool de la IA sin tipo). |
| `names(value)` | Columnas/aliases que introduce (reconciliación de etiquetas). |
| `sort_targets(value)` | Por qué se puede ordenar gracias a ella. |
| `resolve(value, df)` | Relativos resueltos. |
| `prepare(spec, rows, *, aggregates)` | Su paso del pipeline (filtrar filas, Top N…). |
| `rules()` | Sus reglas. |
| `readable(error, path, ctx)` | Mensaje legible de un error de su schema. |
| `absent_hint(widget)` | Mensaje si viene con valor y el widget no la tiene. |
| `manifest()` / `absent_manifest()` *(cls)* | Lo que aporta a `manifest()["data"]` con y sin ella. |
| `describe()` / `missing()` | Lo que aporta a la ficha de la IA. |
| `ui`, `panel_order`, `label`, `hint`, `group` | Su control en el panel del editor (`ui=None`: sin control). |
| `panel(columns)` | Su entrada en `manifest()["parts"]`: `{key, ui, label, hint, group, ...panel_fields}`. |
| `panel_fields(columns)` | Lo propio de su `ui` (opciones, `item_fields`…). |
| `bounds_text(name, low, high)` | `"dimensions 1–3"`. |
| `class ColumnListPart(SpecPart)` | Lista de columnas sin repetir entre `low` y `high`. Atributos de panel: `options_from` (`all`/`numeric`/`dimension`), `add_label`, `empty_label`, `empty_selectable`, `excludes`, `sortable`, `allow_all`. |
| `ColumnListPart.__init__(low=0, high=None, **panel)` | `panel`: textos propios del widget (`label=`, `hint=`…); una opción desconocida lanza `TypeError`. |
| `column_message(error, path, ctx)` | «la columna 'x' no existe en la hoja». |
| `NoRepeatedColumn(key)` | Regla: sin columnas repetidas en la lista. |
| `UnsupportedPart` | Regla PRE_SCHEMA: una clave conocida que el widget no tiene y viene con valor. |

**`parts/columns.py`**

| Pieza | `key` | Panel | Reglas / notas |
|---|---|---|---|
| `Dimensions` | `dimensions` | `column-list`, grupo `GROUPING`, `Filas`, opciones `dimension` | Destino de orden. |
| `Pivots` | `pivots` | `column-list`, grupo `GROUPING`, `Columnas`, «Sin agrupar» seleccionable | `PivotNotDimension`, `PivotNeedsDimension`. |
| `Columns` | `columns` | `column-list`, arrastrable, «Usar todas» | Destino de orden. |
| `GROUPING` | — | `{key, label, hint}` del bloque «Agrupar datos». | |

**`parts/metrics.py`**

| Artefacto | Descripción |
|---|---|
| `Metrics(low=1, high=MAX_METRICS, *, types, show_as, multi_with_pivot)` | `metrics`; `types`: claves de `METRICS` admitidas (`None` = todas). Panel: `metric-list`. |
| `PivotMultiMetric` | PRE_SCHEMA, bloqueante: pivote + varias métricas (salvo `multi_with_pivot`). |
| `MetricsValid` | Delega en `metrics_errors`. |
| `AliasNotColumn` | Un alias no puede llamarse como una columna. |
| `ShowAsAllowed` | Solo si `show_as=False` (la dona). |
| `DEFAULT_TYPES = {'agg', 'calc'}`, `PIVOT_MULTIMETRIC_MSG` | |

**`parts/filtering.py`**

| Artefacto | Descripción |
|---|---|
| `Filters` (`filters`, `order=10`) | WHERE. `prepare` aplica `apply_filters`. Regla `ConditionsValid`. Panel: `condition-list`. |
| `Having` (`having`, `order=20`) | Grupos de la primera dimensión que cumplen. `prepare` recorta las filas a esos grupos. Regla `HavingRefsWidgetMetrics`. Panel: `condition-list-metric`. |
| `@dataclass(frozen) Sort` | `by`, `dir`; `descending` *(prop)*, `to_dict()`. |
| `OrderBy` (`sort`) | `Sort` o `null`. Regla `SortTargetExists` (lo que dicen los `sort_targets` de las piezas). Panel: `field-group` con `by` (`options_from: sort_targets`) y `dir`. |
| `SORT_DIRECTIONS`, `SORT_DIRECTION_LABELS` | `desc`/`asc` y sus textos. |
| `@dataclass(frozen) Limit` | `n`, `others`. |
| `TopN` (`limit`, `order=30`) | Los N primeros grupos según `sort`; con `others`, el resto como «Otros». Regla `LimitNeedsMetricSort`. Panel: `field-group` con `n` y `others`, y el aviso `limit_without_metric_sort`. |
| `grouped_hint(widget, result)`, `_grouped_rows(...)` | Mensaje para el KPI y «¿el plan agrupa?». |

**`parts/trend.py`**

| Artefacto | Descripción |
|---|---|
| `TrendBy` (`trend_by`) | Columna de la mini tendencia del KPI. Panel: `column-picker` con opciones `dimension`. |

### 3.14 `dsl/spec.py` — `DataSpec`

| Artefacto | Descripción |
|---|---|
| `class DataSpec` | Valor inmutable compuesto por piezas (`parts`). Los valores se leen como atributos (`spec.dimensions`). |
| `__init__(source="", **values)` | Completa con `default()` las piezas que faltan. |
| `get(key, default)`, `has(key)` | Acceso seguro. |
| `keys()` *(cls)*, `part(key)` *(cls)* | Claves y pieza por clave. |
| `names()`, `sort_targets()` | Unión de las de sus piezas. |
| `metrics` *(prop)*, `aliases` *(prop)*, `metric(alias)` | Atajos de métricas. |
| `schema(ctx, *, for_ai)` *(cls)* | Schema cerrado (`additionalProperties: false`) con `source` como `const`. |
| `normalize(raw)` *(cls)* | Quita las claves ajenas vacías (el builder manda todas). |
| `with_defaults(raw)` *(cls)* | Completa las claves propias ausentes o `null`. |
| `own(raw)` *(cls)* | Sin las claves ajenas (ya reportadas). |
| `from_dict(raw)` *(cls)* / `to_dict()` | JSON ↔ objeto. |
| `resolved(df)` | Copia con todos los relativos resueltos una vez. |
| `union_schema(spec_classes, ctx, *, for_ai)` | Schema que acepta cualquier tipo (IA sin tipo fijado). |
| `with_parts(base, *parts, without=())` | Piezas de `base` reemplazando las de igual clave y quitando `without`. |
| `GroupedSpec` | `dimensions(1,1)`, `pivots(0,1)`, `filters`, `metrics(1,5)`, `having`, `sort`, `limit`: gráficos y tablas dinámicas. |
| `ScalarSpec` | `filters`, `metrics(1,4, agg/calc/grouped)`, `trend_by`: KPI. |
| `RowsSpec` | `columns(1,50)`, `filters`, `sort`: tabla de datos. |

---

## 4. Backend: `engine/` (la ejecución)

### 4.1 `engine/executor.py`

| Función | Descripción |
|---|---|
| `run(spec, df, plan)` | Único punto donde se calcula. 1) `universe = df`; 2) `spec.resolved(universe)`; 3) `prepare` de cada pieza en orden de `order`; 4) `plan.run(spec, PlanInput(...))`. Nunca evalúa texto. |

### 4.2 `engine/plans/base.py`

| Artefacto | Descripción |
|---|---|
| `class ResultTooLargeError(ValueError)` | El cruce excede lo que se puede mostrar; el widget muestra el mensaje. |
| `class PlanResult` | Marcador de los resultados tipados. |
| `@dataclass PlanInput` | `df` (filas ya recortadas), `universe`, `has_others`. |
| `class ResultPlan(Generic[R])` | `key`, `aggregates` (¿trabaja con grupos?), `run(spec, data) -> R`. Registro `PLANS`. |
| `others_last(values, key)` | «Otros» al final. |

### 4.3 Planes

| Plan (`key`) | Resultado | Para |
|---|---|---|
| `FlatPlan` (`flat`) | `FlatResult(dimension, rows, totals)` | Gráficos sin pivote, dona. |
| `PivotChartPlan` (`pivot_chart`) | `PivotChartResult(dimension, pivot, metric, dimension_values, pivot_values, rows, row_totals, column_totals, grand_totals)` | Gráficos con pivote (una métrica, una serie por valor). |
| `PivotTablePlan` (`pivot_table`) | `PivotTableResult(dimensions, pivots, metrics, column_keys, rows: [PivotRow], grand: PivotBlock)` | Tabla dinámica con niveles anidados y subtotales. `MAX_TABLE_CELLS=20000`. |
| `ScalarPlan` (`scalar`) | `ScalarResult(values, trend: Trend | None)` | KPI. `trend(spec, data)` calcula el KPI por cada valor de `trend_by` (hasta `MAX_TREND_POINTS=60`). |
| `RowsPlan` (`rows`, sin agregar) | `RowsResult(columns, rows, total_rows)`; `truncated` *(prop)* | Tabla de datos (hasta `MAX_ROWS=5000`). |
| `ColumnValuesPlan` (`column_values`, sin agregar) | `ColumnValuesResult(values, truncated)` | Opciones de la caja de filtros (hasta `MAX_BOARD_IN_VALUES`). |

Auxiliares de `pivot_table.py`: `PivotBlock(cells, totals)`, `PivotRow(key, subtotal)`, `_ordered_keys(df, columns)` (combinaciones en orden de la hoja) y `_hierarchy(leaf_keys, sort_level)` (claves en orden de despliegue, cada grupo seguido de su subtotal). En `scalar.py`, `chronological_keys(series)` da el eje de la tendencia.

---

## 5. Backend: `widgets/` (los tipos de widget)

### 5.1 `widgets/base.py`

**`ViewOptions`** (`@dataclass(frozen)`): opciones de presentación; nunca cambian el cálculo.

| Miembro | Descripción |
|---|---|
| `title`, `labels`, `display` | Comunes: título, nombres legibles `{as|columna: texto}`, preferencias de UI del frontend. |
| `from_request(data, previous=None)` *(cls)* | Desde el body del builder (o la IA normalizada). Lo que no viene se toma de `previous`; `display` se conserva al editar. |
| `_request_fields(data, previous)` *(cls)* | Hook de las subclases para sus campos. |
| `from_view(view)` *(cls)* / `_view_fields(view)` *(cls)* | Desde un `view_spec` guardado. |
| `ai_doc` | Línea para el prompt de la IA. |
| `ai_properties()` *(cls)*, `ai_required()` *(cls)* | Lo que agrega a `view_options` de la tool. |
| `from_ai(view_options)` *(cls)* | Respuesta de la IA → body del builder. |
| `reconcile(spec)` | Copia sin referencias a lo que el spec ya no tiene. |
| `view_fields()` | Sus campos en el `view_spec`. |
| `label(name)` | Nombre legible (o `humanize`). |

**`WidgetType`**: un tipo de widget.

| Miembro | Descripción |
|---|---|
| `key`, `label` | Clave (≤ 20 caracteres) y nombre. |
| `spec_cls`, `options_cls` | Su `DataSpec` y sus `ViewOptions`. |
| `plan_key` | Plan en `PLANS` (o se sobrescribe `plan()`). |
| `board_filtered` | ¿Se calcula con los filtros del tablero? |
| `panel_hidden` | Piezas que el panel no muestra. |
| `max_per_dashboard` | Límite por tablero (`None` = sin límite). |
| `ai_enabled`, `ai_doc`, `ai_examples` | Lo que sabe la IA. |
| `manifest(columns=None)` | `{data, parts, view, max_per_dashboard}` para el editor (ver §4.3 de `arquitectura_widgets.md`). |
| `data_schema(ctx, *, for_ai)` | Schema de su spec. |
| `rules()` | `UnsupportedPart` + reglas de sus piezas (+ las propias en subclases). |
| `errors(raw, ctx)` | *Template method*: normalize → PRE_SCHEMA → schema → SEMANTIC. Nunca lanza. |
| `validate(raw, ctx)` | `DataSpec` o `SpecValidationError`. |
| `options(data, previous_view)` | `ViewOptions` desde un request sobre el `view_spec` actual. |
| `build_view(spec, options)` | `view_spec`: `widget`, `data_view`, campos de opciones reconciliadas, `percent`, `title`, `labels`, `display`. |
| `data_view(spec)` | Lo derivado del spec (ej. eje X). |
| `default_title(spec, options)` | «Métrica por dimensión». |
| `plan(spec)`, `execute(spec, df)` | Plan y ejecución. |
| `compile(result, options, spec)` *(abstracto)* | Resultado → formato exacto del frontend. |
| `render(data_spec, view_spec, df)` | Calcula y compila un widget guardado. |
| `WIDGETS` | Registro de tipos. |
| `percent_metrics(spec)` | Aliases que son porcentaje. |

### 5.2 `widgets/chart.py` — base de barras y líneas

| Artefacto | Descripción |
|---|---|
| `REFERENCE_KINDS`, `MAX_REFERENCE_LINES=5`, `REFERENCE_COLOR` | Líneas de referencia. |
| `clean_reference_line(raw)`, `clean_reference_lines(raw)` | Normalizan (o descartan) líneas: `kind`, `value`, `series`, `label`, `color`. |
| `ChartOptions(ViewOptions)` | Agrega `reference_lines`; `reconcile` descarta líneas sobre métricas que ya no existen (sin pivote). |
| `ChartWidget(WidgetType)` | `spec_cls=GroupedSpec`; `plan()` elige `flat` o `pivot_chart` según haya pivote; `compile` → `{series, categories, percent?, referenceLines?}`; `_references(...)` *(static)* resuelve la serie de cada línea a su nombre dibujado. |

### 5.3 Los widgets

| Archivo | Clase(s) | Qué hace / particularidades |
|---|---|---|
| `bar.py` | `BarOptions(ChartOptions)` (+`stacked`), `BarWidget` (`bar`) | Barras. `stacked` solo con pivote (se reconcilia). |
| `line.py` | `LineWidget` (`line`), `_ascending(values)` | Líneas. Sin orden elegido, el eje X va en orden cronológico (`_chronological`). |
| `donut.py` | `DonutSpec` (una métrica, sin pivote, sin `show_as`), `DonutWidget` (`donut`) | `compile` → `{series, labels}`; el % lo calcula ApexCharts. |
| `dynamic_table.py` | `DynamicTableSpec` (hasta 3 filas y 2 columnas, varias métricas con pivote), `DynamicTableWidget` (`dynamic_table`), `_pivot_columns(result, options)` | Columnas anidadas como Sheets, filas de subtotal (`__subtotal`), fila de totales aparte. |
| `table.py` | `TableWidget` (`table`) | Filas tal cual: `{columns: [{header, field, numeric}], rows, total_rows, truncated?}`. |
| `kpi.py` | `KpiOptions` (`primary`, `compare`, `compare_mode`, `target`, `higher_is_better`, `status`), `TrendNeedsTrendableMetric`, `_status(...)`, `KpiWidget` (`kpi`) | Tarjeta con comparación, meta, semáforo y tendencia. `from_ai` traduce `target_metric`/`target_value`. |
| `filter.py` | `FilterControl` (registro `FILTER_CONTROLS`), `MultiSelectControl`, `FilterOptions` (+`controls`), `FilterSpec`, `FilterWidget` (`filter`) | Caja de filtros: `board_filtered=False`, `max_per_dashboard=1`, `ai_enabled=False`, `panel_hidden=("filters",)`. `compile` → `{filters: [{field, label, type, options, truncated?}]}`. |

### 5.4 `widgets/presentation.py` — helpers de compilación

| Artefacto | Descripción |
|---|---|
| `humanize(name)` | `total_ventas` → «Total ventas». |
| `pivot_field(pivot_value, metric)`, `cell_field(col_key, metric)`, `total_field(metric)` | Claves planas de celdas para Tabulator (`PIVOT_FIELD_PREFIX`, `TOTAL_FIELD_PREFIX`, `KEY_SEPARATOR`). |
| `TOTAL_LABEL = "Total general"` | |
| `number(value)` | Número JSON-serializable. |
| `column_values(df, column)` | Valores de una columna como lista nativa. |

### 5.5 `widgets/ext/` — extensiones

| Artefacto | Descripción |
|---|---|
| `load(package="sheets_reports.widgets.ext")` | Importa cada módulo del paquete (registra sus widgets). Lo llama `SheetsReportsConfig.ready`. |

Una extensión importa solo de `sheets_reports/sdk.py`, que reexporta la API estable (ver su `__all__`).

---

## 6. Backend: servicios, vistas, modelo y configuración

### 6.1 `services/sheets.py` — lectura de Google Sheets

| Artefacto | Descripción |
|---|---|
| `class SheetError(Exception)` | Error legible (las vistas responden 502). |
| `fetch_sheet_dataframe(sheet_id, gid)` | Lee la pestaña vía `gviz/tq` (CSV), sin caché. |
| `get_sheet_dataframe(sheet_id, gid, ttl=SHEET_CACHE_TTL)` | La misma lectura, cacheada por `(sheet_id, gid)`. **Usar esta.** |
| `invalidate_sheet_cache(sheet_id, gid)` | Fuerza la próxima lectura (`/schema/?refresh=1`). |
| `get_sheet_schema(df)` | `{all_fields, numeric_fields}`. |
| `get_dimension_fields(df, max_numeric_unique=31)` | Columnas agrupables: texto/fecha y numéricas enteras con pocos valores (año, mes). |
| `get_field_samples(df, max_unique=15)` | Valores distintos de columnas de texto con pocos valores (para la IA y el autocompletar). |
| `_access_token()`, `_coerce_numeric_columns(df)` | Token de la service account; convierte a número columnas de texto numéricas. |

### 6.2 `services/widget_service.py` — casos de uso

| Artefacto | Descripción |
|---|---|
| `clean_position(value, fallback=None)` | `{x, y, w, h}` acotado (w 1-12, h 100-3000). |
| `WidgetService(dashboard, df)` | No hace I/O: recibe la hoja ya cargada. |
| `._raw_data_spec(definition, payload)` | Del body del builder toma las claves de `SPEC_PARTS` y completa con defaults. |
| `.create(widget_type, payload)` | Valida, crea `Widget` con `data_spec` y `view_spec`. Respeta `max_per_dashboard`. |
| `.update_spec(widget, payload)` | Valida, reconstruye `view_spec` conservando lo que no viene, guarda. |
| `.parse_board_filters(raw)` | `?filters=` → `(condiciones válidas, mensajes de las ignoradas)`. `ValueError` si no es una lista JSON. |
| `.render(widget, board_filters=None)` | `{"data": ...}` o `{"error": ...}`; nunca tumba el tablero. |

### 6.3 `services/ai_spec.py` — IA (Gemini)

| Artefacto | Descripción |
|---|---|
| `class SpecGenerationError(Exception)` | La IA no produjo un spec válido (422). |
| `CORE_PROMPT`, `PIVOT_PROMPT`, `VIEW_PROMPT` | Partes fijas del prompt. |
| `capabilities_text(widget)` | «Admite: …; sin …» desde las piezas. |
| `build_system_prompt(widgets=None)` | Prompt completo: core + cada widget (`ai_doc`, capacidades, opciones, ejemplos). |
| `build_tool_parameters(ctx, widget_type)` | Schema de la tool `create_widget` desde los registros y la hoja. |
| `generate_widget_spec(prompt, widget_type, ctx)` | `{widget_type, data_spec, view_spec}`; reintenta una vez con los errores. |
| `gemini_client(api_key)`, `_call_model`, `_tools`, `_to_gemini_schema`, `_scalar_or_list_schema`, `_columns_context`, `_user_message`, `_normalize`, `_audit`, `_view_docs` | Internos: cliente, adaptación del schema al subconjunto de Gemini, mensajes, normalización de la respuesta y auditoría (`AI_AUDIT_LOG_FILE`). |

### 6.4 `models.py`

| Artefacto | Descripción |
|---|---|
| `Dashboard` | `nombre`, `owner`, `sheet_url`, `sheet_gid`, `created_at`; `sheet_id` *(prop)* extraído de la URL. |
| `Widget` | `dashboard`, `type` (choices de `WIDGETS`), `position`, `data_spec`, `view_spec`, `source_prompt`, timestamps; `definition` *(prop)* = su `WidgetType`. |
| `default_position()` | `{"x": 0, "y": 0, "w": 6, "h": 300}`. |
| `widget_type_choices()` | `[(key, label)]` de `WIDGETS`. |

### 6.5 `views.py`

| Función | Descripción |
|---|---|
| `home`, `board_editor`, `board_view` | Páginas. `board_editor` pasa `widget_manifest` (sin columnas) y `refresh_minutes`. |
| `_get_user(request)` | Usuario de la sesión o, sin login, el primer superusuario. |
| `_json_body(request)` | Body JSON como dict (`ValueError` si no). |
| `_error(message, status=400, **extra)` | `JsonResponse({"error": ...})`. |
| `_owned_dashboard`, `_owned_widget` | Recursos del usuario (o `None`). |
| `_gid_from_url(url)` | gid del `#gid=` de la URL. |
| `_load_sheet(dashboard)` | La hoja desde la caché. |
| `_serialize_dashboard`, `_serialize_widget` | JSON de respuesta. |
| `_validation_error(e)` | 422 con `{error, errors}`. |
| Endpoints | Ver §7. |

### 6.6 Otros

| Archivo | Contenido |
|---|---|
| `apps.py` | `SheetsReportsConfig.ready()`: carga las extensiones y rechaza claves de widget de más de 20 caracteres. |
| `admin.py` | `DashboardAdmin` (con `WidgetInline`) y `WidgetAdmin` (specs como JSON legible, `_pretty_json`). |
| `config/urls.py` | Rutas (§7). |
| `config/settings.py` | Configuración (§1). |

---

## 7. API HTTP

Todas responden JSON; los errores son `{"error": "...", ...}`.

| Método y ruta | Vista | Body / query | Respuesta |
|---|---|---|---|
| `GET /` | `home` | — | Página de tableros. |
| `GET /tableros/<id>/edit/` | `board_editor` | — | Editor. |
| `GET /tableros/<id>/shared/` | `board_view` | — | Vista de solo lectura. |
| `GET /api/dashboards/` | `dashboard_list` | — | `[dashboard]`. |
| `POST /api/dashboards/` | `dashboard_list` | `{nombre, sheet_url, sheet_gid?}` | `dashboard` (201). |
| `PUT /api/dashboards/<id>/` | `dashboard_detail` | `{nombre?, sheet_url?, sheet_gid?}` | `dashboard`. |
| `DELETE /api/dashboards/<id>/` | `dashboard_detail` | — | `{deleted: true}`. |
| `POST /api/dashboards/<id>/duplicate/` | `dashboard_duplicate` | — | Copia con sus widgets (201). |
| `GET /api/dashboard/<id>/schema/` | `dashboard_schema` | `?refresh=1` invalida la caché | `{all_fields, numeric_fields, dimension_fields, sample_values, widget_manifest}`. |
| `GET /api/dashboard/<id>/render/` | `dashboard_render` | `?filters=[{field, op, value}]` | `{dashboard, widgets: [widget + data|error], filter_errors}`. Nunca llama a la IA. |
| `POST /api/dashboard/<id>/widgets/` | `create_widget` | `{type, <claves del spec>, <opciones de vista>, title?, labels?, position?, display?}` | `widget + data` (201) o 422. |
| `PUT /api/widget/<id>/spec/` | `update_widget_spec` | `{<claves del spec>, <opciones de vista>, title?, labels?}` | `widget + data` o 422. |
| `PUT /api/widget/<id>/` | `widget_detail` | `{position?, title?, display?}` (solo presentación) | `widget`. |
| `DELETE /api/widget/<id>/` | `widget_detail` | — | `{deleted: true}`. |
| `POST /api/dashboard/<id>/table-assistant/` | `table_assistant` | `{prompt}` | `{data_spec, view_spec}` sugeridos (no crea nada). |

---

## 8. Frontend: plantillas

Stack: Django templates + Tailwind (CDN) + Alpine.js 3 (estado reactivo) + Sortable (arrastre) + ApexCharts (gráficos) + Tabulator (tablas) + Virtual Select (selector múltiple).

| Plantilla | Qué contiene |
|---|---|
| `base.html` | Layout, Tailwind, Alpine. Define `window.SCRIPT_NAME` y `window.apiUrl(path)` (usar siempre para `fetch`). |
| `home.html` | Lista de tableros: componente Alpine `homeApp()` (`init`, `boardUrl`, `sheetLabel`, `openMenu`, `deleteDashboard`, `duplicateDashboard`, `resetNewDashboardForm`, modales de alta y edición). |
| `board_editor.html` | Editor: barra de módulos, lienzo, panel lateral («Configurar» / «Personalizar»). Publica `window.WIDGET_MANIFEST`, `DASHBOARD_ID`, `SHARE_PATH`, `REFRESH_MINUTES` y carga los scripts en orden: `base-widget` → `widget-registry` → `widgets/*` → `panel` → `dashboard-store` → `filters` → `board-editor-init`. |
| `board_view.html` | Vista compartida: mismos widgets montados en solo lectura (`board-view-init.js`). |

**Panel de datos** (`partials/panel/`), incluido desde el campo `builder` del panel:

| Partial | Qué dibuja | Variables Alpine que usa |
|---|---|---|
| `panel.html` | Recorre `$store.dashboard.panelSections`: título de la sección, sus piezas (en columnas si es un grupo) y los extras de la sección. | `section`, `s`, `part` |
| `part.html` | Despacho por `part.ui` al partial que corresponde. | `part` |
| `column_list.html` | ui `column-list`: filas con select, ×, arrastre, «Agregar», «Usar todas». | `part`, `v`, `i` |
| `column_picker.html` | ui `column-picker`: un select opcional. | `part` |
| `field_group.html` | ui `field-group`: campos `select`/`number`/`text`/`checkbox` con `show_if`/`enable_if` y avisos (`notes`). | `part`, `field`, `note` |
| `metric_list.html` | ui `metric-list`: métricas (agg/calc/grouped), su detalle desplegable, filtros por métrica, arrastre. | `m`, `i` |
| `column_row_inline.html` | Opción de vista en la línea de cada columna: tipo de control (caja de filtros). | `part`, `i` |
| `column_row_below.html` | Opciones de vista bajo cada columna: totales por nivel, repetir etiquetas, nombre del filtro. | `part`, `i` |
| `section_addons.html` | Opciones de vista junto a una sección: «Apiladas» (grupo), tarjeta del KPI y líneas de referencia (métricas). | `section` |

**Partials de condiciones** (reutilizados por el panel y por las métricas):

| Partial | Parámetros de `{% include ... with %}` |
|---|---|
| `partials/builder_conditions.html` | `list`: expresión Alpine de la lista de condiciones `{field, op, value, mode, value2}`. |
| `partials/builder_group_conditions.html` | `list`: lista de `{left, op, rightKind, right}`; `options`: métricas elegibles `[{value, label}]`. |

---

## 9. Frontend: JavaScript

Todo vive en `static/sheets_reports/js/board_editor/`. Los archivos son scripts clásicos (sin bundler): las funciones de nivel superior de `panel.js` y `dashboard-store.js` son globales; las clases se publican en `window` (`window.BaseWidget`, `window.WidgetRegistry`, …).

### 9.1 `base-widget.js` — `BaseWidget`

Clase base de todos los widgets del frontend: capacidades (del manifiesto), campos del panel de presentación, montaje en el DOM y ciclo de dibujo.

**Identidad y capacidades (estáticos)**

| Miembro | Descripción |
|---|---|
| `type` | Igual al `key` del backend. |
| `palette` | `{icon, category, label, description}` en la barra de módulos. |
| `defaults` | `{title, width, height}` de un widget nuevo. `minHeight`. |
| `CHART_COLORS` | Paleta de series. |
| `DEFAULT_MANIFEST` | Manifiesto usado sin el del backend (vista compartida). |
| `manifest` *(get)* | `window.WIDGET_MANIFEST[type]` o el por defecto. |
| `supportsView(key)` | ¿Su `options_cls` tiene ese campo? |
| `maxDimensions`, `maxPivots`, `maxMetrics`, `maxColumns` | Cotas de `manifest.data`. |
| `supportsDimension`, `supportsPivot`, `supportsMetrics`, `usesColumns`, `supportsShowAs`, `supportsSort`, `metricTypes` | Derivados de `manifest.data`. |
| `supportsStacked`, `supportsReferenceLines`, `singleton` | Derivados de `view` y `max_per_dashboard`. |
| `defaultColumns`, `defaultColumnsFrom` | Con qué columnas arranca un widget nuevo con columnas sueltas. |
| `columnControls`, `supportsColumnLabels` | Opciones de vista por columna (caja de filtros). |
| `placement` | `'canvas'` (grid) o `'header'` (franja fija). |
| `supportsLabels`, `supportsTotals` | Nombre a mostrar de métricas; totales por nivel. |

**Campos del panel de presentación (estáticos)**

| Miembro | Descripción |
|---|---|
| `FIELD_TITLE`, `FIELD_BUILDER`, `FIELD_WIDTH`, `FIELD_HEIGHT` *(get)*, `FIELD_START_COL` | Campos `{key, label, type, tab, options...}`. `type`: `text`, `select`, `number`, `range`, `checkbox`, `builder`, `assistant`. |
| `drawerFields` *(get)* | Lista que dibuja el panel; las subclases agregan las suyas. |
| `GRID_COLUMNS = 12`, `_startColNumber`, `_startColValue`, `_maxStartCol`, `startColOptionsForWidth(width)`, `fitStartCol(startCol, width)` | Columna de inicio válida para un ancho. |
| `_parseSpan(widthClass)`, `getGhostSpan()`, `_ghostSpanFromWidth()` | Ancho en columnas para el fantasma del arrastre. |

**Helpers estáticos**

| Miembro | Descripción |
|---|---|
| `escapeHTML(str)` | Escapa texto para `innerHTML`. |
| `allSeriesPercent(payload, series)` | ¿Todas las series son %? |
| `REFERENCE_KINDS`, `REFERENCE_DEFAULT_LABELS`, `REFERENCE_COLOR` | Líneas de referencia. |
| `referenceValue(line, series)`, `referenceFormat(percentAxis)` | Valor y formato de una línea. |
| `DOWNLOAD_ICON_SVG`, `dragHandleHTML()`, `actionButtonsHTML()` | Fragmentos HTML comunes. |
| `fromServer(w)` | Widget serializado → instancia de la clase registrada (mapea `position` a clases de grid y `view_spec.display` a propiedades). |

**Instancia**

| Miembro | Descripción |
|---|---|
| `constructor(raw)` | `id`, `title`, `chart_type`, `data_spec`, `view_spec`, `source_prompt`, `width`, `height`, `startCol`, `order`, `_dirty`. |
| `applyServerState(w)` | Copia specs y título que devolvió el backend. |
| `hasSpec` *(get)* | ¿Ya tiene `data_spec`? |
| `buildElement()` *(abstracto)*, `buildStandardCardElement()`, `buildReadOnlyElement()` | DOM de la tarjeta. |
| `mount()`, `mountReadOnly()` | Monta en el editor o en la vista compartida (`_readOnly`). |
| `getContentContainer()` | `#chart-<id>`. |
| `setLoading(bool)`, `loaderOverlayHTML()` | Indicador de carga. |
| `updateChrome()` | Aplica título, ancho, inicio y alto al DOM. |
| `getProperties()` | Preferencias de UI que van a `view_spec.display` (las subclases agregan). |
| `getPosition()` | `{x, y, w, h}` para el backend. |
| `toPayload()` | `{title, position, display}` (guardado de presentación). |
| `renderContent(container, data)` *(abstracto)* | Dibuja `data` compilado por el backend. |
| `applyRender(entry)` | `{data}` → `renderContent`; `{error}` → `renderError`. Guarda `_lastEntry`. |
| `renderApexChart(container, options)` | Monta un ApexChart en un div interno. |
| `renderError(message, {retryable})`, `renderPlaceholder()` | Estados vacíos. |
| `downloadButtonHTML`, `_filenameSlug`, `chartExportToolbar()`, `downloadRowsAsCSV(headers, rows, filename)` | Exportación. |
| `_attachCommonEvents()`, `_onResizeStart(e)`, `destroy()` | Editar/borrar, redimensionado, limpieza. |

### 9.2 `widget-registry.js` — `WidgetRegistry`

| Método estático | Descripción |
|---|---|
| `register(WidgetClass)` | Registra por `type`. |
| `has(type)`, `get(type)` | Consulta (`get` lanza si no existe). |
| `getAll()`, `getTypes()` | Clases y tipos registrados. |
| `getPaletteEntries()` | Entradas de la barra de módulos. |
| `create(type, raw)` | Nueva instancia. |

### 9.3 `widgets/*.js` — clases de cada widget

Cada una define `type`, `palette`, `defaults`, sus `drawerFields` de presentación, `getProperties()` (lo que guarda en `display`), `mockData()` (vista previa sin datos) y `renderContent(container, data)`.

| Archivo | Clase | Particularidades |
|---|---|---|
| `bar-widget.js` | `BarWidget` | Campos: horizontal, ancho del eje Y, ancho de barra, formato de etiquetas, ancho forzado, cuadrícula. `_colorsFor(series)` (color estable por nombre), `_wireLegendDrag` / `_onLegendReorder` / `_destroyLegendSortable` (reordenar series arrastrando la leyenda). `applySeriesOrder(series, order)` (función local). |
| `line-widget.js` | `LineWidget` | ApexCharts de líneas con líneas de referencia. |
| `donut-widget.js` | `DonutWidget` | `FIELD_LABEL_MODE`: valor o % en las porciones. |
| `kpi-widget.js` | `KpiWidget` | Campos: decimales, abreviar, prefijo, sufijo. `format(value, {percent, signed})`, `_compareHTML`, `_targetHTML`, `_renderTrend` (sparkline). `STATUS_CLASS`, `STATUS_BAR`, `SPARK_COLOR`. |
| `dynamic-table-widget.js` | `DynamicTableWidget` | Tabulator. `formats` (formatos de columna: text, number, currency, percent, progress), `applyFormatter(field, tipo)`, `calcFormatter(config)` *(static)*, `_formatRow`, `_wireTableEvents` (orden de columnas y CSV), `_orderedColumns`, `totalsOn(levels, level)` *(static)*, `_displayRows` (subtotales apagados y etiquetas repetidas). Campos: asistente IA, filas por página, paginación, resaltar última fila. |
| `table-widget.js` | `TableWidget extends DynamicTableWidget` | Filas tal cual; sin totales ni nombres de métrica. |
| `filter-widget.js` | `FilterWidget` | `placement='header'`, `columnControls`, `supportsColumnLabels`. `_buildBar(editable)`, `_initMultiSelect(ele, filter)` (Virtual Select, textos en `MULTI_SELECT_TEXTS`), `_destroySelects()`. Al cambiar la selección llama a `store.setBoardFilter`. |

### 9.4 `panel.js` — panel de datos genérico

| Artefacto | Descripción |
|---|---|
| `CUSTOM_STATE_PARTS` | Piezas con adaptador propio en `dashboard-store.js` (`dimensions`, `pivots`, `columns`, `filters`, `metrics`, `having`, `sort`). |
| `PANEL_SOURCES` | Fuentes de opciones que dependen del panel (`options_from`): `sort_targets → store.sortOptions`. |
| `PANEL_CHECKS` | Avisos (`notes[].when`): `limit_without_metric_sort → store.limitNeedsMetricSort`. |
| `panelParts(widgetClass)`, `panelPart(widgetClass, key)` | `manifest.parts` y una pieza por clave. |
| `panelSections(parts)` | Agrupa piezas seguidas con el mismo `group`: `[{key, label, hint, grouped, parts}]`. |
| `fieldIsEmpty(field, value)` | Vacío (o número inválido o bajo `min`). |
| `fieldGroupFromSpec(part, value)` / `fieldGroupToPayload(part, state)` | Estado de un `field-group` (números como texto) ↔ valor del spec (`null` si falta `required`). |
| `partStateFromSpec(part, value)` / `partStateToPayload(part, state, b)` | Estado genérico por ui (`column-list` → array, `column-picker` → string, `field-group` → objeto, otra → tal cual). |
| `builderList(b, key)` | Columnas elegidas de una lista sin vacíos, repetidos ni las tomadas por una lista anterior de sus `excludes`. |

### 9.5 `dashboard-store.js` — estado del editor

**Constantes**

| Constante | Descripción |
|---|---|
| `AI_FETCH_TIMEOUT_MS`, `RENDER_FETCH_TIMEOUT_MS`, `LAYOUT_SAVE_DELAY_MS` | Tiempos. |
| `AGG_OPTIONS`, `SHOW_AS_LABELS`, `METRIC_TYPE_OPTIONS`, `CALC_OP_OPTIONS`, `GROUP_RESULT_OPTIONS`, `COUNT_RESULTS`, `RANKING_RESULTS` | Catálogos de métricas (espejo de los registros del backend; `word` arma los alias). |
| `FILTER_OP_OPTIONS`, `LIST_OPS`, `EMPTY_OPS`, `RELATIVE_OPS`, `VALUE_MODE_OPTIONS`, `COMPARE_OP_OPTIONS`, `OP_SHORT` | Catálogos de condiciones. |
| `LIST_TOTALS` | `{dimensions: 'rowTotals', pivots: 'columnTotals'}`. |

**Funciones del builder** (el builder es el estado editable del panel: `drawerDraft.builder`)

| Función | Descripción |
|---|---|
| `fetchJsonSafe(url, options, timeoutMs)` | `fetch` con timeout que nunca truena por JSON inválido: `{r, data}`. |
| `totalsLevels(list, n)` | Totales por nivel ajustados a `n`. |
| `chosen(list)` | Sin vacíos ni repetidos. |
| `builderClass(b)` | Clase del widget del builder (`b.widget`). |
| `builderDims(b)`, `builderPivots(b)`, `builderColumns(b)` | Listas elegidas (vía `builderList`). |
| `isCountAgg(agg)`, `newId()`, `slug(text)`, `asAlias(text)`, `metricAlias(agg, field, showAs, suffix)`, `defaultColumnName(name)` | Utilidades de alias y nombres. |
| `newCondition`, `conditionFromSpec`, `typedValue`, `conditionToSpec`, `conditionsToSpec`, `describeCondition` | Condiciones del builder ↔ spec. |
| `newGroupCondition`, `groupConditionFromSpec(h, idOf)`, `groupConditionToSpec(h, aliasOf)` | Condiciones sobre grupos (con ids locales ↔ alias). |
| `newMetric(type, numericFields)`, `newInnerMetric` | Métrica nueva con los campos de todos los tipos. |
| `metricSignature(m, b)` | Firma del contenido: si no cambió, la métrica conserva su alias. |
| `metricFromSpec(raw, b, idOf)`, `metricToSpec(m, alias, aliasOf, numericFields)` | Métrica ↔ spec. |
| `showAsOptions(b)`, `effectiveShowAs(b, showAs)` | «Mostrar como» válido para la forma actual. |
| `activeMetrics(b)`, `isRankingMetric(m)` | Métricas que se envían; ¿es top/bottom? |
| `metricAliases(b)` | Alias final de cada métrica y mapa `id → alias`, resolviendo dependencias. |
| `kpiFromView(view, idOf)`, `kpiToPayload(k, aliasOf)` | Roles del KPI. |
| `referenceLinesFromView`, `referenceLinesToPayload` | Líneas de referencia. |
| `builderFromSpec(spec, view, widget)` | `data_spec` + `view_spec` → builder. Cada pieza en `b[clave]`; piezas sin control en `b.kept`. |
| `builderToPayload(b, numericFields)` | Builder → body de POST/PUT (claves del spec + opciones de vista). |
| `describeMetric(m, b)` | Texto de una métrica (pasos de la IA). |
| `withCurrent(options, current)` | Incluye el valor actual aunque ya no esté en las opciones. |

**`Alpine.store('dashboard')`** (registrado en `alpine:init`)

*Estado*: `widgets`, `editingId`, `editingType`, `dashboardId`, `drawerDraft` (copia editable de lo que muestra el panel), `drawerTab`, `drawerSpecError`, `drawerSaveError`, `drawerSaving`, `drawerApplying`, `drawerAsk*` y `drawerAdvice` (asistente IA), `drawerSpecs` (visor JSON), `schema` (columnas de la hoja), `schemaError`, `widgetManifest` (manifiesto reactivo), `metricsSortable`, `listSortables`.

| Grupo | Miembros |
|---|---|
| Carga | `loadSchema()` (columnas y manifiesto completo), `_renderUrl()`, `loadBoard()`, `refreshData()`, `_reportFilterErrors(data)`. |
| Widgets del lienzo | `addWidget(type)`, `removeWidget(id)`, `_saveWidget(w, {keepalive})`, `scheduleLayoutSave()`, `flushLayoutSave()`, `hasPendingLayout`, `reorderWidgets()`, `fluidStartColOnDrop(el)`, `_swapWidgetId(w, newId)`. |
| Panel | `editingWidget`, `drawerWidgetClass`, `drawerFields`, `builder`, `openDrawer(id)`, `closeDrawer()`, `_builderDraft(w)`, `_defaultBuilder()`, `_syncDrawerSpecs(w)`, `fitStartCol()`. |
| Secciones del panel | `panelSections`. |
| `column-list` | `listOptions(part, i)`, `canAddListItem(part)`, `addListItem(part)`, `canRemoveListItem(part)`, `removeListItem(part, i)`, `_listTotals(part)`, `onListChange(part)`, `useAllListItems(part)`, `initListSortable(el, part)`, `destroyListSortables()`. |
| `column-picker` / `field-group` | `pickerOptions(part)`, `fieldOptions(part, field)`, `fieldVisible(part, field)`, `fieldEnabled(part, field)`, `partNotes(part)`. |
| Columnas de la caja de filtros | `columnControl(i)`, `setColumnControl(i, value)`, `columnLabel(i)`, `setColumnLabel(i, value)`. |
| Métricas | `maxMetrics`, `metricTypes`, `drawerSupportsLabels`, `drawerIsKpi`, `metricLabelPlaceholder(i)`, `metricName(id)`, `metricRefOptions(i, {numericOnly})`, `innerRefOptions(m)`, `groupResultNeedsValue(m)`, `addInnerMetric(m)`, `removeInnerMetric(m, i)`, `addBuilderMetric()`, `removeBuilderMetric(i)`, `initMetricsList(el)`, `destroyMetricsList()`, `reorderMetrics()`, `metricTypeLabel(type)`, `onMetricTypeChange(m)`, `fieldOptionsFor(agg, current)`, `showAsOptions`, `showAsValue(m)`. |
| Condiciones | `addCondition(list)`, `removeCondition(list, i)`, `opOptionsFor(field)`, `onConditionFieldChange(c)`, `conditionUsesMode(c)`, `conditionNeedsValue(c)`, `conditionIsList(c)`, `valueModeOptions`, `sampleListId(field)`, `sampleLists`, `addGroupCondition(list, options)`. |
| Orden y Top N | `sortOptions`, `limitNeedsMetricSort`, `showStacked`. |
| Líneas de referencia | `referenceSeriesOptions(current)`, `addReferenceLine()`, `removeReferenceLine(i)`. |
| Guardar | `_numericSet`, `_payload(b)`, `builderDirty`, `applyBuilder()` (POST/PUT del spec), `saveDrawer()` (spec si cambió + presentación). |
| Asistente IA | `askAssistant()`, `drawerSteps`, `applyAdvice()`. |

### 9.6 `filters.js` — selección de la caja de filtros

Agrega al store la selección del tablero: `{columna: [valores]}`, que viaja en `?filters=` y define el universo de todos los widgets.

| Miembro del store | Descripción |
|---|---|
| `boardFilters` | La selección actual. |
| `initBoardFiltersFromURL()` | Lee `?filters=` al cargar (ignora URLs mal formadas). |
| `_boardConditions()` | Selección → `[{field, op: 'in', value}]`. |
| `_applyBoardFilters(filters)` | Actualiza la URL y dispara `dashboard:filters-changed`. |
| `setBoardFilter(field, values)` | Cambia una columna (lista vacía la quita). |
| `clearBoardFilters(fields)` | Quita varias columnas. |
| `getFilterQueryString()` | Query para `/render/` y para «Compartir». |

### 9.7 `board-editor-init.js` — arranque del editor

| Artefacto | Descripción |
|---|---|
| `PALETTE_GROUPS` | Grupos de la barra de módulos. |
| `containerFor(WidgetClass)` | Grid o franja de filtros según `placement`. |
| `RAIL_STORAGE_KEY`, `initRail(sidebarEl)` | Barra lateral contraíble (estado en `localStorage`). |
| `searchText(text)`, `renderPalette(sidebarEl)`, `filterPalette(sidebarEl, query)` | Barra de módulos y su buscador. |
| `showToast(message)` | Aviso flotante (también como `window.showToast`). |
| `copyToClipboard(text)` | Copiar el enlace de «Compartir». |
| `DOMContentLoaded` | Carga columnas y tablero, monta los widgets, configura Sortable (soltar módulos, reordenar el lienzo), escucha `dashboard:filters-changed` y el refresco periódico. |

### 9.8 `board-view-init.js` y utilidades

| Archivo | Contenido |
|---|---|
| `board-view-init.js` | Store mínimo de la vista compartida (`dashboardId`, `widgets`, `_renderUrl()`, `refreshData()`) y montaje en solo lectura. |
| `utils/formatearEtiquetaApex.js` | `formatearEtiquetaApex(texto, maxCaracteresPorLinea=18)`: parte etiquetas largas del eje en líneas. |

### 9.9 Flujo de datos del editor

```
DOMContentLoaded
  ├─ store.loadSchema()  ── GET /schema/ ─► schema + widgetManifest
  └─ store.loadBoard()   ── GET /render/ ─► BaseWidget.fromServer(w) ─► mount() ─► applyRender(entry)

openDrawer(id) ─► drawerDraft = campos de presentación + builder = builderFromSpec(data_spec, view_spec)
  panel.html ─► panelSections ─► part.html ─► componente de cada ui (lee/escribe drawerDraft.builder[part.key])

saveDrawer()
  ├─ builderDirty? ─► applyBuilder() ─► POST /widgets/ o PUT /widget/<id>/spec/ (builderToPayload)
  │                     ─► applyServerState + applyRender
  └─ _saveWidget()  ─► PUT /widget/<id>/ (title, position, display)
```

---

## 10. Pruebas

`python manage.py test sheets_reports`. Organización por capa:

| Archivo | Qué cubre |
|---|---|
| `tests/fixtures.py` | Helpers: `sales_df()` y `sellers_df()` (hojas de prueba), `sales_ctx()`, `spec(**overrides)`, `agg(...)`, `calc(...)`, `errors_for(type, spec)`, `view(type, spec, options)`, `compiled(...)`, `execute(...)`. |
| `tests/dsl/test_validation.py`, `tests/dsl/test_spec.py` | Schemas, reglas, mensajes, composición de `DataSpec`. |
| `tests/engine/test_plans.py` | Cálculos de cada plan. |
| `tests/widgets/test_compile.py`, `test_rule_order.py`, `test_view_options.py`, `test_table.py`, `test_filter.py` | Formato de salida, orden de errores, política vista → datos, tabla y caja de filtros. |
| `tests/widgets/test_panel.py` | `manifest()["parts"]`: orden, `ui`, opciones resueltas, textos propios, `panel_hidden`, y que cada `ui` tenga su rama en `part.html`. |
| `tests/test_architecture.py` | Extensiones de punta a punta (incluido un histograma con su pieza `bins` editable en el panel), reglas de capas e imports. |
| `tests/test_views.py`, `tests/test_ai_spec.py` | Endpoints y generación con IA (Gemini simulado). |

El frontend no tiene suite propia. Para verificar un cambio en la ida y vuelta del builder, se puede cargar `panel.js` y `dashboard-store.js` en Node con `vm` y comparar `builderToPayload(builderFromSpec(spec))` contra el spec guardado.

---

## 11. Glosario

| Término | Significado |
|---|---|
| **data_spec** | JSON de lo que calcula un widget: `{source, dimensions, metrics, filters, ...}`. |
| **view_spec** | JSON de cómo se presenta: título, etiquetas, opciones del tipo, `display`. |
| **pieza (`SpecPart`)** | Una clave del `data_spec` con todo lo suyo. |
| **dimensión / pivote** | Columnas que agrupan filas (eje X) / columnas (series). |
| **métrica** | Lo que se mide: `agg` (resumen), `calc` (cálculo entre métricas), `grouped` (por grupo, solo KPI). |
| **alias (`as`)** | Nombre interno de una métrica; lo referencian cálculos, orden, having y etiquetas. |
| **show_as** | Mostrar como valor o como % de la fila, columna o total. |
| **having** | Condición sobre los grupos ya calculados. |
| **Top N (`limit`)** | Los N primeros grupos según el orden; el resto como «Otros». |
| **universo** | Las filas antes de las condiciones del widget: denominador de los %. |
| **filtros del tablero** | Selección de la caja de filtros; viaja en la URL, no se guarda en ningún widget. |
| **manifiesto** | Lo que el backend publica de cada tipo para el editor (`data`, `parts`, `view`). |
| **builder** | Estado editable del panel de datos en el frontend (`drawerDraft.builder`). |
| **plan** | Forma de resultado (`flat`, `pivot_chart`, `pivot_table`, `scalar`, `rows`, `column_values`). |
| **compile** | Paso del widget que convierte el resultado del plan en el JSON que dibuja el frontend. |
