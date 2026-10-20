"""Endpoints del login con Google.

- GET  login/      → redirige al consentimiento de Google (`?next=` es a dónde volver).
- GET  callback/   → cambia el código por tokens, los guarda y vuelve a `next`.
- GET  status/     → JSON con la configuración, si hay cuenta conectada y un `access_token`
                     vigente (lo usa el frontend para abrir el Google Picker).
- POST disconnect/ → olvida los tokens guardados.
"""
import logging

from django.contrib.auth import get_user_model, login as django_login, logout as django_logout
from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from google_auth import services

logger = logging.getLogger(__name__)

STATE_KEY = "google_oauth_state"
NEXT_KEY = "google_oauth_next"
CODE_VERIFIER_KEY = "google_oauth_code_verifier"


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
def login_page(request):
    """Página de inicio de sesión."""
    if request.user.is_authenticated:
        return redirect(_safe_next(request.GET.get("next")) or "home")
    next_url = _safe_next(request.GET.get("next"))
    return render(request, "google_auth/login.html", {
        "configured": services.is_configured(),
        "next": next_url,
    })


@require_http_methods(["GET"])
def login(request):
    if not services.is_configured():
        return JsonResponse({"error": "Google OAuth no está configurado en el servidor."}, status=400)
    try:
        url, state, code_verifier = services.authorization_url(request)
    except services.GoogleAuthError as e:
        return JsonResponse({"error": str(e)}, status=400)
    request.session[STATE_KEY] = state
    request.session[NEXT_KEY] = _safe_next(request.GET.get("next"))
    if isinstance(code_verifier, str):
        request.session[CODE_VERIFIER_KEY] = code_verifier
    request.session.save()
    return redirect(url)


@require_http_methods(["GET"])
def callback(request):
    if request.GET.get("error"):
        logger.warning("OAuth de Google rechazado: %s", request.GET.get("error"))
        return redirect(_safe_next(request.session.pop(NEXT_KEY, "")) or "home")
    expected = request.session.get(STATE_KEY, "")
    state = request.GET.get("state", "")
    if not expected or state != expected:
        logger.warning(
            "Estado OAuth inválido. Esperado en sesión: %r, recibido en URL: %r. "
            "Asegúrate de acceder usando el mismo dominio/host (localhost vs 127.0.0.1).",
            expected, state
        )
        return JsonResponse({
            "error": "Estado OAuth inválido.",
            "detalle": "La sesión expiró o ingresaste usando un host distinto (ej. localhost vs 127.0.0.1). Intenta iniciar sesión de nuevo."
        }, status=400)
    request.session.pop(STATE_KEY, None)
    code_verifier = request.session.pop(CODE_VERIFIER_KEY, None)
    next_url = _safe_next(request.session.pop(NEXT_KEY, "")) or reverse("home")
    try:
        credentials = services.exchange_code(
            request, request.GET.get("code", ""), state, code_verifier=code_verifier
        )
    except services.GoogleAuthError as e:
        return JsonResponse({"error": str(e)}, status=400)
    except Exception:  # noqa: BLE001 - error de red o de Google
        logger.exception("OAuth callback de Google")
        return JsonResponse({"error": "No se pudo conectar con Google."}, status=502)

    email = services.credentials_email(credentials)

    # Autenticar usuario en Django
    if request.user.is_authenticated:
        user = request.user
    else:
        User = get_user_model()
        if email:
            user = User.objects.filter(email__iexact=email).first()
            if not user:
                user = User.objects.create_user(username=email, email=email)
        else:
            user = services.current_app_user(request)

        if user is None:
            return JsonResponse({"error": "No se pudo determinar el usuario de Google."}, status=401)
        django_login(request, user)

    services.save_credentials(user, credentials, email=email)

    if next_url in (reverse("home"), reverse("login_page")):
        return redirect("home")
    return redirect(_mark_connected(next_url))


@require_http_methods(["GET", "POST"])
def logout_view(request):
    """Cierra la sesión del usuario."""
    django_logout(request)
    return redirect("login_page")


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
