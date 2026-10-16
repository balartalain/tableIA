"""Deja la petición en curso en un ContextVar para que los servicios de Google sepan con qué
credenciales trabajar (ver google_auth.services). Va después de AuthenticationMiddleware."""
from google_auth.services import reset_current_request, set_current_request


class GoogleAuthMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token = set_current_request(request)
        try:
            return self.get_response(request)
        finally:
            reset_current_request(token)
