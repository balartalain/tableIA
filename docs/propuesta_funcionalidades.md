# Propuesta de funcionalidades

Ideas para seguir creciendo la app, ordenadas por cuánto suman frente a lo que cuestan.
Salen de lo que hoy tiene el código y de los huecos que muestra la ayuda.

## Lo más necesario (huecos que hoy se notan)

1. **Tests automáticos en cada push (CI).** Hoy los tests solo corren a mano: un cambio que
   rompe algo se sube sin aviso (pasó con 4 tests de navegador desactualizados). Un workflow de
   GitHub Actions con los tests unitarios y los de Playwright marca el commit en rojo y avisa
   por correo. Poco trabajo.
2. **Agrupar fechas por mes, trimestre o año.** Una columna de fecha agrupa por cada día exacto,
   y la ayuda de Dimensiones pide agregar una columna «mes» a la hoja. Elegir «Fecha › por mes»
   en la dimensión evita tocar la hoja y da tendencias limpias.
3. **Fuente Excel o CSV.** Ya aparece en el selector como «Próximamente». Muchas instituciones
   tienen los datos en archivos, no en Google Sheets.

## Para quien consume los tableros

4. **Compartir con permisos.** Hoy la vista pide iniciar sesión y no hay roles. Compartir con
   correos o dominios concretos (lector / editor) o con un enlace público de solo lectura.
5. **Exportar a PDF o imagen y enviarlo por correo programado** (ej. «el tablero de ventas, cada
   lunes a la gerencia»). El sistema de envío de correos ya existe: falta adjuntar la captura
   del tablero.
6. **Profundizar en el detalle (drill-down).** Clic en una barra o una fila para filtrar el
   tablero por ese valor, o ver las filas que la forman.
7. **Alertas.** Avisar por correo cuando un KPI cruza su meta o su semáforo pasa a rojo; el KPI
   ya tiene meta y semáforo.

## Para quien arma los tableros

8. **Historial de cambios y deshacer.** El tablero se guarda solo, así que un error borra
   trabajo. Un historial de versiones o deshacer los últimos cambios da tranquilidad.
9. **Actualización programada de las fuentes.** Hoy los datos se releen al conectar o con
   «Actualizar datos». Releerlos cada cierta hora, con el mismo cron que ya corre para los
   correos.
10. **Comparar periodos en un clic.** «Este mes frente al anterior» o «frente al mismo mes del
    año pasado», sin armar dos métricas con condiciones.

## Con la IA que ya tiene la app

11. **Explicar un cambio.** «¿Por qué bajaron las ventas en marzo?» El chat con los datos ya
    calcula con Python; se podría lanzar desde un punto de un gráfico.
12. **Detectar anomalías.** Marcar en los gráficos los valores fuera de lo normal, con una nota
    de la IA.

## Por dónde empezar

Las tres primeras recomendadas, porque reutilizan mucho de lo que ya existe:

| | Funcionalidad | Por qué primero |
|---|---|---|
| 1 | CI | Evita que los errores pasen sin aviso; base para todo lo demás. |
| 2 | Agrupar fechas | Mejora casi todos los gráficos de tendencia sin cambiar las hojas. |
| 5 | Exportar y enviar por correo | Aprovecha el envío de correos que ya funciona. |
