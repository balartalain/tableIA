from django.conf import settings
from django.db import models


class GoogleCredential(models.Model):
    """Los tokens OAuth 2.0 con los que un usuario lee su Drive y sus hojas de cálculo.

    Se piden cifrados (crypto.py). El `refresh_token` es lo que permite leer sin volver a
    pedir permiso al usuario; se renueva el `access_token` cuando vence (services.py)."""
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="google_credential",
        help_text="Usuario cuya cuenta de Google está conectada.",
    )
    email = models.EmailField(blank=True, default="", help_text="Correo de la cuenta conectada.")
    access_token_encrypted = models.TextField(blank=True, default="")
    refresh_token_encrypted = models.TextField(blank=True, default="")
    token_uri = models.CharField(max_length=255, default="https://oauth2.googleapis.com/token")
    scopes = models.JSONField(default=list, blank=True, help_text="Permisos concedidos por el usuario.")
    expiry = models.DateTimeField(null=True, blank=True, help_text="Vencimiento del access token.")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Credencial de Google"
        verbose_name_plural = "Credenciales de Google"

    def __str__(self):
        return f"Google de {self.user} ({self.email or 'sin correo'})"

    def to_credentials(self):
        """Las credenciales de `google.oauth2` para llamar a la API, o None sin cliente OAuth."""
        from google.oauth2.credentials import Credentials

        from google_auth import crypto

        client_id = getattr(settings, "GOOGLE_OAUTH_CLIENT_ID", "")
        client_secret = getattr(settings, "GOOGLE_OAUTH_CLIENT_SECRET", "")
        if not client_id or not client_secret:
            return None
        return Credentials(
            token=crypto.decrypt(self.access_token_encrypted) or None,
            refresh_token=crypto.decrypt(self.refresh_token_encrypted) or None,
            token_uri=self.token_uri or "https://oauth2.googleapis.com/token",
            client_id=client_id,
            client_secret=client_secret,
            scopes=list(self.scopes or []) or None,
            expiry=self.expiry,
        )
