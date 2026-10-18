# Configuración del envío de correos

Cada tablero puede enviar un mensaje a una lista de destinatarios. Cada destinatario lo
recibe **una sola vez**; el envío se reparte en lotes y, si hace falta, en varios días. Este
es el único documento del envío de correos: las secciones 1 a 5 son la guía de uso y las
secciones 6 y 7, el detalle técnico.

## 1. Servidor

### Variables de entorno (`.env`)

| Variable | Por defecto | Para qué |
|---|---|---|
| `CAMPAIGN_TIME_ZONE` | `America/Santo_Domingo` | Zona horaria de la hora de envío que se elige en el tablero. |
| `CAMPAIGN_CRON_MINUTES` | `5` | Cada cuánto despierta el cron; debe coincidir con el intervalo del crontab. La interfaz avisa de las horas de envío que casi no alcanzan a correr. |
| `EMAIL_ENCRYPTION_KEY` | vacía | Clave con la que se cifran las contraseñas de los remitentes. Vacía, se deriva de `SECRET_KEY` (cambiar `SECRET_KEY` obligaría a escribirlas de nuevo). |
| `CAMPAIGN_SMTP_HOST` | `smtp.gmail.com` | Servidor SMTP de los remitentes. |
| `CAMPAIGN_SMTP_PORT` | `587` | Puerto SMTP (TLS). |
| `CAMPAIGN_EMAIL_BACKEND` | SMTP de Django | Cómo se envía; para probar sin enviar correos, ver «Probar sin enviar correos». |

Generar una `EMAIL_ENCRYPTION_KEY`:

```
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### Base de datos

```
venv/bin/python manage.py migrate
```

### Crontab

El envío lo arranca el crontab; nada se envía desde la web salvo «Enviar prueba».

```
*/5 * * * * cd /ruta/TableIA && venv/bin/python manage.py send_campaign_emails
```

El cron solo **dispara** el envío cuando llega la hora: su frecuencia es cuánto puede
atrasarse el arranque, no el ritmo de los correos. El comando puede durar varios minutos
(ver §4); una pasada que encuentra un envío en curso no hace nada.

El intervalo del crontab debe coincidir con `CAMPAIGN_CRON_MINUTES` (arriba). Con él, la
interfaz avisa cuando la hora de envío cae en la última franja de ticks antes de medianoche
(p. ej. 23:56–23:59 con 5 minutos), donde un tick puede no alcanzarla y el envío no arranca.

Para probar a mano:

```
venv/bin/python manage.py send_campaign_emails --dashboard <id> --force
```

`--dashboard` limita al envío de un tablero; su id es el número de la URL del tablero, por
ejemplo `/tableros/19/edit/`.

`--force` arranca el envío ahora mismo, sin esperar a que le toque. Se salta:

- la hora de envío;
- el día (envía aunque hoy no sea día de envío);
- el día cerrado (envía aunque hoy ya se haya ejecutado y terminado);
- solo junto con `--dashboard`: que el envío esté desactivado. Con `--force` solo, se
  ejecutan únicamente los envíos activados.

Sigue respetando:

- el máximo de correos al día (si hoy ya se alcanzó, no envía nada y lo anota en el registro);
- que cada persona lo recibe una sola vez: solo envía a los pendientes;
- los lotes y la pausa de 1 minuto entre ellos;
- el envío en curso: si ya hay uno corriendo para ese tablero, no arranca otro.

Sin `--force`, el comando hace lo mismo que el cron: revisa si al envío le toca y, si no,
termina sin hacer nada.

### Probar sin enviar correos

En el `.env` **local**:

```
CAMPAIGN_EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend
```

Ningún correo sale a Gmail: cada mensaje se imprime completo en la terminal. Los lotes, la
pausa y la pestaña «Seguimiento de correos» se comportan como en un envío real.

1. Reiniciar `runserver` después de cambiar el `.env`.
2. En el tablero, guardar al menos un remitente con cualquier contraseña (se exige que
   exista, pero no se usa).
3. Lanzar `send_campaign_emails --dashboard <id> --force`: los correos aparecen en esa
   terminal. «Enviar prueba» los imprime en la terminal de `runserver`.

A tener en cuenta:

- Los destinatarios quedan como **notificados** aunque no haya salido ningún correo. Tras
  probar con una lista real hay que vaciarla y cargarla de nuevo para que el envío real
  les llegue.
- No valida la contraseña de aplicación ni los rechazos de Gmail: todo «se envía» bien.
- No dejar esa línea en el `.env` del servidor: el envío parecería funcionar sin mandar nada.

## 2. Remitentes (cuentas de Gmail)

Cada remitente es una cuenta de Gmail con una **contraseña de aplicación**, no la contraseña
del correo:

1. En la cuenta de Google, activar la verificación en dos pasos.
2. Entrar a <https://myaccount.google.com/apppasswords>, crear una contraseña de aplicación
   y copiar las 16 letras. alain.perez@uapa.edu.do => dwpn vhng yuma ffal
3. Pegarla en el tablero junto al correo del remitente (con o sin espacios).

La contraseña se guarda cifrada y no vuelve al navegador: el campo aparece vacío, y solo se
cambia si se escribe otra. Gmail admite unos **500 correos al día por cuenta**; con más
remitentes se puede subir el máximo diario.

## 3. El tablero (botón «Configurar envío de correos» del editor)

| Campo | Qué es |
|---|---|
| **Destinatarios** | Un CSV con un correo por fila (de cada fila se toma la primera celda que sea un correo; la primera fila puede ser el encabezado). Los nuevos se agregan; los que ya estaban conservan su estado. «Vaciar lista» borra también quién ya fue notificado y desactiva el envío. |
| **Remitentes** | Correo y contraseña de aplicación (§2). |
| **Máximo de correos al día** | Tope de correos por día de envío, entre todos los remitentes. |
| **Tamaño del lote por remitente** | Cuántos correos envía cada remitente en cada lote. |
| **Frecuencia** | Todos los días, o los días de la semana que se marquen. |
| **Hora** | Hora a la que arranca el envío, en `CAMPAIGN_TIME_ZONE`. |
| **Asunto y cuerpo** | El mensaje, en texto plano. |
| **Enviar prueba** | Guarda lo escrito y envía el mensaje a una dirección, con el primer remitente. |
| **Activar el envío** | Exige asunto, cuerpo, al menos un remitente y destinatarios. Suma la pestaña «Seguimiento de correos» a la vista compartida. |

## 4. Cómo se envía

A la hora indicada arranca una ejecución que:

1. Envía un **lote**: hasta «tamaño del lote» correos por cada remitente.
2. Espera **1 minuto** (fijo en el código: `BATCH_PAUSE_S` en `services/mailing.py`).
3. Repite hasta llegar al **máximo de correos al día** o quedarse sin pendientes.

Los pendientes que no entraron siguen el próximo día de envío.

**Ejemplo:** 1 200 destinatarios, 2 remitentes, lote de 50 y máximo de 500 al día. Cada
lote son 100 correos (50 × 2): el día hace 5 lotes, unos 5 minutos más lo que tarde Gmail
en aceptar los correos. El envío termina al tercer día de envío (500 + 500 + 200).

Lo que pasa cuando algo falla:

- **Un destinatario falla** (dirección rechazada): queda pendiente y se reintenta el
  próximo día de envío; al tercer intento queda como «Falló».
- **Un remitente falla** (contraseña mal, cuenta bloqueada, tope de Gmail): no se usa más
  ese día y los demás siguen con sus destinatarios. Si no queda ninguno, el envío del día
  termina. Tras corregirlo, **Guardar** en el tablero reabre el día y el envío se retoma en
  la siguiente pasada del cron.
- **El proceso se corta** (reinicio del servidor): la siguiente pasada del cron lo retoma
  pasados 10 minutos, sin repetir a nadie ni pasar del máximo.

## 5. Seguimiento

Con el envío activado, la vista compartida del tablero tiene la pestaña **«Seguimiento de correos»**, que se
actualiza sola cada 10 segundos:

| Pieza | Qué responde |
|---|---|
| **Franja de estado** | ¿En qué está el envío? «Enviando ahora» (con cuántos van de hoy), «Próximo envío», «El envío de hoy terminó antes de llegar al máximo» (con el último error) o «Envío completado». |
| **Notificados** | ¿Cuánto falta? Total notificado, barra con notificados, pendientes y fallidos, y la fecha aproximada en que termina. |
| **Enviados por día** | ¿Va al ritmo esperado? Una barra por día y la línea del máximo diario; una barra corta es un día que no llegó al máximo. |
| **Remitentes** | ¿Qué cuenta falla? Por cuenta: enviados hoy, en total, errores y el último error. |
| **Errores por motivo** | ¿Por qué falla? Los errores agrupados y contados: distingue direcciones que no existen de un problema de la cuenta. |
| **Destinatarios** | ¿Qué pasó con una persona? Estado, fecha, remitente y error de cada correo. |
| **Registro de envíos** | El detalle cronológico (inicio, cada lote, cada error, resumen). Está plegado: se despliega al hacer clic. |

«Terminó antes de llegar al máximo» significa que quedaron pendientes sin enviar hoy: o
ningún remitente pudo enviar (revisar «Remitentes»), o los que quedan fallaron y se
reintentan el próximo día de envío.

En el admin de Django están los mismos datos (`Envío de correoss`, `Destinatarios`, `Logs de
correos`).

## 6. Backend (`services/mailing.py`, `views_email.py`)

### 6.1 Modelos

| Modelo | Qué guarda |
|---|---|
| `EmailCampaign` | Uno por tablero (`dashboard.email_campaign`): `enabled`, `subject`, `body`, `daily_limit` (máximo de correos al día), `batch_size` (correos por remitente en cada lote), `frequency` (`daily` \| `weekly`), `send_time`, `weekdays` (0 = lunes … 6 = domingo, solo en `weekly`), `last_run_on` (día local cerrado: ese día ya no se envía más) y `running_since` (hay un lote en curso). |
| `EmailSender` | Cuenta de Gmail que envía: `email` y `password_encrypted` (su contraseña de aplicación, cifrada). |
| `EmailRecipient` | Destinatario: `email`, `status` (`pending` \| `sent` \| `failed`), `attempts`, `sent_at`, `sender_email` y `error`. |
| `EmailLog` | Bitácora: `created_at`, `level` (`info` \| `error`), `message`, `recipient_email`, `sender_email`. |

### 6.2 Servicio (`services/mailing.py`)

- **Contraseñas:** `encrypt_password` / `decrypt_password` (Fernet con `EMAIL_ENCRYPTION_KEY`
  o, sin ella, una clave derivada de `SECRET_KEY`). Nunca se devuelven al navegador.
- **`parse_recipients_csv(content)`** → `(válidos, inválidos)`: de cada fila, la primera celda
  que es un correo; en minúsculas y sin repetir. Una primera fila sin correo es el encabezado.
- **`is_due(campaign, now)`**: activada, con pendientes, hoy es día de envío, ya pasó
  `send_time`, el día no está cerrado (`last_run_on`) y queda cupo de `daily_limit`. La hora
  y el día son de `CAMPAIGN_TIME_ZONE`. Un cron atrasado la ejecuta igual; `next_run` da la
  próxima fecha (ahora mismo si el envío de hoy quedó sin terminar).
- **`run_campaign(campaign, now, force)`** hace **el envío del día**, en lotes:
  1. Toma el candado con un `update` condicionado sobre `running_since`: de dos crons
     solapados, solo uno sigue (el otro recibe `None`). El envío renueva el candado en cada
     lote; uno de más de `LOCK_STALE` (10 min) es de un proceso que murió y no bloquea. Con
     el día cerrado también devuelve `None`, salvo `force`.
  2. Cada lote: cupo = el menor entre `daily_limit` − enviados hoy y `batch_size` ×
     remitentes; toma esa cantidad de pendientes y los reparte en ronda entre los remitentes
     con contraseña (a lo sumo `batch_size` cada uno). Cada remitente envía los suyos por
     una conexión SMTP (`CAMPAIGN_SMTP_HOST`/`PORT`, TLS).
  3. Entre un lote y el siguiente espera `BATCH_PAUSE_S` (60 s, fijo en el código). Sigue
     hasta llegar al máximo del día, quedarse sin pendientes o sin remitentes, o cambiar el
     día.
  4. Un destinatario que falla suma un intento y sigue pendiente, pero no se reintenta en
     la misma ejecución sino el próximo día de envío; al tercero (`MAX_ATTEMPTS`) queda
     `failed`. Si falla el remitente (no inicia sesión, se desconecta, el servidor lo
     rechaza), no se usa más en esa ejecución y sus destinatarios quedan pendientes, sin
     sumar intentos, para los demás remitentes.
  5. Al terminar cierra el día (`last_run_on`). Si el proceso se corta a medias el día
     queda abierto: la siguiente pasada del cron retoma cuando el candado vence.
  6. Deja en `EmailLog` el inicio, cada lote, cada error y el resumen.
- **`send_test(campaign, to)`**: el mensaje a una dirección, con el primer remitente.
- **`report_summary(campaign)`** (lo que se consulta cada pocos segundos): `state`
  (`running`: hay un envío en curso · `done`: sin pendientes · `stopped`: el día se cerró
  antes del máximo y quedan pendientes · `scheduled`: espera su próxima ejecución), totales
  por estado, `sent_today`, `daily_limit`, `last_log_id`, `next_run`, `estimated_end` (el día
  en que se acaban los pendientes al ritmo del máximo diario) y `last_error` (el último error
  de hoy).
- **`report_stats(campaign)`** (se pide cuando hay novedades): `by_day` (enviados por día
  local, últimos `STATS_DAYS`), `by_sender` (por remitente: enviados hoy y en total, errores
  y último error; incluye los configurados sin envíos y los quitados que enviaron) y
  `errors` (los `STATS_ERRORS` motivos más frecuentes, juntando los intentos del mismo).

### 6.3 Comando y crontab

`manage.py send_campaign_emails` recorre los envíos activados y ejecuta los que `is_due`.
El cron solo dispara el arranque: el ritmo de los lotes lo lleva `run_campaign`, así que el
comando puede durar varios minutos.
Las opciones `--dashboard` y `--force` y la línea del crontab están en §1.

### 6.4 Endpoints (`views_email.py`)

Del dueño del tablero:

| Ruta | Qué hace |
|---|---|
| `GET /api/dashboard/{id}/email-campaign/` | La configuración, los remitentes (`email`, `has_password`) y `report`. Sin envío guardado, los valores por defecto. |
| `PUT /api/dashboard/{id}/email-campaign/` | Guarda. Un remitente sin `password` conserva la suya; el que no viene se quita. Para activar exige asunto, cuerpo, un remitente y destinatarios. Guardar reabre el día (`last_run_on = None`). |
| `POST /api/dashboard/{id}/email-campaign/recipients/` | CSV (`file`, multipart) → `{added, duplicates, invalid, invalid_count}`. Los que ya estaban conservan su estado. |
| `DELETE /api/dashboard/{id}/email-campaign/recipients/` | Vacía la lista y desactiva el envío. |
| `POST /api/dashboard/{id}/email-campaign/test/` | `{to}` → envía la prueba. |

De solo lectura, para la vista compartida; responden 404 si el envío no está activado:

| Ruta | Qué devuelve |
|---|---|
| `GET /api/dashboard/{id}/email-report/` | `report_summary`: lo que la página consulta cada pocos segundos. |
| `GET /api/dashboard/{id}/email-report/stats/` | `report_stats`: enviados por día, por remitente y errores por motivo. |
| `GET /api/dashboard/{id}/email-report/recipients/?page&size&status` | Destinatarios paginados: `{rows, total_rows, last_page}`. |
| `GET /api/dashboard/{id}/email-report/logs/?page&size` | Bitácora paginada, lo más reciente primero. |

`board_view` pasa `email_enabled` a su template.

## 7. Frontend

**Configuración (editor).** El botón «Configurar envío de correos» del header abre el bottom sheet con
`view: 'email'` (el store `bottomSheet` elige entre `'sources'` y `'email'`) y despacha
`email-campaign:open`. `emailCampaign()` (`email-campaign.js`, `email_campaign.html`) carga
la configuración y la guarda entera con «Guardar»:

- **Destinatarios:** «Cargar CSV» sube el archivo y muestra cuántos se agregaron, cuántos ya
  estaban y cuáles no son un correo. «Vaciar lista» pide confirmación.
- **Remitentes:** correo y contraseña de aplicación. Una contraseña ya guardada no vuelve al
  navegador: el campo queda vacío y solo se envía si se escribe otra.
- **Programación:** máximo de correos al día, tamaño del lote por remitente, frecuencia
  (todos los días o días de la semana) y hora. Muestra el próximo envío que calcula el servidor.
- **Mensaje:** asunto y cuerpo. «Enviar prueba» guarda lo escrito y manda el mensaje a la
  dirección indicada.
- **Activar el envío:** el servidor lo rechaza si falta el mensaje, un remitente o los
  destinatarios.

**Reporte (vista compartida).** Con el envío activado, `board_view.html` trae las pestañas
«Tablero» y «Seguimiento de correos» y carga `email-report.js`. La pestaña activa va en el hash
(`#correos`). Al volver a «Tablero» se despacha `resize` para que los gráficos tomen su
ancho. La pestaña «Seguimiento de correos» tiene:

- la franja de estado (`state` del reporte): enviando ahora, próximo envío, terminó antes
  del máximo (con el último error) o completado;
- «Notificados»: el total, una barra de avance (notificados, pendientes, fallidos) y la
  fecha estimada de fin;
- «Enviados por día»: barras (ApexCharts) con la línea del máximo diario;
- «Remitentes» y «Errores por motivo»: tablas cortas (Tabulator, datos locales);
- la tabla de destinatarios, con paginación remota;
- el registro de envíos, plegado (`<details>`): su tabla se crea al desplegarlo.

**Aviso de novedades.** No hay websocket: mientras la pestaña «Seguimiento de correos» está a la vista, la
página pide cada 10 s `GET /email-report/` (unos conteos). Si cambian los totales o
`last_log_id`, actualiza la franja y el avance, pide `GET /email-report/stats/` (gráfico,
remitentes, errores) y vuelve a pedir las tablas paginadas. Con el documento oculto no
consulta, y al volver consulta de inmediato.
