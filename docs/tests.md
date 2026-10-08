# Tests

```bash
venv/bin/python manage.py test sheets_reports                      # toda la suite (~9 s)
venv/bin/python manage.py test sheets_reports.tests.test_config_matrix

# En el navegador (Playwright + Chromium; necesitan internet para los CDN). Una vez:
#   venv/bin/pip install -r requirements-dev.txt
#   venv/bin/python -m playwright install chromium
E2E=1 venv/bin/python manage.py test sheets_reports.tests.e2e
# Para verlas: con ventana y 300 ms entre acciones; PWDEBUG=1 en vez, paso a paso con el Inspector.
HEADED=1 SLOWMO=300 E2E=1 venv/bin/python manage.py test sheets_reports.tests.e2e

# Cobertura (pip install -r requirements-dev.txt)
venv/bin/coverage run --branch --source=sheets_reports manage.py test sheets_reports
venv/bin/coverage report --sort=cover
```

## Qué prueba cada archivo

### Configuraciones del panel (lo que puede armar el usuario)
| Archivo | Qué prueba |
|---|---|
| `tests/test_config_matrix.py` | **Todas** las configuraciones que permiten las `capabilities` de cada widget (dimensiones, pivotes, agregaciones, «Mostrar como», métricas con condición, tendencia, columnas de la tabla): o la validación las rechaza con un mensaje, o se dibujan sin error y cumplen los invariantes del tipo (`INVARIANTS`: % que suman 100, acumulado que no baja, totales = suma de las filas, formatos en todas las columnas…). `debe_rechazarse` lista, en un solo lugar, las configuraciones que se pueden armar pero no tienen sentido (ej. Promedio con «% del total»). |
| `tests/test_config_matrix.py` · `PanelParityTests` | Lo que el panel ofrece (`panel_options` en el manifiesto: «Mostrar como» por agregación y pivote, agregaciones solo numéricas, formatos) es exactamente lo que el servidor acepta al guardar. |
| `tests/engine/test_validation.py` | `form_errors`: casos puntuales de la validación (capacidades, columnas, condiciones, estilo, título). |
| `tests/test_architecture.py` | Capas que no se mezclan y el contrato de cada widget registrado: claves, capabilities, `style_schema`, manifiesto, ejemplos de la IA válidos, partial del panel y **sus tests** (entrada en `INVARIANTS` y `tests/widgets/test_<tipo>.py`). |

### Widgets (lo que cada uno le devuelve al frontend)
Un archivo por widget en `tests/widgets/test_<tipo>.py`: `test_bar`, `test_line`, `test_donut`, `test_kpi` (roles del número, meta, tendencia), `test_dynamic_table` (filas, columnas del pivote, totales, formatos), `test_table` (filas tal cual, API), `test_filter` (caja de filtros y filtros del tablero). Además:
- `test_compile.py`: contrato común de `render` (render_data + widget_form, o error sin tumbar el form).
- `test_styles_chema.py`: el `style_schema` como contrato del estilo.

### Panel en el navegador (`tests/e2e/test_panel.py`, solo con `E2E=1`)
El JS del panel y el dibujo de las tablas, con la hoja simulada: el formato de la fuente llega a todas las columnas de la tabla dinámica; «Mostrar como» desaparece al pasar a Promedio; la dimensión obligatoria no se puede quitar; sobre una columna de texto solo se cuenta; la tabla dinámica llena el ancho y el total queda pegado a las filas.

`FormulaBuilderTests`, el constructor de fórmulas por bloques: armar un SI solo con clics y guardarlo; arrastrar de verdad (mouse) un operador que envuelve un bloque, un valor a un hueco y un bloque al panel para quitarlo; ida y vuelta árbol del servidor → texto del constructor → el mismo árbol.

### Motor
| Archivo | Qué prueba |
|---|---|
| `tests/engine/test_steps.py` | Los pasos: filtros, agregación, pivote, escalar, ventanas, orden/límite, metadatos y el resultado anidado de la tabla dinámica. |
| `tests/engine/test_formulas.py` | Lenguaje de fórmulas, cómo opera cada función, el árbol para el constructor (`formula_tree`) y campos calculados (por fila y agregados) en el motor. |

### Fuentes de datos y API
| Archivo | Qué prueba |
|---|---|
| `tests/test_sources.py` | Conectar una hoja: Drive, pestañas, tipos de columna, caché de la hoja. |
| `tests/test_data_sources.py` | Fuentes del tablero: CRUD, renombrar/reemplazar/actualizar con aviso de impacto, campos calculados, formato de columnas (moneda, %), varios widgets por fuente. |
| `tests/test_source_columns.py` | Las columnas que usa cada widget (para renombrar y avisar qué se rompe). |
| `tests/test_views.py` | Las vistas como adaptadores HTTP: render, guardar widgets, asistente de IA, tableros. |

### IA
| Archivo | Qué prueba |
|---|---|
| `tests/test_ai_spec.py` | Generación del formulario por la IA (con el modelo simulado): reintento con errores, schema de la tool, prompt. |
| `tests/test_ai_suggestions.py` | Pedidos sugeridos del chat: limpieza y caché. |

## Widget nuevo
1. Su clase en `sheets_reports/widgets/` y su partial del panel.
2. Su entrada en `INVARIANTS` (`tests/test_config_matrix.py`): qué cumple siempre lo que dibuja. La matriz recorre sola sus `capabilities`.
3. `tests/widgets/test_<tipo>.py` con sus casos concretos.

`test_architecture` falla si falta el 2 o el 3.
