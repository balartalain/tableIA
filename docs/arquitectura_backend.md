# Arquitectura del backend

> Documento detallado de **cada estructura y concepto** del backend. El frontend está en
> [arquitectura_frontend.md](arquitectura_frontend.md).

---

## 1. Visión general

TableIA es una aplicación Django que construye tableros de reportes sobre hojas de Google
Sheets. El usuario describe lo que quiere en lenguaje natural (o lo configura a mano en un
editor visual), y el backend calcula los números con pandas y devuelve JSON listo para dibujar.

### 1.1 Principios de diseño

1. **Contratos planos, no código.** Cada widget se guarda como dos JSON (`fields` + `style`).
   Ninguno contiene lógica: solo valores validados contra reglas construidas en cada llamada
   desde las columnas reales de la hoja.
2. **Registros sobre condicionales.** Widgets, operadores de filtro y valores relativos son
   clases registradas en `Registry`. El sistema recorre el registro (enums del schema, choices
   del modelo, tool de la IA, manifiesto del editor) en vez de preguntar `if tipo == ...`.
3. **Pasos atómicos.** La consulta es una cadena de 5 funciones puras (`engine/steps/`) que
   reciben el frame y un dict `metadata` compartido. `BaseWidget.process_query` las encadena;
   un widget atípico la sobrescribe y reusa solo los pasos que le sirven.
4. **IA como propuesta, no ejecución.** La IA solo devuelve un `WidgetForm` validado
   (`form_errors`); el motor ejecuta todo. La IA nunca calcula ni ve los datos.
5. **Renderizado defensivo.** Cada widget captura sus excepciones y devuelve `{"error": ...}`;
   un widget roto nunca tumba el tablero.
6. **Contrato de datos en el backend, panel en un partial.** El `style_schema` declara las
   claves de `style` (tipo, opciones, default) y el backend valida contra él. Cómo se
   editan lo decide el partial de Django de cada tipo (`templates/sheets_reports/widgets/
   config/_<tipo>_config.html`), escrito a mano.

### 1.2 Dependencias externas

| Paquete | Uso |
|---|---|
| Django 5.x | Web framework |
| pandas | Transformación de datos |
| requests | Lectura de Google Sheets (gviz/tq) |
| jsonschema (Draft 2020-12) | Validación de condiciones/estilos |
| google-genai | Generación de `WidgetForm` (solo en `services/ai_spec.py`) |
| dj-database-url / django-environ | Configuración desde variables de entorno |

---

## 2. Capas y reglas de dependencias

### 2.1 Mapa de capas

```
                    ┌─────────────────────────────────────────────────────┐
  HTTP (views.py) → │ services/                                           │
                    │   widget_service.py  casos de uso (CRUD + render)  │
                    │   sheets.py          lectura de la hoja + caché     │
                    │   ai_spec.py         propuesta de WidgetForm (IA)   │
                    └───────────────┬─────────────────────────────────────┘
                                    ▼
                    ┌─────────────────────────────────────────────────────┐
                    │ widgets/                                            │
                    │   BaseWidget: capabilities, style_schema,           │
                    │   process_query, compile; registro WIDGETS          │
                    └───────────────┬─────────────────────────────────────┘
                                    ▼
                    ┌──────────────────────────┐   ┌──────────────────────────┐
                    │ engine/                  │──▶│ utils/                   │
                    │ steps/: 5 pasos atómicos │   │ valores, orden, registro,│
                    │ context.py: SheetContext │   │ errores y límites        │
                    └──────────────────────────┘   └──────────────────────────┘
```

### 2.2 Equivalencias con la arquitectura anterior (¿dónde quedó `ViewOptions`?)

La arquitectura DSL compleja (`DataSpec`, `SpecPart`, `ViewOptions`, `ViewSpec`, `WidgetType`,
`METRICS`, `ResultPlan`…) **fue eliminada**. Mapa de sustitución:

| Antes (eliminado) | Ahora (actual) | Dónde |
|---|---|---|
| `DataSpec` (+ `GroupedSpec`, `ScalarSpec`, `RowsSpec`, `with_parts`) | `WidgetFields` | `widgets/schemas.py` |
| **`ViewOptions`** / `ViewSpec.display` | **`WidgetStyle`** (+ `style_schema` declarativo) | `widgets/schemas.py` + cada widget |
| `SpecPart` / `SPEC_PARTS` (piezas del data_spec) | `capabilities` + `style_schema` planos en la clase | `widgets/base.py`, cada widget |
| `WidgetType` (clase base genérica) | `BaseWidget` | `widgets/base.py` |
| `dsl/` (spec, partes, reglas, métricas, condiciones…) | `widgets/schemas.py` + `engine/steps/` + `utils/` | — |
| `engine/executor.py`, `engine/plans/`, `PipelineExecutor`, `PipelineContext` | `engine/steps/` (`run_steps` + 5 funciones) + `WidgetResult` | — |
| `METRICS` registry | dict `AGGREGATIONS` | `engine/steps/aggregation.py` |
| `widgets/chart.py`, `widgets/view.py` | `widgets/presentation.py` | — |
| `WidgetType.options_cls` (`ai_properties`, `reconcile`) | validación por control en `form_errors()` | `services/ai_spec.py` |

El contrato viejo está **prohibido en código**: `test_architecture.py`
(`test_no_quedan_simbolos_del_contrato_viejo`) falla ante cualquier aparición de `data_spec`,
`view_spec`, `SPEC_PARTS`, `SpecPart`, `ResultPlan`, `PlanInput`, `PlanResult`, `ViewOptions`,
`DataSpec`, `WidgetType`, `METRICS`, `PLANS`, `generate_widget_spec`, `widget_type_from_spec`
(en nombres o atributos, fuera de tests y migraciones).

### 2.3 Reglas de capas (verificadas por tests)

| Regla | Test |
|---|---|
| `utils/` no importa `widgets/` ni `services/` | `test_utils_no_importa_widgets_ni_servicios` |
| `utils/` no nombra widgets concretos (ni como constantes de texto) | `test_utils_no_nombra_widgets_concretos` |
| `engine/` no importa `widgets/` ni `services/` | `test_motor_no_importa_widgets_ni_servicios` |
| Nada importa `google.genai` salvo `services/ai_spec.py` | `test_nadie_importa_gemini` |
| No quedan `dsl/`, `engine/pipeline.py` ni otros módulos legacy | `test_no_queda_codigo_legacy` |
| El `style_schema` solo declara datos (`key`, `label`, `type`, `options`, `options_from`, `default`) | `test_style_schema_solo_declara_datos` |
| Cada widget registrado tiene su partial y `board_editor.html` lo incluye | `test_cada_widget_tiene_su_partial` |

---

## 3. Estructura de paquetes

```
config/
  settings.py               # Configuración (DB, caché, Gemini, REPORT_PATH)
  urls.py                   # Todas las rutas de la app

sheets_reports/
  models.py                 # Dashboard, Widget, default_position, widget_type_choices
  views.py                  # Vistas HTTP (adaptadores sobre services/)
  admin.py                  # Django Admin (Dashboard con inline de widgets)
  apps.py                   # ready(): valida la longitud de las claves de widget

  widgets/                  # Tipos de widget (registrados en WIDGETS)
    __init__.py             # exporta WIDGETS/BaseWidget + importa los 7 built-in
    base.py                 # BaseWidget, WidgetResult, registry WIDGETS
    schemas.py              # WidgetFields, WidgetStyle, WidgetForm (contratos planos)
    presentation.py         # Helpers de presentación (labels, series, porcentajes)
    kpi.py                  # KpiWidget          «Tarjeta KPI»
    bar.py                  # BarChartWidget     «Gráfico de Barras»
    line.py                 # LineWidget         «Gráfico de Líneas»
    donut.py                # DonutWidget        «Gráfico de Dona»
    table.py                # TableWidget        «Tabla»
    dynamic_table.py        # DynamicTableWidget «Tabla Dinámica»
    filter.py               # FilterWidget       «Filtros» (singleton de tablero)

  templates/
    board_editor.html       # Editor: lienzo + drawer (pestañas, pie) que incluye el panel del tipo
    sheets_reports/widgets/config/
      _<tipo>_config.html   # Panel de cada tipo: pestañas «Configurar» y «Personalizar»
      blocks/               # Piezas compartidas: _title, _assistant, _columns, _dimensions,
                            # _pivots, _metrics, _trend, _filters, _sort, _limit, _json,
                            # _style_checkbox/_text/_number/_palette

  engine/
    __init__.py             # reexporta run_steps, AGGREGATIONS, ResultTooLargeError
    context.py              # SheetContext
    steps/
      __init__.py           # run_steps (secuencia por defecto) + build_query_result
      filter.py             # Paso 1 filter_rows + FilterOperator, RelativeValue, Condition
      aggregation.py        # Paso 2 apply_aggregation + AGGREGATIONS, MAX_PIVOT_CELLS
      calculated.py         # Paso 3 apply_calculated_metrics
      window.py             # Paso 4 apply_window_functions
      sort.py               # Paso 5 apply_sort_limit

  services/
    __init__.py
    sheets.py               # Lectura de la hoja (gviz/tq) + caché + schema
    widget_service.py       # WidgetService: CRUD, validación, render
    ai_spec.py              # WidgetForm vía Gemini: crea, o ajusta el borrador actual (current) con el historial del chat

  utils/                    # Helpers sin conocer widgets
    registry.py             # Registry genérico + UnknownKeyError
    validation.py           # SpecValidationError, schema_errors, límites y piezas de JSON Schema
    data.py                 # to_python, to_key, sort_key, percent, is_number, chronological…

  tests/                    # Ver §15
```

---

## 4. Modelos de datos

### 4.1 `Dashboard` (`models.py`)

Tablero sobre una pestaña (`gid`) de una hoja de Google Sheets.

| Campo | Tipo | Restricciones / notas |
|---|---|---|
| `id` | BigAutoField | PK |
| `nombre` | `CharField(255)` | Nombre descriptivo; `help_text` |
| `owner` | `FK → settings.AUTH_USER_MODEL` | `on_delete=CASCADE`, `related_name="dashboards"` |
| `sheet_url` | `URLField(500)` | URL completa de la hoja |
| `sheet_gid` | `CharField(50)` | `default="0"`; gid de la pestaña (número tras `#gid=`) |
| `created_at` | `DateTimeField(auto_now_add=True)` | |

- `Meta`: `ordering = ["-created_at"]`, verbose names «Dashboard(s)».
- **`sheet_id`** (property): extrae el ID de la hoja con
  `re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", sheet_url)`; `""` si no hay match.
- `__str__` → `nombre`.

### 4.2 `Widget` (`models.py`)

Un widget del tablero. Guarda el contrato plano, no código.

| Campo | Tipo | Restricciones / notas |
|---|---|---|
| `dashboard` | `FK → Dashboard` | `related_name="widgets"`, CASCADE |
| `type` | `CharField(50)` | `choices=widget_type_choices` (función, no lista) |
| `title` | `CharField(255)` | `default="Nuevo Widget"` |
| `position` | `JSONField` | `default=default_position` → `{"x":0,"y":0,"w":6,"h":300}` |
| `fields` | `JSONField` | `default=dict`; contrato de datos (`WidgetFields.to_dict()`) |
| `style` | `JSONField` | `default=dict`; contrato de estilo (`WidgetStyle.to_dict()`) |
| `source_prompt` | `TextField(null=True, blank=True)` | Prompt original si fue generado por IA |
| `created_at` | `DateTimeField(auto_now_add=True)` | |
| `updated_at` | `DateTimeField(auto_now=True)` | |

Semántica de `position` (documentada en el `help_text`): `x` = columna inicial (0 = fluido),
`y` = orden, `w` = columnas 1–12, `h` = alto en px.

- **`widget_type_choices()`**: importa `WIDGETS` en tiempo de ejecución y devuelve
  `[(key, cls.label), ...]`. Los choices salen del registro, no están hardcodeados.
- **`definition`** (property): la clase registrada del tipo, o `None` si el tipo ya no existe
  (el widget queda en BD pero el render devuelve un error legible).
- `Meta`: `ordering = ["created_at"]`.

### 4.3 Migraciones relevantes

| Migración | Qué hizo |
|---|---|
| `0002_reset_widgets_dsl` | Reset del esquema durante la migración DSL |
| `0004_rename_table_to_dynamic_table` | `table` → `dynamic_table` |
| `0006_data_spec_own_keys` | `data_spec` con claves propias |
| `0007_remove_widget_data_spec_remove_widget_view_spec_and_more` | **Fin del contrato viejo**: borra `data_spec`/`view_spec`, deja `fields` + `style` |

---

## 5. Contratos planos (`widgets/schemas.py`)

Tres dataclasses planas. Reemplazan a `DataSpec` / `ViewOptions` / `ViewSpec` (ver §2.2).

### 5.1 `WidgetFields` — capa de datos

```python
@dataclass
class WidgetFields:
    dimensions: List[str] = []        # columnas de agrupación (eje X / filas)
    metrics:   List[Dict] = []        # métricas en orden de visualización
    filters:   List[Dict] = []        # condiciones sobre filas
    pivots:    List[str] = []         # desagregación (series / columnas cruzadas)
    columns:   List[Dict] = []        # solo tabla: columnas a mostrar tal cual
    trend_by:  Optional[str] = None   # columna de la sparkline del KPI
    sort_by:   Optional[str] = None   # columna o alias; "-" delante = descendente
    limit:     Optional[int] = None   # máximo de filas/grupos
```

#### `metrics[]` — cada métrica es un dict

| Clave | Tipo | Descripción |
|---|---|---|
| `agg` | str | `sum`, `avg`, `median`, `min`, `max`, `std`, `count`, `count_distinct` (y `mean` como alias de `avg` en `AGGREGATIONS`) |
| `field` | str | Columna a agregar. Obligatorio salvo en `count` sin campo (cuenta filas) |
| `alias` | str | Nombre técnico de la columna resultante, **snake_case** (`^[a-z][a-z0-9_]{0,40}$` en la validación de IA) |
| `label` | str? | Nombre a mostrar (hasta 80 chars). Sin él se usa `metric_label()` |
| `filters` | list? | Condiciones **solo de esta métrica** (p. ej. «ventas 2026» y «ventas 2025» en el mismo widget) |
| `window` | dict? | `{"type": "percent_of_total" \| "percent_of_row" \| "running_total" \| "pct_change"}` |
| `type` | `"formula"`? | Marca la métrica como fórmula |
| `expression` | str? | Fórmula entre **alias ya agregados** (`DataFrame.eval`): `"total_g / total_v"` |

Dos métricas sobre el mismo campo no colisionan: la agregación crea una columna intermedia
`__metric_{i}` por métrica.

#### `filters[]` — cada condición es un dict

`{"field", "op", "value"}` o `{"field", "op", "relative"}` (nunca ambos). Operadores y
valores relativos: ver §9.6.

#### `columns[]` — solo el widget «Tabla»

Acepta strings (`"mes"`) u objetos (`{"field": "mes", "label": "Mes"}`); `_normalize_columns()`
normaliza todo a objeto y **omite `label` si está vacío**. La tabla no agrupa ni resume: no
lleva `dimensions` ni `metrics`.

#### Métodos

| Método | Qué hace |
|---|---|
| `from_dict(data)` | Construye desde el JSON guardado; normaliza `trend_by` (`str.strip()`, vacío → `None`) y `columns` |
| `to_dict()` | Serializa a JSON con las 8 claves siempre presentes |

### 5.2 `WidgetStyle` — capa de presentación

```python
@dataclass
class WidgetStyle:
    values: Dict[str, Any] = {}   # {"title": "...", "stacked": true, ...}
```

| Método | Qué hace |
|---|---|
| `from_dict(data)` | Envuelve el dict (o `{}` si no es dict) |
| `to_dict()` | Devuelve el dict crudo |
| `get(key, default)` | Lectura con defecto |
| `__getattr__(name)` | Lee `style.stacked` como `style.values["stacked"]`; `AttributeError` si no existe |

No valida nada por sí misma: la validación es `_clean_style()` (descarta claves fuera del
`style_schema`) + `form_errors()` (tipos y opciones).

### 5.3 `WidgetForm`

```python
@dataclass
class WidgetForm:
    fields: WidgetFields
    style: WidgetStyle
```

`from_dict({"fields": ..., "style": ...})` / `to_dict()`. Es el contrato completo que la IA
propone, que `WidgetService` valida y que se guarda en dos JSONFields separados.

---

## 6. Sistema de widgets

### 6.1 `Registry` (`utils/registry.py`)

Registro genérico, reutilizado por widgets (`WIDGETS`) y operadores de filtro (`FILTER_OPS`,
`RELATIVE_VALUES`).

| Miembro | Descripción |
|---|---|
| `__init__(label, *, instantiate=True)` | `label` nombra la pieza en los mensajes («Tipo de widget»); `instantiate` decide si `register` guarda una **instancia** (`cls()`) o la **clase** |
| `register(cls)` | Decorador; registra bajo `cls.key`. Lanza `ValueError` si la clave ya existe |
| `unregister(key)` | Borra silenciosamente |
| `get(key)` | Instancia/clase; lanza **`UnknownKeyError(ValueError)`**: «{label} desconocido: {key}» |
| `__contains__`, `keys()`, `items()`, `values()`, `__iter__` | Recorrido (el orden es el de registro) |

`UnknownKeyError` la dispara `WIDGETS.get(...)` (tipos desconocidos en el servicio/IA) y
`FILTER_OPS.get(...)` / `RELATIVE_VALUES.get(...)` (operadores inválidos).

### 6.2 `WidgetResult` (`widgets/base.py`)

Salida de `process_query()`: data plana + metadata de la consulta.

| Campo | Tipo | Descripción |
|---|---|---|
| `data` | Any | Data del frontend (dict escalar, lista de records…) |
| `metadata` | dict | Resultado de los pasos (`universe`, `nested`, `dimensions`, `trend`…) |
| `fields` | `WidgetFields?` | Campos usados |
| `type` | str | `scalar`, `flat`, `rows`, `columns`, `pivot_chart`… |
| `frame` | `DataFrame?` | Frame crudo de trabajo (si no, se deriva de `data`) |

- **`from_pipeline(raw)`**: construye desde el dict `{data, type, metadata}` de
  `run_steps` (metadata lleva `fields` dentro).
- **`rows`** (property): el DataFrame de trabajo — `frame` si existe; si no, deriva de `data`
  (DataFrame, lista, `None` → vacío, `{"values": {...}}` → una fila, dict → una fila).
- **`columns`** (property): `list(rows.columns)`.

### 6.3 `BaseWidget` (`widgets/base.py`)

Clase abstracta base. Tres capas declarativas, sin specs:

```python
class BaseWidget(ABC):
    # --- identidad ---
    key: ClassVar[str]              # clave de registro (igual que type_key)
    type_key: ClassVar[str]         # tipo en el modelo Widget
    label: ClassVar[str]            # nombre visible en UI/IA

    # --- capa declarativa ---
    style_schema: ClassVar[List[Dict]] = []   # contrato de `style` (valida; no dibuja)

    # --- reglas planas del tipo ---
    capabilities: ClassVar[dict] = {}
    max_per_dashboard: ClassVar[Optional[int]] = None
    board_filtered: ClassVar[bool] = True
    ai_enabled: ClassVar[bool] = True
    ai_doc: ClassVar[str] = ""
    ai_examples: ClassVar[List[tuple]] = []
```

#### Métodos

| Método | Firma | Qué hace |
|---|---|---|
| `process_query` | `(df, WidgetFields) → WidgetResult` | `run_steps(df, fields, widget_type=self.type_key)` → `WidgetResult.from_pipeline()` |
| `compile` | `(WidgetResult, WidgetStyle, fields?, metadata?) → dict` | Convierte el resultado en el JSON que dibuja el frontend. **Debe implementarse** (`NotImplementedError`) |
| `render` | `(df, form_data) → dict` | **Punto de entrada único**: completa `style` con `style_defaults()`, arma `WidgetForm`, ejecuta `process_query` + `compile`. Captura `KeyError` (columna borrada) y cualquier `Exception` → `{"error": msg, "widget_form": ...}`; éxito → `{"render_data": ..., "widget_form": ...}` |
| `style_defaults` | `() → dict` | `{c["key"]: c["default"] for c in style_schema if "default" in c}` |

#### `capabilities` — claves planas

| Clave | Tipo | Significado |
|---|---|---|
| `dimensions` | `[min, max]` | Rango de dimensiones. `[0,0]` = no admite |
| `pivots` | `[min, max]` | Rango de pivotes |
| `metrics` | `[min, max]` | Rango de métricas. Con `max > 1` cada métrica admite además sus propias condiciones (`metric.filters`); con una sola métrica equivaldrían a `filters` y se rechazan |
| `columns` | `[min, max]` | Rango de columnas (solo «Tabla»); ausente = no admite |
| `sort` | bool | Admite `sort_by` |
| `limit` | bool | Admite `limit` |
| `filters` | bool | Admite filtros propios |
| `trend` | bool | Admite `trend_by` (solo KPI) |
| `dimensions_label`, `dimensions_hint` | str? | Texto alternativo («Filtros» reinterpreta `dimensions` como «Columnas del filtro») |

Las 6 claves base (`dimensions`, `pivots`, `metrics`, `sort`, `limit`, `filters`) son
**obligatorias** y deben ser válidas: `test_capacidades_planas_y_validas`.

#### `style_schema` — contrato de cada clave de `style`

| Clave | Tipo | Descripción |
|---|---|---|
| `key` | str | Clave en `style` (única por widget) |
| `label` | str | Nombre de la clave para la IA y los mensajes de error (obligatoria) |
| `type` | str | `string`, `number`, `boolean` o `choice`; `form_errors` valida el valor contra él |
| `options` | list | Solo en `choice`: `[{"value", "label"}, ...]` |
| `options_from` | `"metrics"` | `choice` cuyo valor es además un **alias de métrica** del propio widget (roles del KPI) |
| `default` | any | Default; debe coincidir con el `type`. `style_defaults()` completa el form con ellos |

Nada más: el schema no dice dónde ni cómo se edita cada clave (`test_style_schema_solo_declara_datos`).
Rangos, etiquetas cortas, secciones plegables o campos deshabilitados son HTML del partial del
tipo. Por ejemplo, el partial del KPI vacía `target` cuando la meta deja de ser «Valor fijo».

`test_el_manifiesto_refleja_cada_tipo` comprueba que el manifiesto del editor
(`_widget_manifest`: etiqueta, `style_schema`, `style_defaults`, capacidades) sale de la clase.

### 6.4 Registro de widgets

```python
# widgets/__init__.py
from sheets_reports.widgets.base import WIDGETS, BaseWidget
from sheets_reports.widgets import kpi, bar, line, donut, dynamic_table, table, filter
```

Importar el paquete registra los 7 tipos. Un widget nuevo son dos piezas:
1. un módulo en `widgets/` con su subclase decorada con `@WIDGETS.register`, importado en
   `widgets/__init__.py`;
2. su panel `templates/sheets_reports/widgets/config/_<key>_config.html` (las dos pestañas,
   armadas con los `blocks/` que necesite) y su `<template x-if>` con el `{% include %}` en
   `board_editor.html`. `test_cada_widget_tiene_su_partial` falla si falta.

`apps.py` valida además que ninguna clave supere `max_length=50` del campo `type`
(`ImproperlyConfigured` al arrancar).

### 6.5 `widgets/presentation.py` — helpers compartidos

| Símbolo | Qué hace |
|---|---|
| `TOTAL_LABEL` | `"Total general"` |
| `AGG_LABELS` | `{agg → etiqueta en español}`: `sum`→«Suma», `avg`→«Promedio», `median`→«Mediana», `min`→«Mínimo», `max`→«Máximo», `std`→«Desviación estándar», `count`→«Conteo de filas», `count_distinct`→«Valores distintos» |
| `agg_label(agg)` | Traducción con fallback al texto crudo |
| `humanize(name)` | `total_ventas` → `Total ventas` (underscore → espacio + mayúscula inicial) |
| `number(value)` | Escalar nativo o `None` si no es número |
| `column_values(df, col)` | Lista con `to_python` |
| `metric_alias(metric)` | `alias` → `field` |
| `metric_label(metric)` | **Rótulo**: `label` del usuario si existe; si no `"{Agg} {Campo}"` («Promedio Ventas»); si no, `humanize(alias)` |
| `chart_series(result, fields?, metadata?)` | `(categories, [(nombre, columna)])`; con pivots: una serie por valor del pivote (`{valor}_{alias}`); sin pivots: una por métrica con su label |
| `percent_aliases(fields)` | Alias cuya ventana es `percent_*` (el frontend los formatea con `%`) |
| `value_column(result, ...)` | Columna numérica de la serie única (dona/tabla) |

---

## 7. Catálogo de tipos de widget

| `key` | Clase | `label` | dimensions | pivots | metrics | columns | sort | limit | filters | trend | `max_per_dashboard` | `board_filtered` |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `kpi` | `KpiWidget` | Tarjeta KPI | [0,0] | [0,0] | [1,4] | — | ✗ | ✗ | ✓ | ✓ | — | ✓ |
| `bar` | `BarChartWidget` | Gráfico de Barras | [1,1] | [0,1] | [1,5] | — | ✓ | ✓ | ✓ | ✗ | — | ✓ |
| `line` | `LineWidget` | Gráfico de Líneas | [1,1] | [0,1] | [1,5] | — | ✓ | ✓ | ✓ | ✗ | — | ✓ |
| `donut` | `DonutWidget` | Gráfico de Dona | [1,1] | [0,0] | [1,1] | — | ✓ | ✓ | ✓ | ✗ | — | ✓ |
| `table` | `TableWidget` | Tabla | [0,0] | [0,0] | [0,0] | [1,50] | ✓ | ✓ | ✓ | ✗ | — | ✓ |
| `dynamic_table` | `DynamicTableWidget` | Tabla Dinámica | [0,3] | [0,2] | [1,5] | — | ✓ | ✓ | ✓ | ✗ | — | ✓ |
| `filter` | `FilterWidget` | Filtros | [0,50]¹ | [0,0] | [0,0] | — | ✗ | ✗ | ✗ | ✗ | **1** | **✗** |

¹ En «Filtros», `dimensions` son las **columnas expuestas como controles** (vacío = todas).
Todos los tipos tienen `ai_enabled = True`.

### 7.1 `style_schema` por tipo (claves)

| Tipo | Controles |
|---|---|
| `kpi` | `title`, `decimals`, `abbreviate`, `prefix`, `suffix`, `primary`, `compare`, `compareMode`, `target`, `targetMetric`, `targetLabel`, `statusBasis`, `status_good`, `status_warn`, `higher_is_better` |
| `bar` | `title`, `horizontal`, `stacked`, `color_scheme`, `yAxisWidth`, `barWidth`, `dataLabelFormatter`, `chartWidth`, `showGrid` |
| `line` | `title`, `color_scheme`, `curve`, `showGrid`, `showMarkers` |
| `donut` | `title`, `labelMode`, `donutSize`, `showLegend` |
| `table` | `title`, `pageSize`, `showPagination`, `boldLastRow` |
| `dynamic_table` | `title`, `pageSize`, `showPagination`, `showTotals`, `rowSubtotal1`, `rowSubtotal2`, `showColumnTotals`, `columnSubtotal1`, `repeatRowLabels`, `boldLastRow` |
| `filter` | `title`, `layout` (`horizontal`/`vertical`) |

### 7.2 Detalle por widget

#### `kpi` — `widgets/kpi.py`

- **`process_query`**: además de los pasos por defecto, si `fields.trend_by` calcula la serie de la
  sparkline (`metadata["trend"] = {categories, series}`). Un fallo en la tendencia no rompe la
  tarjeta (se loguea); un `KeyError` sí se propaga (columna borrada).
- **`_trend()`**: re-ejecuta los pasos con `dimensions=[trend_by]` (mismas métricas y filtros),
  ordena `chronological` y se queda con los últimos `MAX_TREND_POINTS = 60`.
- **`_without_eq_filter_on()`**: dentro de un punto de la serie, quita a la métrica sus filtros
  `eq` sobre `trend_by` (el punto ya fija esa columna; sin quitarlos vaciaría el valor).
- **`compile()`** — roles del número, todos desde `style` con respaldo en el orden de métricas:
  - `primary`: alias elegido o la primera métrica.
  - `compare`: alias elegido si existe y ≠ `primary`; produce `{label, value, delta, delta_pct, mode, better}`.
  - **Meta**: `targetMetric == "fixed"` → `style.target`; si es alias de columna → esa cifra;
    `""` / «— elegir —» → `target = None` (sin barra, aunque quede un `target` viejo guardado).
  - Semáforo: `statusBasis` (`""` automático | `target_pct` | `value`), `status_good` (default 100),
    `status_warn` (default 80), `higher_is_better`; sin base válida → `status = None`.
  - `trend`: solo si `metadata["trend"]` tiene la serie del `primary` con la misma longitud.
  - Formato: `decimals`, `abbreviate` (K/M), `prefix`, `suffix`.
  - Etiquetas: `metric_label()` de la métrica correspondiente.
  - Si `df` está vacío → payload cero sin `compare`/`target`/`status`.

#### `bar` / `line` / `donut` — gráficos

Los tres usan `chart_series()` para `(categories, pairs)` y `percent_aliases()`.

- **`bar`**: `horizontal`, `stacked`; `reference_lines` → `referenceLines` (anotaciones
  ApexCharts con `kind: "value"`, color `#d97706`, label «Meta: {valor}»).
- **`line`**: `curve`, `showMarkers`, `showGrid`, `color_scheme`; si no hay `sort_by`,
  reordena el eje con `chronological()` (best-effort).
- **`donut`**: una sola serie (`pairs[0]`); `labelMode`, `donutSize`, `showLegend` (front).

#### `table` — `widgets/table.py`

- **`process_query`**: los pasos por defecto y luego recorta el frame a `fields.columns` (en ese orden)
  y pone `metadata.dimensions = columnas visibles`.
- **`compile`**: `MAX_ROWS = 2000` (más allá se trunca y se avisa con `truncated`/`total_rows`);
  columnas `{"header", "field", "numeric"}`; `headers` de `label` con fallback `humanize`.
  Salida `type: "tabulator"`.

#### `dynamic_table` — `widgets/dynamic_table.py`

- Depende de `metadata["nested"]` (resultado jerárquico del paso 2, §8.4).
- **`_pivot_columns()`**: columnas de los pivotes anidadas como una hoja — cada valor del primer
  pivote agrupa los del segundo, seguido de su «Total {valor}» (subtotal) y al final
  «Total general». Con 1 métrica todo cuelga de un grupo con su nombre; con varias, cada
  columna se abre en subcolumnas por métrica.
- **`_compile_nested()`**: `columns` (dimensiones + métricas/pivotes), `rows` con
  `__subtotal: True` y etiqueta «Total {valor}» en la última dimensión, `totals` = fila de
  total general, `percent` expandido a `cell_field(...)`/`total_field(...)` si hay pivotes.
- Fallback sin `nested` (escalar/sin dims): `_build_columns` + `_totals` (suma de cada numérica).
- Usa `cell_field()` / `total_field()` del motor (`__pivots.\x1f….alias`, `__total.alias`).

#### `filter` — `widgets/filter.py`

- **No consulta**: `process_query` devuelve `{"columns": [...]}` con las columnas elegidas en
  `dimensions` (o todas) y conserva el `frame` completo.
- **`compile`**: por columna, `distinct_values()` (normalización idéntica a la de las
  condiciones, §9.6) → `multi_select` con hasta `MAX_FILTER_VALUES = 100` opciones y flag
  `truncated`.
- `board_filtered = False` (sus opciones listan la hoja completa, sin recorte) y
  `max_per_dashboard = 1`.
- `capabilities["dimensions"] = [0, 50]` con `dimensions_label`/`dimensions_hint` propios.

---

## 8. Motor: `engine/steps/`

### 8.1 Secuencia y `metadata`

```python
def run_steps(df, fields, widget_type=None):
    metadata = {}
    df = filter_rows(df, fields.filters, metadata)                         # 1
    df = apply_aggregation(df, fields, metadata, widget_type=widget_type)  # 2
    df = apply_calculated_metrics(df, fields.metrics, metadata)            # 3
    df = apply_window_functions(df, fields.metrics, metadata)              # 4
    df = apply_sort_limit(df, fields.sort_by, fields.limit, metadata)      # 5
    return build_query_result(df, fields, metadata)
```

Cada paso es una función pura sobre el frame; el dict `metadata` compartido lleva lo que un
paso deja para los siguientes (`universe`, y la forma del resultado que decide la
agregación). `widget_type="dynamic_table"` pide el resultado jerárquico. Un widget que solo
necesita algunos pasos los encadena él mismo y cierra con `build_query_result`.

### 8.2 Constantes del motor (`engine/steps/aggregation.py`)

| Constante | Valor | Uso |
|---|---|---|
| `AGGREGATIONS` | dict (9 claves) | `sum, avg, mean, median, min, max, std, count, count_distinct` → nombres pandas (`mean`, `nunique`) |
| `MAX_PIVOT_CELLS` | `50_000` | Tope de celdas (filas × columnas × métricas) |
| `KEY_SEPARATOR` | `"\x1f"` | Separa valores de varios pivotes en claves de celda (carácter de control) |
| `ResultTooLargeError` | `ValueError` | Se lanza al superar `MAX_PIVOT_CELLS` |

Helpers públicos: `cell_field(col_key, metric)` → `"__pivots.{clave}.{alias}"`;
`total_field(metric)` → `"__total.{metric}"`; `agg_name(agg)` (traducción a pandas);
`metric_alias(metric)` / `metric_field(metric)`; `hierarchy(leaf_keys, sort_key)`.

### 8.3 Paso 1 — `filter_rows` (`filter.py`)

- Aplica `fields.filters` con `parse_conditions` + `apply_filters` (AND de condiciones). El
  mismo módulo define los operadores (`FILTER_OPS`), los valores relativos
  (`RELATIVE_VALUES`), `Condition` y la validación (`condition_errors`), que también usan las
  métricas y los filtros del tablero.
- Metadata: `universe` (frame **sin** recortar por filtros de métrica: el denominador de los
  porcentajes) y `filters_applied`; `universe` también se guarda cuando no hay filtros.

### 8.4 Paso 2 — `apply_aggregation` (`aggregation.py`)

Despacha según `(widget_type, dimensions, pivots)`:

| Caso | Función | Resultado |
|---|---|---|
| Sin dimensions ni pivots | `_scalar()` | `metadata["scalar_result"] = {"values": {alias: valor}}` |
| `dynamic_table` con dims o pivots | `_nested()` | `metadata["nested"]` (jerárquico) + frame ancho de respaldo |
| Con pivots | `_pivot()` | `pivot_table` de pandas; `pivot_chart_result` si hay dimensión |
| Con dimensions | `_grouped()` | `groupby` + `agg`; `flat_result` |

Detalles importantes:

- **`_metric_columns(df, metrics)`**: crea `__metric_{i}` por métrica (evita colisiones entre
  dos aggs del mismo campo); `count` sin campo usa columnas de `1`; las `filters` propias de la
  métrica enmascaran sus filas como `NaN` (todas las aggs las ignoran → cada métrica resume
  solo sus filas dentro del mismo groupby).
- **`_grouped`**: preserva el orden de aparición de la primera dimensión (`pd.Categorical`
  ordenado) y renombra `__metric_{i}` → `alias`.
- **`_scalar`**: por métrica aplica sus filtros propios (`_metric_rows`) y luego la agg;
  `count` sin campo = número de filas.
- **`_nested`**: `row_leaf`/`col_leaf` (`_ordered_keys`, sin nulos, orden de aparición) →
  `hierarchy()` construye el árbol de claves con subtotales por nivel (como las tablas
  dinámicas de una hoja). Un groupby por pareja (nivel de fila, nivel de columna) en `tables`;
  cruza todas las celdas; controla `MAX_PIVOT_CELLS`. Aplica fórmulas y ventanas **celda a
  celda** (`_cell_metrics`/`_cell_window`: `percent_of_total` sobre el total de su columna,
  `percent_of_row` sobre el de su fila). Metadata `nested`: `{dimensions, pivots, metrics,
  row_leaf, column_keys, cells, rows, grand}`.
- **`_pivot`**: solo usa `pivots[0]`; `fill_value=0`; `sort=False`; renombra
  `{valor}_{columna}` → `{valor}_{alias}`; comprueba el fan-out contra `MAX_PIVOT_CELLS`.

### 8.5 Paso 3 — `apply_calculated_metrics` (`calculated.py`)

- Si `nested` ya calculó celda a celda → no hace nada.
- Si `scalar_result` → evalúa cada `type: "formula"` sobre el dict de
  valores **en el orden de la lista** (una fórmula puede usar la anterior) vía
  `pd.DataFrame([values]).eval()`.
- Si no → `df[alias] = df.eval(expression)` por cada métrica formula.
- Cualquier error de evaluación deja `alias = None` (una fórmula mala no tumba el widget).

### 8.6 Paso 4 — `apply_window_functions` (`window.py`)

Se declara **dentro** de la métrica: `{"alias": "pct", "field": "monto", "agg": "sum",
"window": {"type": "percent_of_total"}}`.

| `window.type` | Transformación |
|---|---|
| `percent_of_total` | `value / total * 100` (redond. 2) |
| `percent_of_row` | Igual que `percent_of_total` en un frame plano; en la tabla dinámica, sobre el total de su fila |
| `running_total` | `cumsum()` |
| `pct_change` | `pct_change() * 100` |

- Trabaja sobre el alias ya agregado (o el campo crudo si el alias no está en el frame).
- **KPI (`scalar`)**: solo aplica `percent_of_total`, con denominador = `universe` (filas del
  widget sin recorte de filtros propios de la métrica).
- `nested`: ya calculado celda a celda → se omite.
- Copia el frame antes de escribir (evita escribir sobre una vista de pandas).

### 8.7 Paso 5 — `apply_sort_limit` (`sort.py`)

- `sort_by`: `"col"` ascendente, `"-col"` descendente; si la columna no existe, no ordena.
- `limit`: `df.head(limit)`.
- **Rama `nested`**: `_sort_nested()` ordena los **hermanos de cada nivel** (por dimensión o
  por la métrica de sus totales; claves `(tipo, valor)` con nulos al final) reconstruyendo la
  jerarquía con `hierarchy`; el subtotal cierra a su grupo. Luego trunca `rows` y el frame.

### 8.8 `build_query_result` y tipos de salida

```python
build_query_result(df, fields, metadata)
# → {"data": ..., "type": ..., "metadata": {...}}
```

Decide la forma final según lo que dejó la agregación en `metadata`:

| Condición | `type` | `data` | Extra |
|---|---|---|---|
| `scalar_result` | `"scalar"` | `{"values": {alias: valor}}` | — |
| `flat_result` | `"flat"` | records del frame (ya ordenado/limitado) | `dimension`, `dimension_values`, `totals` |
| `pivot_chart_result` | `"pivot_chart"` | records | `dimension`, `pivot_values`, `metrics` |
| `metadata["pivoted"]` | `"pivot_table"` | records | `dimensions`, `pivots` |
| sin aggregación | `"rows"` | records | `columns` |
| agregado sin dims ni pivots | `"flat"` | records | — |

`metadata` final siempre incluye: `fields`, `dimensions`, `pivots`, `metrics`,
`pivot_column`, `pivot_values`, `dimension_values`, `totals`, `row_totals`,
`column_totals`, `nested`.
