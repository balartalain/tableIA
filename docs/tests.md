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
Un archivo por widget en `tests/widgets/test_<tipo>.py`: `test_bar`, `test_line`, `test_donut`, `test_kpi` (roles del número, meta, tendencia), `test_dynamic_table` (filas, columnas del pivote, totales, formatos), `test_table` (filas tal cual, API), `test_filter` (caja de filtros y filtros del tablero), `test_correlation` (Pearson entre números y Spearman con una ordinal o una dummy, nominales como una fila por categoría y dicotómicas como una, celdas de la misma variable, ordinales por su escala, escala guardada, valores fuera del orden, topes de categorías y filas). Además:
- `test_compile.py`: contrato común de `render` (render_data + widget_form, o error sin tumbar el form).
- `test_styles_chema.py`: el `style_schema` como contrato del estilo.

### Panel en el navegador (`tests/e2e/test_panel.py`, solo con `E2E=1`)
El servidor de pruebas atiende las peticiones de a una (`SerialLiveServerThread`): con SQLite en memoria todos los hilos comparten una conexión, y las peticiones simultáneas de la página daban errores 500 al azar.

El JS del panel y el dibujo de las tablas, con la hoja simulada: el formato de la fuente llega a todas las columnas de la tabla dinámica; «Mostrar como» desaparece al pasar a Promedio; la dimensión obligatoria no se puede quitar; sobre una columna de texto solo se cuenta; la tabla dinámica llena el ancho y el total queda pegado a las filas; un porcentaje de un campo calculado sale con 2 decimales; la tabla de fuentes («Sin usar», columnas incluidas, estado, confirmación al eliminar); el formateador común de números (`formatNumber`); el grupo «Cuadrículas y casillas» de una fuente de formulario y el pivote que se completa al elegir «· Aspecto».

`FormulaBuilderTests`, el constructor de fórmulas por bloques: armar un SI solo con clics y guardarlo; arrastrar de verdad (mouse) un operador que envuelve un bloque, un valor a un hueco y un bloque al panel para quitarlo; una función sin huecos (HOY) se dibuja con su nombre; «Generar con IA» (IA simulada) reemplaza el lienzo o muestra el motivo si no pudo.

### Videos de demostración (`tests/e2e/demo_videos.py`, solo con `DEMO=1`)
No es parte de la suite: el nombre no empieza con `test_`, así que solo corre si se lo nombra.
Graba un `.webm` por flujo, con su portada (`.png`, la última imagen), en `sheets_reports/static/sheets_reports/help/videos/`. Están en git porque los muestra el centro de ayuda («Ver video» en cada tópico, según `HELP_VIDEOS` de `sheets_reports/help.py`): regrabarlos actualiza la ayuda.

```bash
DEMO=1 venv/bin/python manage.py test sheets_reports.tests.e2e.demo_videos                                     # los 9
DEMO=1 venv/bin/python manage.py test sheets_reports.tests.e2e.demo_videos.DemoVideos.test_05_generar_tablero_con_ia  # uno
HEADED=1 DEMO=1 venv/bin/python manage.py test sheets_reports.tests.e2e.demo_videos                            # mirándolos
```

Los flujos son:
1. Crear tablero.
2. Nombres de columnas con IA.
3. Agregar fuente.
4. Agregar widgets.
5. Generar tablero con IA.
6. Asistente IA del widget.
7. Análisis IA de un widget.
8. Chat con los datos.
9. Campos calculados.

Todo lo externo está simulado para que cada grabación salga igual:
- **La hoja:** `demo_df()`, con 96 ventas, y sus pestañas.
- **La cuenta de Google y el selector de Drive:** un `google.picker` falso que «elige» la hoja de la demo.
- **Las respuestas de la IA:** son textos fijos (`AI_PLAN`, `ASSISTANT_ANSWER`, `INSIGHT`, `CHAT_ANSWERS`, …). Si cambian los datos de la demo, conviene revisar que esos textos sigan diciendo lo mismo que los números.

Cómo se graba:
- El cursor y los subtítulos de cada paso los dibuja `OVERLAY`, porque Playwright no muestra el mouse.
- Cada video falla si la página tiene un error de JS.
- Para pasarlos a mp4 (`brew install ffmpeg`): `ffmpeg -i sheets_reports/static/sheets_reports/help/videos/01_crear_tablero.webm -c:v libx264 -crf 20 -pix_fmt yuv420p 01_crear_tablero.mp4`.

### Motor
| Archivo | Qué prueba |
|---|---|
| `tests/engine/test_steps.py` | Los pasos: filtros (con los valores relativos: este mes y el mes anterior del calendario sobre fechas, año-mes y meses sueltos), agregación, pivote, escalar, ventanas, orden/límite, metadatos y el resultado anidado de la tabla dinámica. |
| `tests/engine/test_formulas.py` | Lenguaje de fórmulas, cómo opera cada función, el árbol del constructor (`formula_tree`, `formula_text` con su ida y vuelta, el catálogo de piezas), las funciones de fecha (partes, `DATEDIFF`, `TODAY`, fechas que no lo son, el orden de meses, días y año-mes) y campos calculados (por fila y agregados) en el motor. |

### Fuentes de datos y API
| Archivo | Qué prueba |
|---|---|
| `tests/test_sources.py` | Conectar una hoja: Drive, pestañas, tipos de columna, caché de la hoja. |
| `tests/test_data_sources.py` | Fuentes del tablero: CRUD, renombrar/reemplazar/actualizar con aviso de impacto, campos calculados, formato de columnas (moneda, %), varios widgets por fuente. |
| `tests/test_source_columns.py` | Las columnas que usa cada widget (para renombrar y avisar qué se rompe). |
| `tests/test_grids.py` | Cuadrículas y preguntas de casillas de formulario como columnas virtuales: formato largo (simple y de casillas), la casilla como cuadrícula de opciones (% que marcó cada opción), Aspecto × Respuesta con conteo y % de la fila, filtros en ancho antes de desdoblar y sobre las virtuales después, promedio de una escala numérica, dos cuadrículas rechazadas, columna ancha excluida que sigue en la cuadrícula, un aspecto fuera con un filtro, contexto de la IA, validación, filtros del tablero, editor de la fuente (posición, valores por defecto, alias iguales a los del motor) y guardado. |
| `tests/test_column_scales.py` | Escala de medida de las columnas: reglas de detección (escalas conocidas, «No aplica» como categoría válida, identificadores, discreta y continua), la guardada en la fuente manda, el PUT de escalas con su validación y «Clasificar con IA» (modelo simulado). |
| `tests/test_views.py` | Las vistas como adaptadores HTTP: render, guardar widgets, asistente de IA, tableros. |

### Tareas en segundo plano
| Archivo | Qué prueba |
|---|---|
| `tests/test_tasks.py` | Las corridas del cron (`TaskRun`): registro con estado y duración, el mensaje de error que se relanza, la poda de las viejas y el registro que dejan `send_campaign_emails` y `sync_gemini_files` (una fuente que falla no corta las demás y deja la corrida en error). |

### IA
| Archivo | Qué prueba |
|---|---|
| `tests/test_ai_spec.py` | Generación del formulario por la IA (con el modelo simulado): reintento con errores, reparación adicional acotada de correlaciones con escalas mezcladas, conservación de escalas efectivas, schema de la tool y prompt. |
| `tests/test_ai_suggestions.py` | Pedidos sugeridos del chat: limpieza y caché. |
| `tests/test_ai_formula.py` | «Generar con IA» de un campo calculado (modelo simulado): fórmula con su árbol, motivo cuando no puede, reintento con el error del motor, errores legibles. |
| `tests/test_email_campaign.py` | Envío de correos (con el backend de correo en memoria): lectura del CSV, cifrado de la contraseña, el mensaje en HTML con su alternativa en texto y el saneo del cuerpo, cuándo le toca ejecutarse (diario, semanal, zona horaria, cupo del día, día cerrado), la ejecución (tope por remitente, correos espaciados uno por segundo, reparto en ronda entre remitentes, candado entre crons, remitente que falla, reintentos, remitente que no inicia sesión, bitácora), sus endpoints (la contraseña nunca sale, validación, solo el dueño), el reporte (estado del envío, fecha estimada de fin, enviados por día, por remitente, errores por motivo, tablas paginadas) y el comando del crontab. |

## Widget nuevo
1. Su clase en `sheets_reports/widgets/` y su partial del panel.
2. Su entrada en `INVARIANTS` (`tests/test_config_matrix.py`): qué cumple siempre lo que dibuja. La matriz recorre sola sus `capabilities`.
3. `tests/widgets/test_<tipo>.py` con sus casos concretos.

`test_architecture` falla si falta el 2 o el 3.
