"""Endpoints del login con Google.

- GET  login/      → redirige al consentimiento de Google (`?next=` es a dónde volver).
- GET  callback/   → cambia el código por tokens, los guarda y vuelve a `next`.
- GET  status/     → JSON con la configuración, si hay cuenta conectada y un `access_token`
                     vigente (lo usa el frontend para abrir el Google Picker).
- POST disconnect/ → olvida los tokens guardados.
"""
import logging

from django.http import JsonResponse
from django.shortcuts import redirect
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from google_auth import services

logger = logging.getLogger(__name__)

STATE_KEY = "google_oauth_state"
NEXT_KEY = "google_oauth_next"


def _safe_next(value: str) -> str:
    """Solo rutas locales: evita un redirect abierto."""
    value = (value or "").strip()
    if value.startswith("/") and not value.startswith("//"):
        return value
    return ""


def _mark_connected(url: str) -> str:
    """Marca la vuelta para que el editor abra el Google Picker solo (`?google=connected`)."""
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}google=connected"


@require_http_methods(["GET"])
def login(request):
    if not services.is_configured():
        return JsonResponse({"error": "Google OAuth no está configurado en el servidor."}, status=400)
    try:
        url, state = services.authorization_url(request)
    except services.GoogleAuthError as e:
        return JsonResponse({"error": str(e)}, status=400)
    request.session[STATE_KEY] = state
    request.session[NEXT_KEY] = _safe_next(request.GET.get("next"))
    return redirect(url)


@require_http_methods(["GET"])
def callback(request):
    if request.GET.get("error"):
        logger.warning("OAuth de Google rechazado: %s", request.GET.get("error"))
        return redirect(_safe_next(request.session.pop(NEXT_KEY, "")) or "/")
    expected = request.session.pop(STATE_KEY, "")
    state = request.GET.get("state", "")
    if not expected or state != expected:
        return JsonResponse({"error": "Estado OAuth inválido."}, status=400)
    next_url = _safe_next(request.session.pop(NEXT_KEY, "")) or "/"
    user = services.current_app_user(request)
    if user is None:
        return JsonResponse({"error": "No hay usuarios creados para guardar la conexión."}, status=401)
    try:
        credentials = services.exchange_code(request, request.GET.get("code", ""), state)
    except services.GoogleAuthError as e:
        return JsonResponse({"error": str(e)}, status=400)
    except Exception:  # noqa: BLE001 - error de red o de Google
        logger.exception("OAuth callback de Google")
        return JsonResponse({"error": "No se pudo conectar con Google."}, status=502)
    services.save_credentials(user, credentials, email=services.credentials_email(credentials))
    return redirect(_mark_connected(next_url))


@require_http_methods(["GET"])
def status(request):
    user = services.current_app_user(request)
    credentials = services.user_credentials(user) if user is not None else None
    credential = services.stored_credential(user) if user is not None else None
    return JsonResponse({
        "configured": services.is_configured(),
        "connected": credentials is not None,
        "email": credential.email if credential is not None else "",
        # El Picker lo necesita en el navegador; es el token del propio usuario.
        "access_token": credentials.token if credentials is not None else "",
    })


@csrf_exempt
@require_http_methods(["POST"])
def disconnect(request):
    user = services.current_app_user(request)
    if user is not None:
        services.disconnect(user)
    return JsonResponse({"connected": False})
