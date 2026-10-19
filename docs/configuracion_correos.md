# Configuración del envío de correos

Cada tablero puede enviar un mensaje a una lista de destinatarios. Cada destinatario lo
recibe **una sola vez**; el envío se reparte entre los remitentes y, si hace falta, en varios días. Este
es el único documento del envío de correos: las secciones 1 a 5 son la guía de uso y las
secciones 6 y 7, el detalle técnico.

## 1. Servidor

### Variables de entorno (`.env`)

| Variable | Por defecto | Para qué |
|---|---|---|
| `CAMPAIGN_TIME_ZONE` | `America/Santo_Domingo` | Zona horaria de la hora de envío que se elige en el tablero. |
| `CAMPAIGN_CRON_MINUTES` | `5` | Cada cuánto despierta el cron; debe coincidir con el intervalo del crontab. La interfaz avisa de las horas de envío que casi no alcanzan a correr. |
| `DATA_UPLOAD_MAX_MEMORY_SIZE` | `3145728` (3 MB) | Tope del cuerpo de una petición que Django lee en memoria. El mensaje se guarda con un `PUT` JSON y lleva las imágenes incrustadas en base64, así que supera el valor por defecto de Django (2.5 MB). |
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

- el cupo del día (si hoy ya se alcanzó, no envía nada y lo anota en el registro);
- que cada persona lo recibe una sola vez: solo envía a los pendientes;
- el ritmo entre correos (1 por segundo, `SEND_INTERVAL_S`);
- el envío en curso: si ya hay uno corriendo para ese tablero, no arranca otro.

Sin `--force`, el comando hace lo mismo que el cron: revisa si al envío le toca y, si no,
termina sin hacer nada.

### Probar sin enviar correos

En el `.env` **local**:

```
CAMPAIGN_EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend
```

Ningún correo sale a Gmail: cada mensaje se imprime completo en la terminal. El ritmo, el
reparto y la pestaña «Seguimiento de correos» se comportan como en un envío real.

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
   y copiar las 16 letras. alainperez@uapa.edu.do => dwpn vhng yuma ffal
3. Pegarla en el tablero junto al correo del remitente (con o sin espacios).

La contraseña se guarda cifrada y no vuelve al navegador: el campo aparece vacío, y solo se
cambia si se escribe otra. Gmail admite unos **500 correos al día por cuenta**; con más
remitentes se puede subir el máximo diario.

## 3. El tablero (botón «Configurar envío de correos» del editor)

| Campo | Qué es |
|---|---|
| **Destinatarios** | Un CSV con un correo por fila (de cada fila se toma la primera celda que sea un correo; la primera fila puede ser el encabezado). Los nuevos se agregan; los que ya estaban conservan su estado. «Vaciar lista» borra también quién ya fue notificado y desactiva el envío. |
| **Remitentes** | Correo y contraseña de aplicación (§2). |
| **Correos por remitente al día** | Cuántos correos envía cada remitente cada día (máximo 500, el límite de Gmail). El cupo del día es ese número por cada remitente: dos remitentes a 200 envían hasta 400 al día. |
| **Frecuencia** | Todos los días, o los días de la semana que se marquen. |
| **Hora** | Hora a la que arranca el envío, en `CAMPAIGN_TIME_ZONE`. |
| **Asunto y cuerpo** | El asunto en texto plano y el cuerpo con un editor enriquecido (negrita, cursiva, listas, enlaces e imágenes). |
| **Enviar prueba** | Guarda lo escrito y envía el mensaje a una dirección, con el primer remitente. |
| **Activar el envío** | Exige asunto, cuerpo, al menos un remitente y destinatarios. Suma la pestaña «Seguimiento de correos» a la vista compartida. |

El correo se envía en **HTML** y, además, con una versión en texto plano para los clientes
que no muestran HTML (se genera quitando las etiquetas). Las **imágenes van incrustadas en
base64** dentro del propio mensaje: no hace falta alojarlas, pero engordan el correo (el tope
por imagen es 300 KB y `DATA_UPLOAD_MAX_MEMORY_SIZE` > 2.5 MB, ver §1; si el mensaje entero
supera ese tope, al guardar avisa de que es demasiado grande). Gmail las muestra;
algunos clientes de escritorio (Outlook) pueden no hacerlo. Al guardar, el servidor **sanea**
el HTML: solo se conservan las etiquetas del editor (script, estilos y atributos peligrosos
se eliminan) y los enlaces se abren en otra pestaña.

## 4. Cómo se envía

A la hora indicada arranca una ejecución que va enviando los **pendientes de uno en uno**,
espaciados **1 segundo** (fijo en el código: `SEND_INTERVAL_S` en `services/mailing.py`),
repartidos en ronda entre los remitentes, hasta llegar al **cupo de cada remitente** o
quedarse sin pendientes. El paso uniforme evita las ráfagas que activan las defensas
anti-spam de Gmail. Los pendientes que no entraron siguen el próximo día de envío.

**Ejemplo:** 1 200 destinatarios, un remitente con el tope en 500 al día. A ~1 correo por
segundo, el envío del día (~500 correos) tarda unos 8–9 minutos; termina al tercer día de
envío (500 + 500 + 200). Más remitentes suben el **cupo diario** (hasta 500 por cuenta), no
la velocidad: los correos salen en serie. Si se ponen dos remitentes a 200, el cupo del día
es 400.

Lo que pasa cuando algo falla:

- **Un destinatario falla** (dirección rechazada): queda pendiente y se reintenta el
  próximo día de envío; al tercer intento queda como «Falló».
- **Un remitente falla** (contraseña mal, cuenta bloqueada, tope de Gmail): no se usa más
  ese día y los demás siguen con sus destinatarios. Si no queda ninguno, el envío del día
  termina. Tras corregirlo, **Guardar** en el tablero reabre el día y el envío se retoma en
  la siguiente pasada del cron.
- **El proceso se corta** (reinicio del servidor): la siguiente pasada del cron lo retoma
  pasados 10 minutos, sin repetir a nadie ni pasar del cupo.

## 5. Seguimiento

Con el envío activado, la vista compartida del tablero tiene la pestaña **«Seguimiento de correos»**, que se
actualiza sola cada 10 segundos:

| Pieza | Qué responde |
|---|---|
| **Franja de estado** | ¿En qué está el envío? «Enviando ahora» (con cuántos van de hoy), «Próximo envío», «El envío de hoy terminó antes de llegar al máximo» (con el último error) o «Envío completado». |
| **Notificados** | ¿Cuánto falta? Total notificado, barra con notificados, pendientes y fallidos, y la fecha aproximada en que termina. |
| **Enviados por día** | ¿Va al ritmo esperado? Una barra por día y la línea de la capacidad diaria (tope por remitente × remitentes); una barra corta es un día que no llegó al tope. |
| **Remitentes** | ¿Qué cuenta falla? Por cuenta: enviados hoy, en total, errores y el último error. |
| **Errores por motivo** | ¿Por qué falla? Los errores agrupados y contados: distingue direcciones que no existen de un problema de la cuenta. |
| **Destinatarios** | ¿Qué pasó con una persona? Estado, fecha, remitente y error de cada correo. |
| **Registro de envíos** | El detalle cronológico (inicio, cada error, resumen). Está plegado: se despliega al hacer clic. |

«Terminó antes de llegar al máximo» significa que quedaron pendientes sin enviar hoy: o
ningún remitente pudo enviar (revisar «Remitentes»), o los que quedan fallaron y se
reintentan el próximo día de envío.

En el admin de Django están los mismos datos (`Envío de correoss`, `Destinatarios`, `Logs de
correos`).

## 6. Backend (`services/mailing.py`, `views_email.py`)

### 6.1 Modelos

| Modelo | Qué guarda |
|---|---|
| `EmailCampaign` | Uno por tablero (`dashboard.email_campaign`): `enabled`, `subject`, `body` (el mensaje, en HTML saneado), `per_sender_limit` (correos al día por remitente, máximo 500), `frequency` (`daily` \| `weekly`), `send_time`, `weekdays` (0 = lunes … 6 = domingo, solo en `weekly`), `last_run_on` (día local cerrado: ese día ya no se envía más) y `running_since` (hay un envío en curso). |
| `EmailSender` | Cuenta de Gmail que envía: `email` y `password_encrypted` (su contraseña de aplicación, cifrada). |
| `EmailRecipient` | Destinatario: `email`, `status` (`pending` \| `sent` \| `failed`), `attempts`, `sent_at`, `sender_email` y `error`. |
| `EmailLog` | Bitácora: `created_at`, `level` (`info` \| `error`), `message`, `recipient_email`, `sender_email`. |

### 6.2 Servicio (`services/mailing.py`)

- **Contraseñas:** `encrypt_password` / `decrypt_password` (Fernet con `EMAIL_ENCRYPTION_KEY`
  o, sin ella, una clave derivada de `SECRET_KEY`). Nunca se devuelven al navegador.
- **`parse_recipients_csv(content)`** → `(válidos, inválidos)`: de cada fila, la primera celda
  que es un correo; en minúsculas y sin repetir. Una primera fila sin correo es el encabezado.
- **`is_due(campaign, now)`**: activada, con pendientes, hoy es día de envío, ya pasó
  `send_time`, el día no está cerrado (`last_run_on`) y queda cupo (enviados hoy <
  `per_sender_limit` × remitentes). La hora y el día son de `CAMPAIGN_TIME_ZONE`. Un cron
  atrasado la ejecuta igual; `next_run` da la próxima fecha (ahora mismo si el envío de hoy
  quedó sin terminar).
- **`run_campaign(campaign, now, force)`** hace **el envío del día**:
  1. Toma el candado con un `update` condicionado sobre `running_since`: de dos crons
     solapados, solo uno sigue (el otro recibe `None`). El envío renueva el candado cada minuto
     mientras envía (así un envío largo no se lo roba otro cron); uno de más de `LOCK_STALE`
     (10 min) es de un proceso que murió y no bloquea. Con el día cerrado también devuelve
     `None`, salvo `force`.
  2. Calcula el cupo de hoy de cada remitente (`per_sender_limit` − lo que ya envió hoy) y
     reparte los pendientes en ronda entre los remitentes con contraseña, hasta agotar sus
     cupos. Cada remitente envía los suyos por una conexión SMTP
     (`CAMPAIGN_SMTP_HOST`/`PORT`, TLS).
  3. Espacia los correos `SEND_INTERVAL_S` (1 s, fijo en el código) y renueva el candado a
     mitad de camino. Sigue hasta agotar los cupos, quedarse sin pendientes o sin remitentes,
     o cambiar el día (los que sobran siguen el próximo día de envío).
  4. Un destinatario que falla suma un intento y sigue pendiente, pero no se reintenta en
     la misma ejecución sino el próximo día de envío; al tercero (`MAX_ATTEMPTS`) queda
     `failed`. Si falla el remitente (no inicia sesión, se desconecta, el servidor lo
     rechaza), no se usa más en esa ejecución y sus destinatarios quedan pendientes, sin
     sumar intentos, para los demás remitentes.
  5. Al terminar cierra el día (`last_run_on`). Si el proceso se corta a medias el día
     queda abierto: la siguiente pasada del cron retoma cuando el candado vence.
  6. Deja en `EmailLog` el inicio, cada error y el resumen.
- **Mensaje:** se envía en HTML (`EmailMultiAlternatives`) con su alternativa en texto plano
  (`html_to_text`: quita las etiquetas y respeta los saltos de bloque). El HTML del cuerpo se
  guarda saneado con `nh3` (allow-list en `views_email.BODY_TAGS`): sin `script`, sin atributos
  de eventos y con los enlaces abriéndose en otra pestaña; se permite el `src` en `data:` para
  las imágenes incrustadas en base64.
- **`send_test(campaign, to)`**: el mensaje a una dirección, con el primer remitente.
- **`report_summary(campaign)`** (lo que se consulta cada pocos segundos): `state`
  (`running`: hay un envío en curso · `done`: sin pendientes · `stopped`: el día se cerró
  antes del cupo y quedan pendientes · `scheduled`: espera su próxima ejecución), totales
  por estado, `sent_today`, `per_sender_limit`, `daily_capacity` (tope por remitente ×
  remitentes), `last_log_id`, `next_run`, `estimated_end` (el día en que se acaban los
  pendientes al ritmo de la capacidad diaria) y `last_error` (el último error de hoy).
- **`report_stats(campaign)`** (se pide cuando hay novedades): `per_sender_limit`,
  `daily_capacity`, `by_day` (enviados por día local, últimos `STATS_DAYS`), `by_sender` (por
  remitente: enviados hoy y en total, errores y último error; incluye los configurados sin
  envíos y los quitados que enviaron) y `errors` (los `STATS_ERRORS` motivos más frecuentes,
  juntando los intentos del mismo).

### 6.3 Comando y crontab

`manage.py send_campaign_emails` recorre los envíos activados y ejecuta los que `is_due`.
El cron solo dispara el arranque: el ritmo de los envíos lo lleva `run_campaign`, así que el
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
- **Programación:** correos por remitente al día, frecuencia (todos los días o días de la
  semana) y hora. Muestra el próximo envío que calcula el servidor.
- **Mensaje:** asunto y cuerpo, este último con un editor enriquecido (**Quill**, por CDN). La
  barra permite negrita/cursiva, encabezados, listas, cita, enlaces e imágenes (se incrustan en
  base64, hasta 300 KB cada una). «Enviar prueba» guarda lo escrito y manda el mensaje a la
  dirección indicada.
- **Activar el envío:** el servidor lo rechaza si falta el mensaje, un remitente o los
  destinatarios.

**Reporte (vista compartida).** Con el envío activado, `board_view.html` trae las pestañas
«Tablero» y «Seguimiento de correos» y carga `email-report.js`. La pestaña activa va en el hash
(`#correos`). Al volver a «Tablero» se despacha `resize` para que los gráficos tomen su
ancho. La pestaña «Seguimiento de correos» tiene:

- la franja de estado (`state` del reporte): enviando ahora, próximo envío, terminó antes
  del cupo (con el último error) o completado;
- «Notificados»: el total, una barra de avance (notificados, pendientes, fallidos) y la
  fecha estimada de fin;
- «Enviados por día»: barras (ApexCharts) con la línea de la capacidad diaria;
- «Remitentes» y «Errores por motivo»: tablas cortas (Tabulator, datos locales);
- la tabla de destinatarios, con paginación remota;
- el registro de envíos, plegado (`<details>`): su tabla se crea al desplegarlo.

**Aviso de novedades.** No hay websocket: mientras la pestaña «Seguimiento de correos» está a la vista, la
página pide cada 10 s `GET /email-report/` (unos conteos). Si cambian los totales o
`last_log_id`, actualiza la franja y el avance, pide `GET /email-report/stats/` (gráfico,
remitentes, errores) y vuelve a pedir las tablas paginadas. Con el documento oculto no
consulta, y al volver consulta de inmediato.
