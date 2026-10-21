# Google en UAPA DataViz AI: implementación y credenciales

Documenta la app **`google_auth`**: qué se implementó, cómo encaja con el resto del proyecto y,
paso a paso, cómo darle las credenciales de OAuth y la API key para el Google Picker.

> Guía corta de configuración: [`docs/configuracion_google.md`](configuracion_google.md).

---

## 1. Qué se implementó

Antes, el selector de fuente solo podía listar las hojas de cálculo **compartidas con la cuenta de
servicio** de Google (`GOOGLE_SHEETS_CREDENTIALS_PATH`).

Ahora, además, el usuario puede **conectar su propia cuenta de Google** y elegir la hoja
directamente en su Drive con el **selector nativo de Google (Google Picker)**. Si no se configura
nada, todo sigue funcionando como antes (cuenta de servicio).

La lógica de Google vive en una app Django propia (`google_auth`) para que quede todo el backend
en un solo sitio; el frontend (el selector de fuente) se queda donde estaba, en `sheets_reports`.

### Flujo

```
Editor (selector de fuente)
  │  click «Conectar con Google»
  ▼
GET /auth/google/login/?next=/tableros/nuevo/
  │  redirección al consentimiento de Google (access_type=offline → refresh_token)
  ▼
Google → GET /auth/google/callback/?code=…&state=…
  │  valida el state, cambia el code por tokens y los guarda CIFRADOS
  ▼
redirige a  /tableros/nuevo/?google=connected
  │  el frontend detecta ?google=connected y abre el Google Picker solo
  ▼
El usuario elige una hoja de su Drive  →  fileId
  │  (flujo de siempre) pestañas → columnas → guardar la fuente
  ▼
sheets_reports lee la hoja con el token del usuario
```

Al leer cualquier hoja, primero se intenta con el **token del usuario conectado** y, si falla, con
el de la **cuenta de servicio**. Así los tableros cuyos datos ya estaban compartidos con la cuenta
de servicio no se rompen.

### Arquitectura en una frase

La app pide los tokens una vez (OAuth) y los guarda cifrados; un **middleware** deja la petición en
curso en un `ContextVar` para que `sheets_reports.services.sheets` / `google_drive` usen la
credencial del usuario **sin cambiar sus firmas ni el resto del código**.

---

## 2. Archivos de la app `google_auth`

| Archivo | Responsabilidad |
|---|---|
| `google_auth/models.py` | `GoogleCredential` (OneToOne con el usuario): `email`, tokens **cifrados**, `token_uri`, `scopes`, `expiry` y `to_credentials()` (reconstruye las credenciales de `google.oauth2`). |
| `google_auth/crypto.py` | Cifrado Fernet de los tokens (`GOOGLE_TOKEN_ENCRYPTION_KEY` o derivada de `SECRET_KEY`). |
| `google_auth/services.py` | Todo el motor: `is_configured`, `authorization_url`, `exchange_code`, `save_credentials`, `user_credentials` (renueva el token si venció), `active_credentials` / `active_access_token`, `disconnect`, `template_context`. |
| `google_auth/middleware.py` | `GoogleAuthMiddleware`: pone la petición en curso en un `ContextVar`. |
| `google_auth/views.py` | Endpoints `login`, `callback`, `status`, `disconnect`. |
| `google_auth/urls.py` | Rutas bajo `/auth/google/` (namespace `google_auth`). |
| `google_auth/admin.py` | Ver/borrar conexiones desde el admin. |
| `google_auth/migrations/0001_initial.py` | Tabla `GoogleCredential`. |
| `google_auth/tests/test_google_auth.py` | Configuración, cifrado/renovación, orden de tokens y endpoints. |

### Cambios en el resto del proyecto

| Archivo | Cambio |
|---|---|
| `config/settings.py` | `google_auth` en `INSTALLED_APPS`; `GoogleAuthMiddleware` tras `AuthenticationMiddleware`; ajustes `GOOGLE_*`; `OAUTHLIB_INSECURE_TRANSPORT=1` en `DEBUG`; `OAUTHLIB_RELAX_TOKEN_SCOPE=1`. |
| `config/urls.py` | `path('auth/google/', include('google_auth.urls'))`. |
| `sheets_reports/services/sheets.py` | `_read_attempts()`: token del usuario → token de la cuenta de servicio; `_request_csv()`. |
| `sheets_reports/services/google_drive.py` | `_preferred_credentials()` (usuario o cuenta de servicio); `tab_status` sigue usando la cuenta de servicio. |
| `sheets_reports/views.py` | `board_editor` / `board_new` pasan `google_oauth` al template; `_get_user` delega en `google_auth.services.current_app_user`. |
| `sheets_reports/templates/board_editor.html` | Expone `window.TABLEIA_GOOGLE` con `json_script`. |
| `sheets_reports/templates/sheets_reports/source_picker.html` | Botón «Conectar con Google» / «Elegir en mi Drive», correo conectado, «Desconectar». |
| `sheets_reports/static/.../source-picker.js` | `openGooglePicker()` (carga perezosa del SDK y abre el Picker), `onGooglePicked()`, `disconnectGoogle()`, `init()` (auto-abre al volver del consentimiento). |
| `.env.example` | Variables nuevas documentadas. |

---

## 3. Endpoints

| Método | Ruta | Qué hace |
|---|---|---|
| GET | `/auth/google/login/?next=…` | Redirige al consentimiento de Google. Guarda `state` y `next` en la sesión. |
| GET | `/auth/google/callback/` | Valida `state`, cambia el `code` por tokens, los guarda y vuelve a `next?google=connected`. |
| GET | `/auth/google/status/` | JSON: `{configured, connected, email, access_token}`. El `access_token` lo usa el Picker en el navegador. |
| POST | `/auth/google/disconnect/` | Borra los tokens guardados. |

---

## 4. Cómo agregar las credenciales y la API key

Los pasos se hacen una vez en **Google Cloud Console** y luego se copian los valores al `.env`.

### 4.1 Crear el proyecto

1. Entra en <https://console.cloud.google.com/> y **crea un proyecto** (o elige uno existente).
2. Anota su **número de proyecto** (campo *Project number*, no el *Project ID*): es el
   `GOOGLE_PICKER_APP_ID`.

### 4.2 Habilitar las APIs

En **APIs y servicios → Biblioteca**, habilita las tres:

- **Google Drive API**
- **Google Sheets API**
- **Google Picker API**

### 4.3 Pantalla de consentimiento (OAuth consent screen)

En **APIs y servicios → Pantalla de consentimiento de OAuth**:

1. Tipo de usuario: **Externo** (o **Interno** si el dominio es de Google Workspace).
2. Completa nombre de la app, correo de soporte y correo del desarrollador.
3. **Scopes** → agrega:
   - `.../auth/userinfo.email`
   - `.../auth/userinfo.profile`
   - `.../auth/drive.readonly`
   - `.../auth/spreadsheets.readonly`
4. **Usuarios de prueba**: mientras la app esté en modo **Prueba**, agrega el correo de cada
   persona que vaya a conectar su cuenta (incluido el tuyo). Así funciona sin verificación de
   Google.

### 4.4 Cliente OAuth (Client ID y Secret)

En **APIs y servicios → Credenciales → Crear credenciales → ID de cliente de OAuth**, tipo
**Aplicación web**:

- **Orígenes de JavaScript autorizados**: el origen de la app, sin barra final.
  - Producción: `https://reportes.uapa.edu.do`
  - Local: `http://localhost:8000`
- **URIs de redireccionamiento autorizados**: debe terminar en `/auth/google/callback/`.
  - Producción: `https://reportes.uapa.edu.do/auth/google/callback/`
  - Local: `http://localhost:8000/auth/google/callback/`

Guarda el **Client ID** y el **Client secret**.

> Si la app corre bajo un prefijo (p. ej. `/sheets-reports`), inclúyelo:
> `https://dominio/sheets-reports/auth/google/callback/`.

### 4.5 API key (para el Google Picker)

En **APIs y servicios → Credenciales → Crear credenciales → Clave de API**:

1. Copia la clave → `GOOGLE_PICKER_API_KEY`.
2. (Recomendado) **Restringir la clave**:
   - *Restricciones de aplicación*: **Sitios web** → agrega el origen de la app
     (`https://reportes.uapa.edu.do/*` y, para local, `http://localhost:8000/*`).
   - *Restricciones de API*: **Google Picker API** (y *Drive API* si te lo pide).

### 4.6 Llenar el `.env`

Copia `.env.example` a `.env` si aún no lo tienes y completa:

```env
GOOGLE_OAUTH_CLIENT_ID=1234567890-xxxxxxxx.apps.googleusercontent.com
GOOGLE_OAUTH_CLIENT_SECRET=GOCSPX-xxxxxxxxxxxxxxxx
GOOGLE_OAUTH_REDIRECT_URI=https://reportes.uapa.edu.do/auth/google/callback/
GOOGLE_PICKER_API_KEY=AIzaSyXXXXXXXXXXXXXXXXXXXXXXXXXXX
GOOGLE_PICKER_APP_ID=1234567890
GOOGLE_TOKEN_ENCRYPTION_KEY=
```

| Variable | De dónde sale | Obligatoria |
|---|---|---|
| `GOOGLE_OAUTH_CLIENT_ID` | Cliente OAuth (4.4) | Sí (sin ella no hay login) |
| `GOOGLE_OAUTH_CLIENT_SECRET` | Cliente OAuth (4.4) | Sí |
| `GOOGLE_OAUTH_REDIRECT_URI` | Debe coincidir con el redirect URI del cliente (4.4). Vacío = se arma con el dominio de cada petición | No |
| `GOOGLE_PICKER_API_KEY` | Clave de API (4.5) | Sí, para el Picker |
| `GOOGLE_PICKER_APP_ID` | *Project number* (4.1) | Sí, para el Picker |
| `GOOGLE_TOKEN_ENCRYPTION_KEY` | Clave Fernet propia; vacía se deriva de `SECRET_KEY` | No |

Para generar una `GOOGLE_TOKEN_ENCRYPTION_KEY` propia:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

> Si la cambias (o cambias `SECRET_KEY`, de la que se deriva), las cuentas conectadas deberán
> volver a conectarse.

### 4.7 Aplicar y probar

```bash
source venv/bin/activate
python manage.py migrate      # crea la tabla google_auth_googlecredential
python manage.py runserver
```

Abre el editor, **Nuevo tablero** → el panel «Conectar datos» mostrará **«Conectar con Google»**.
Tras autorizar, el Picker se abre solo y eliges la hoja de tu Drive.

---

## 5. Pruebas locales

Con `DEBUG=True` la app activa `OAUTHLIB_INSECURE_TRANSPORT=1` para permitir `http://localhost`.

En el cliente OAuth deben existir, además de los de producción:

- *Origen de JavaScript*: `http://localhost:8000`
- *URI de redireccionamiento*: `http://localhost:8000/auth/google/callback/`

Si usas otro puerto, cámbialo en ambos sitios y en `GOOGLE_OAUTH_REDIRECT_URI` (o déjalo vacío
para que se arme con el dominio de la petición).

---

## 6. Problemas frecuentes

| Síntoma | Causa probable | Solución |
|---|---|---|
| `redirect_uri_mismatch` | El redirect URI del cliente no coincide exactamente | Corrige el URI en Google Cloud o pon el mismo en `GOOGLE_OAUTH_REDIRECT_URI` (mismo esquema, dominio, puerto y barra final). |
| `origin_mismatch` al abrir el Picker | Falta el *JavaScript origin* | Agrega el origen exacto (`esquema://dominio:puerto`) en el cliente OAuth. |
| `The API developer key is invalid` | La API key está mal, restringida a otro dominio o no tiene la Picker API | Revisa `GOOGLE_PICKER_API_KEY` y sus restricciones. |
| `access_denied` / no deja continuar | La cuenta no es *usuario de prueba* y la app está en modo Prueba | Agrega el correo en la pantalla de consentimiento. |
| El botón «Conectar con Google» no aparece | Falta `GOOGLE_OAUTH_CLIENT_ID` / `SECRET` | Complétalos en `.env` y reinicia el servidor. |
| Una hoja que antes se leía ahora falla | Se está usando el token del usuario y no tiene acceso | La app reintenta con la cuenta de servicio; si tampoco, comparte la hoja con el email de la cuenta de servicio. |
| Los tokens no se pueden leer tras reiniciar | Cambió `SECRET_KEY` o `GOOGLE_TOKEN_ENCRYPTION_KEY` | Vuelve a conectar la cuenta. |

---

## 7. Pruebas automáticas

```bash
source venv/bin/activate
python manage.py test google_auth
```

Cubren: configuración, `authorization_url`, cifrado y recuperación de tokens, orden de tokens al
leer una hoja, y los endpoints `login`/`callback`/`status`/`disconnect` (con Google simulado).
