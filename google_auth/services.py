"""Autenticación con Google (OAuth 2.0) para leer el Drive y las hojas del propio usuario.

Flujo: el usuario conecta su cuenta (Authorization Code con `access_type=offline`, para recibir
un `refresh_token`) y sus tokens quedan cifrados en `GoogleCredential`; el `access_token` se
renueva solo cuando vence. Mientras no haya conexión, `sheets_reports` sigue leyendo con la
cuenta de servicio (`GOOGLE_SHEETS_CREDENTIALS_PATH`).

La credencial del usuario en curso viaja por un `ContextVar` que fija el middleware
(`google_auth.middleware`), para que `sheets_reports.services.sheets` / `google_drive` la usen
sin cambiar sus firmas: leen el token del usuario si está conectado y, si no, el de la cuenta de
servicio. Sin petición (cron, comandos) el ContextVar está vacío.
"""
import base64
import contextvars
import json
import logging
from datetime import timezone as dt_timezone

from django.conf import settings
from django.urls import reverse

logger = logging.getLogger(__name__)

# Lo que pedimos: identificar la cuenta y poder leer su Drive y sus hojas. Lectura solamente.
SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/spreadsheets.readonly",
]

TOKEN_URI = "https://oauth2.googleapis.com/token"

# Petición en curso (la fija el middleware): de ahí sale el usuario cuya cuenta de Google se usa.
_current_request = contextvars.ContextVar("google_auth_request", default=None)


class GoogleAuthError(Exception):
    """Algo que el usuario (o quien configura) puede corregir."""


# ------------------------------------------------------------------ contexto de la petición
def set_current_request(request):
    return _current_request.set(request)


def reset_current_request(token):
    _current_request.reset(token)


def current_request():
    return _current_request.get()


def current_app_user(request):
    """El usuario de la petición o, sin sesión, el superusuario (misma regla que `_get_user` del
    editor): la app todavía no tiene login propio."""
    user = getattr(request, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        return user
    from django.contrib.auth import get_user_model

    User = get_user_model()
    return User.objects.filter(is_superuser=True).first() or User.objects.first()


# ------------------------------------------------------------------ configuración
def is_configured() -> bool:
    return bool(getattr(settings, "GOOGLE_OAUTH_CLIENT_ID", "")
                and getattr(settings, "GOOGLE_OAUTH_CLIENT_SECRET", ""))


def redirect_uri(request=None) -> str:
    configured = getattr(settings, "GOOGLE_OAUTH_REDIRECT_URI", "")
    if configured:
        return configured
    if request is not None:
        return request.build_absolute_uri(reverse("google_auth:callback"))
    return ""


def client_config(redirect_uri_value: str) -> dict:
    if not is_configured():
        raise GoogleAuthError(
            "Falta configurar el cliente OAuth de Google "
            "(GOOGLE_OAUTH_CLIENT_ID y GOOGLE_OAUTH_CLIENT_SECRET).")
    return {"web": {
        "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
        "client_secret": settings.GOOGLE_OAUTH_CLIENT_SECRET,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": TOKEN_URI,
        "redirect_uris": [redirect_uri_value],
    }}


def build_flow(redirect_uri_value: str, state: str | None = None):
    from google_auth_oauthlib.flow import Flow

    return Flow.from_client_config(
        client_config(redirect_uri_value), scopes=SCOPES,
        redirect_uri=redirect_uri_value, state=state)


# ------------------------------------------------------------------ flujo OAuth
def authorization_url(request) -> tuple[str, str]:
    """La URL de consentimiento de Google y el `state` que hay que recordar en la sesión."""
    flow = build_flow(redirect_uri(request))
    return flow.authorization_url(
        access_type="offline", include_granted_scopes="true", prompt="consent")


def exchange_code(request, code: str, state: str | None):
    """Cambia el `code` del callback por las credenciales del usuario."""
    flow = build_flow(redirect_uri(request), state=state)
    try:
        flow.fetch_token(code=code)
    except Exception as e:  # noqa: BLE001 - error de red o de Google: mensaje legible
        raise GoogleAuthError(f"No se pudo completar la conexión con Google: {e}") from e
    return flow.credentials


# ------------------------------------------------------------------ credenciales guardadas
def stored_credential(user):
    """La fila guardada de la cuenta conectada, o None."""
    from google_auth.models import GoogleCredential

    if user is None or not getattr(user, "pk", None):
        return None
    return GoogleCredential.objects.filter(user=user).first()


def credentials_email(credentials) -> str:
    """El correo de la cuenta: del `id_token` (un JWT sin verificar sirve para mostrarlo)."""
    id_token = getattr(credentials, "id_token", "") or ""
    if id_token:
        try:
            payload = id_token.split(".")[1]
            payload += "=" * (-len(payload) % 4)
            email = json.loads(base64.urlsafe_b64decode(payload)).get("email", "")
            if email:
                return email
        except Exception:  # noqa: BLE001 - un id_token raro no debe romper la conexión
            pass
    return fetch_user_email(credentials)


def fetch_user_email(credentials) -> str:
    """Consulta la API de userinfo de Google si el id_token no trajo el email."""
    token = getattr(credentials, "token", None)
    if not token:
        return ""
    try:
        import urllib.request
        req = urllib.request.Request(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {token}"}
        )
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode())
            return data.get("email", "")
    except Exception:  # noqa: BLE001
        return ""


def save_credentials(user, credentials, email: str = ""):
    from google_auth import crypto
    from google_auth.models import GoogleCredential

    credential, _ = GoogleCredential.objects.get_or_create(user=user)
    credential.access_token_encrypted = crypto.encrypt(getattr(credentials, "token", "") or "")
    refresh = getattr(credentials, "refresh_token", None)
    if refresh:
        credential.refresh_token_encrypted = crypto.encrypt(refresh)
    if getattr(credentials, "token_uri", ""):
        credential.token_uri = credentials.token_uri
    scopes = list(getattr(credentials, "scopes", None) or [])
    if scopes:
        credential.scopes = scopes
    expiry = getattr(credentials, "expiry", None)
    if expiry is not None and expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=dt_timezone.utc)
    credential.expiry = expiry
    if email:
        credential.email = email
    credential.save()
    return credential


def user_credentials(user):
    """Las credenciales OAuth del usuario, renovadas; None si no conectó su cuenta.

    Renovar implica una llamada a Google: solo se hace cuando el token venció."""
    credential = stored_credential(user)
    if credential is None:
        return None
    creds = credential.to_credentials()
    if creds is None or not (creds.refresh_token or creds.token):
        return None
    if not creds.valid:
        try:
            from google.auth.transport.requests import Request

            creds.refresh(Request())
            save_credentials(user, creds)
        except Exception:  # noqa: BLE001 - se cae a la cuenta de servicio
            logger.warning("No se pudieron renovar los tokens de Google de %s", user, exc_info=True)
            return None
    return creds


def active_credentials():
    """Las credenciales del usuario de la petición en curso, o None (sin conexión o sin petición)."""
    request = current_request()
    if request is None:
        return None
    user = current_app_user(request)
    if user is None:
        return None
    return user_credentials(user)


def active_access_token() -> str | None:
    credentials = active_credentials()
    return credentials.token if credentials is not None else None


def disconnect(user):
    credential = stored_credential(user)
    if credential is not None:
        credential.delete()


# ------------------------------------------------------------------ estado para las vistas
def connection_state(request) -> dict:
    credential = stored_credential(current_app_user(request))
    return {
        "connected": credential is not None,
        "email": credential.email if credential is not None else "",
    }


def template_context(request) -> dict:
    """Lo que el selector de fuente necesita para el login de Google y el Google Picker."""
    state = connection_state(request)
    return {
        "configured": is_configured(),
        "connected": state["connected"],
        "email": state["email"],
        "picker_api_key": getattr(settings, "GOOGLE_PICKER_API_KEY", ""),
        "picker_app_id": getattr(settings, "GOOGLE_PICKER_APP_ID", ""),
    }
