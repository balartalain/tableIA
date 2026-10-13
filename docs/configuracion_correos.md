# Configuración del envío de correos

Cada tablero puede enviar un mensaje a una lista de destinatarios. Cada destinatario lo
recibe **una sola vez**; el envío se reparte en lotes y, si hace falta, en varios días. El
detalle técnico está en [`arquitectura_backend.md`](arquitectura_backend.md) §11 y
[`arquitectura_frontend.md`](arquitectura_frontend.md) («Envío de correos»).

## 1. Servidor

### Variables de entorno (`.env`)

| Variable | Por defecto | Para qué |
|---|---|---|
| `CAMPAIGN_TIME_ZONE` | `America/Santo_Domingo` | Zona horaria de la hora de envío que se elige en el tablero. |
| `EMAIL_ENCRYPTION_KEY` | vacía | Clave con la que se cifran las contraseñas de los remitentes. Vacía, se deriva de `SECRET_KEY` (cambiar `SECRET_KEY` obligaría a escribirlas de nuevo). |
| `CAMPAIGN_SMTP_HOST` | `smtp.gmail.com` | Servidor SMTP de los remitentes. |
| `CAMPAIGN_SMTP_PORT` | `587` | Puerto SMTP (TLS). |
| `CAMPAIGN_EMAIL_BACKEND` | SMTP de Django | Para probar sin enviar: `django.core.mail.backends.console.EmailBackend`. |

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

Para probar a mano:

```
venv/bin/python manage.py send_campaign_emails --campaign <id> --force
```

`--campaign` limita a un envío (el id de `EmailCampaign`, visible en el admin) y `--force`
ignora la hora y el día. El máximo de correos al día se respeta igual.

## 2. Remitentes (cuentas de Gmail)

Cada remitente es una cuenta de Gmail con una **contraseña de aplicación**, no la contraseña
del correo:

1. En la cuenta de Google, activar la verificación en dos pasos.
2. Entrar a <https://myaccount.google.com/apppasswords>, crear una contraseña de aplicación
   y copiar las 16 letras.
3. Pegarla en el tablero junto al correo del remitente (con o sin espacios).

La contraseña se guarda cifrada y no vuelve al navegador: el campo aparece vacío, y solo se
cambia si se escribe otra. Gmail admite unos **500 correos al día por cuenta**; con más
remitentes se puede subir el máximo diario.

## 3. El tablero (botón «Correos» del editor)

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
| **Activar el envío** | Exige asunto, cuerpo, al menos un remitente y destinatarios. Suma la pestaña «Correos» a la vista compartida. |

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

Con el envío activado, la vista compartida del tablero tiene la pestaña **«Correos»**:
notificados, pendientes, con error y próximo envío, la tabla de destinatarios y el registro
de envíos (inicio, cada lote, cada error y el resumen). Se actualiza sola cada 10 segundos.
En el admin de Django están los mismos datos (`Envíos de correos`, `Destinatarios`, `Logs de
correos`).
