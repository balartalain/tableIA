"""Cifrado de los tokens OAuth de Google antes de guardarlos en la base de datos (Fernet).

La clave es `GOOGLE_TOKEN_ENCRYPTION_KEY` o, si no está, una derivada de SECRET_KEY: cambiarla
obliga a volver a conectar la cuenta de Google (los tokens guardados dejan de poder leerse).
"""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


def _fernet() -> Fernet:
    key = getattr(settings, "GOOGLE_TOKEN_ENCRYPTION_KEY", "")
    if not key:
        key = base64.urlsafe_b64encode(hashlib.sha256(settings.SECRET_KEY.encode()).digest())
    return Fernet(key)


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode() if value else ""


def decrypt(token: str) -> str:
    """El valor guardado o "" si no se puede leer (clave cambiada, texto inválido)."""
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        return ""
