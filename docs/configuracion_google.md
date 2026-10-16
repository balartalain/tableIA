# Conectar Google (login del usuario y Google Picker)

> Documentación completa (implementación + credenciales): [`docs/google_auth.md`](google_auth.md).

La app `google_auth` permite que cada usuario **conecte su cuenta de Google** y elija la hoja de
cálculo directamente en su Drive con el **selector nativo de Google** (Google Picker), en vez de
tener que compartirla con la cuenta de servicio.

Sin configurar esto, el selector sigue funcionando como antes: lista las hojas compartidas con la
cuenta de servicio (`GOOGLE_SHEETS_CREDENTIALS_PATH`).

## Cómo funciona

- `GET /auth/google/login/?next=…` → redirige al consentimiento de Google (Authorization Code con
  `access_type=offline`, para recibir un `refresh_token`).
- `GET /auth/google/callback/` → guarda los tokens **cifrados** en `GoogleCredential` y vuelve a
  `next` agregando `?google=connected`: el editor detecta ese parámetro y **abre el Google Picker
  solo**.
- `GET /auth/google/status/` → JSON con `configured`, `connected`, `email` y un `access_token`
  vigente que el frontend usa para abrir el Picker.
- `POST /auth/google/disconnect/` → olvida los tokens guardados.

Al leer una hoja (`sheets_reports.services.sheets`) se intenta primero con el token del usuario
conectado y, si falla, con el de la cuenta de servicio: las hojas que ya estaban compartidas con
la cuenta de servicio siguen funcionando.

## Pasos en Google Cloud Console

1. Crea (o elige) un proyecto.
2. **APIs y servicios → Biblioteca**: habilita *Google Drive API*, *Google Sheets API* y
   *Google Picker API*.
3. **Pantalla de consentimiento de OAuth**: tipo «Externo» (o «Interno» si es un Workspace).
   Agrega los *scopes*:
   - `.../auth/userinfo.email`, `.../auth/userinfo.profile`
   - `.../auth/drive.readonly`
   - `.../auth/spreadsheets.readonly`

   Mientras la app no esté verificada, deja el modo **Prueba** y agrega como *usuario de prueba* a
   cada persona que vaya a conectar su cuenta.
4. **Credenciales → Crear credenciales → ID de cliente de OAuth**, tipo **Aplicación web**:
   - *Authorized JavaScript origins*: el dominio de la app (ej. `https://reportes.uapa.edu.do`).
   - *Authorized redirect URIs*: `https://reportes.uapa.edu.do/auth/google/callback/`.
5. **Credenciales → Crear credenciales → Clave de API** (para el Picker).

## Variables de entorno

```env
GOOGLE_OAUTH_CLIENT_ID=…apps.googleusercontent.com
GOOGLE_OAUTH_CLIENT_SECRET=…
# Debe coincidir con el redirect URI del cliente. Vacío: se arma con el dominio de la petición.
GOOGLE_OAUTH_REDIRECT_URI=https://reportes.uapa.edu.do/auth/google/callback/
GOOGLE_PICKER_API_KEY=…
GOOGLE_PICKER_APP_ID=1234567890        # el número de proyecto de Google Cloud
# Clave con la que se cifran los tokens guardados; vacía, se deriva de SECRET_KEY.
GOOGLE_TOKEN_ENCRYPTION_KEY=
```

> Los scopes `drive.readonly` y `spreadsheets.readonly` son sensibles/restringidos: con la app en
> modo **Prueba** funcionan para los usuarios de prueba sin verificación de Google. En producción
> con un dominio propio puede que Google pida verificación si se abre a usuarios externos.

## Desarrollo local

Con `DEBUG=True` la app pone `OAUTHLIB_INSECURE_TRANSPORT=1` para permitir `http://localhost`.
El *Authorized redirect URI* del cliente debe incluir `http://localhost:8000/auth/google/callback/`
y el *JavaScript origin* `http://localhost:8000`.
