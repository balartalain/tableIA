# Instalación en producción

La app corre con **Gunicorn detrás de Apache** (HTTPS en Apache, Gunicorn escuchando en
`127.0.0.1`), con **PostgreSQL** y un **crontab** para el envío de correos. `settings.py` ya
viene preparado para eso (`USE_X_FORWARDED_HOST`, `SECURE_PROXY_SSL_HEADER`, `REPORT_PATH`).

En los ejemplos la app vive en `/srv/TableIA`, corre con el usuario `tableia`, se sirve en
`https://reportes.uapa.edu.do` bajo el prefijo `/sheets-reports` y Gunicorn escucha en el
puerto `8001`. Cambiar esos valores en todos los pasos si son otros.

## 1. Paquetes del servidor

Python 3.12, PostgreSQL y Apache con los módulos de proxy, cabeceras y SSL:

```bash
sudo apt install python3.12 python3.12-venv postgresql apache2 git
sudo a2enmod proxy proxy_http headers ssl
sudo useradd --system --create-home --shell /bin/bash tableia
```

## 2. Código y entorno virtual

```bash
sudo mkdir -p /srv/TableIA && sudo chown tableia: /srv/TableIA
sudo -u tableia -i
git clone <url-del-repo> /srv/TableIA
cd /srv/TableIA
python3.12 -m venv venv
venv/bin/pip install -r requirements-prod.txt   # requirements.txt + gunicorn; requirements-dev.txt es solo para tests
```

## 3. Base de datos

```bash
sudo -u postgres psql -c "CREATE USER tableia WITH PASSWORD '<password>';"
sudo -u postgres psql -c "CREATE DATABASE tableia OWNER tableia;"
```

## 4. Variables de entorno (`.env`)

```bash
cp .env.example .env
chmod 600 .env
```

Completar al menos (el detalle de cada variable está en `.env.example`):

```env
SECRET_KEY=<nueva>
DEBUG=False
ALLOWED_HOSTS=reportes.uapa.edu.do
REPORT_PATH=/sheets-reports
DATABASE_URL=postgres://tableia:<password>@localhost:5432/tableia

GOOGLE_OAUTH_CLIENT_ID=…
GOOGLE_OAUTH_CLIENT_SECRET=…
GOOGLE_OAUTH_REDIRECT_URI=https://reportes.uapa.edu.do/sheets-reports/auth/google/callback/
GOOGLE_PICKER_API_KEY=…
GOOGLE_PICKER_APP_ID=…
GOOGLE_TOKEN_ENCRYPTION_KEY=<nueva>

GEMINI_API_KEY=…
AI_AUDIT_LOG_FILE=/srv/TableIA/ai_audit.log

EMAIL_ENCRYPTION_KEY=<nueva>
```

Generar las claves:

```bash
venv/bin/python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"   # SECRET_KEY
venv/bin/python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"                     # las dos *_ENCRYPTION_KEY
```

> Guardar `SECRET_KEY` y las `*_ENCRYPTION_KEY` en un lugar seguro. Si cambian, las cuentas de
> Google conectadas deben reconectarse y las contraseñas de los remitentes escribirse de nuevo.

## 5. Google Cloud

En el cliente OAuth agregar el origen `https://reportes.uapa.edu.do` y el redirect URI
`https://reportes.uapa.edu.do/sheets-reports/auth/google/callback/` (con el prefijo), y
restringir la API key del Picker al dominio. Pasos completos en
[`google_auth.md` §4](google_auth.md).

## 6. Preparar Django

```bash
venv/bin/python manage.py migrate
venv/bin/python manage.py collectstatic --noinput
venv/bin/python manage.py createsuperuser      # para entrar a /admin/
venv/bin/python manage.py check --deploy
```

Para llevar los datos del SQLite de desarrollo, en la máquina de desarrollo:

```bash
venv/bin/python manage.py dumpdata --natural-foreign --natural-primary \
    -e contenttypes -e auth.permission -e sessions --indent 2 > dump.json
```

y en el servidor, después del `migrate`: `venv/bin/python manage.py loaddata dump.json`.

## 7. Gunicorn como servicio

`/etc/systemd/system/tableia.service`:

```ini
[Unit]
Description=UAPA DataViz AI (Gunicorn)
After=network.target postgresql.service

[Service]
User=tableia
Group=tableia
WorkingDirectory=/srv/TableIA
ExecStart=/srv/TableIA/venv/bin/gunicorn config.wsgi:application \
    --bind 127.0.0.1:8001 --workers 3 --timeout 120 \
    --access-logfile - --error-logfile -
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

El `--timeout 120` da margen a las llamadas a Gemini, que pueden tardar.

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now tableia
sudo systemctl status tableia
```

## 8. Apache

Dentro del `<VirtualHost *:443>` del dominio. Esta es la configuración que funciona en
`https://gtsst.uapa.edu.do/tableria` (código en `/home/moisesm/tableIA`, Gunicorn en el puerto
`8014`, `REPORT_PATH=/tableria`); en otro servidor, cambiar el prefijo, la ruta y el puerto:

```apache
# /tableria sin barra final se redirige a /tableria/. El ProxyPassMatch va antes que
# cualquier otro ProxyPass del vhost (p. ej. uno para "/"): ProxyPass gana a RedirectMatch.
ProxyPassMatch ^/tableria$ !
RedirectMatch 301 ^/tableria$ /tableria/

# Los estáticos los sirve Apache directamente (los reúne collectstatic).
Alias /tableria/static/ /home/moisesm/tableIA/staticfiles/
<Directory /home/moisesm/tableIA/staticfiles>
    Options Indexes FollowSymLinks
    AllowOverride None
    Require all granted
</Directory>

# Evitar que el proxy interfiera con las solicitudes de estáticos.
ProxyPass /tableria/static/ !

# Proxy inverso a Gunicorn (puerto 8014).
ProxyPreserveHost On
RequestHeader set X-Forwarded-Proto "https"
ProxyPass /tableria/ http://127.0.0.1:8014/
ProxyPassReverse /tableria/ http://127.0.0.1:8014/

# Los mensajes de correo llevan imágenes en base64 (ver DATA_UPLOAD_MAX_MEMORY_SIZE).
LimitRequestBody 10485760
```

El proxy quita el prefijo y Django lo vuelve a anteponer en las URLs (`FORCE_SCRIPT_NAME`, a
partir de `REPORT_PATH`). Sin prefijo, usar `/` en lugar de `/tableria/` y dejar
`REPORT_PATH` vacío. El puerto debe ser el mismo del `--bind` de Gunicorn (§7).

> El destino del `ProxyPass` va **sin** el prefijo (`http://127.0.0.1:8014/`, no
> `…:8014/tableria`), y Gunicorn no lleva `SCRIPT_NAME`. Si el prefijo llega hasta Django,
> se suma al de `REPORT_PATH` y las URLs salen como `/tableria/tableria/…`.

> Si los estáticos dan **403**, revisar que la ruta del `Alias` y del `<Directory>` sea la carpeta
> real (distingue mayúsculas: `tableIA`, no `tableria`) y que Apache pueda atravesar cada carpeta
> del camino (`namei -l <ruta>/staticfiles`; si falta, `chmod o+x /home/moisesm`).

```bash
sudo apachectl configtest && sudo systemctl reload apache2
```

## 9. Crontab

Con `crontab -e` del usuario `tableia`:

```
*/5 * * * * cd /srv/TableIA && venv/bin/python manage.py send_campaign_emails
0 0 * * * cd /srv/TableIA && venv/bin/python manage.py sync_gemini_files
```

- `send_campaign_emails`: el envío de correos. El intervalo debe coincidir con
  `CAMPAIGN_CRON_MINUTES`. Detalle en [`configuracion_correos.md` §1](configuracion_correos.md).
- `sync_gemini_files`: a las 12:00 AM resube a Gemini las fuentes que usan el chat de datos, para
  que su archivo no expire y el chat no tenga que subirlo mientras el usuario espera.

El cron usa la hora del sistema, no la `TIME_ZONE` de Django. Con el servidor en
`America/Santo_Domingo` (`timedatectl`), `0 0` es la medianoche local; si está en UTC, la
medianoche de Santo Domingo (UTC−4) es `0 4 * * *`.

Cada corrida queda en el admin, en «Tareas en segundo plano».

## 10. Comprobar

- `https://reportes.uapa.edu.do/sheets-reports/login/` abre y los estilos cargan (sin 404 de
  `/static/` en la consola del navegador).
- Entrar con Google, crear un tablero y elegir una hoja con el Picker.
- Generar un widget con IA; queda registrado en `ai_audit.log`.
- Probar un envío: `venv/bin/python manage.py send_campaign_emails --dashboard <id> --force`.
- Probar la subida a Gemini: `venv/bin/python manage.py sync_gemini_files --source <id>`.
- Logs de la app: `journalctl -u tableia -f`.

## Actualizar a una nueva versión

```bash
sudo -u tableia -i
cd /srv/TableIA
git pull
venv/bin/pip install -r requirements-prod.txt
venv/bin/python manage.py migrate
venv/bin/python manage.py collectstatic --noinput
exit
sudo systemctl restart tableia
```
