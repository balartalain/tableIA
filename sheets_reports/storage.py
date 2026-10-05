"""Estáticos con versión en la URL: `{% static %}` añade `?v=<mtime>` del archivo.

Sin versión, el navegador reutiliza un JS viejo de su caché junto a una plantilla nueva (por
ejemplo, la plantilla llama a una función del store que el JS cacheado todavía no tiene).
Funciona igual en desarrollo (archivos de cada app) y en producción (`collectstatic`).
"""
import os

from django.contrib.staticfiles import finders
from django.contrib.staticfiles.storage import StaticFilesStorage


class VersionedStaticFilesStorage(StaticFilesStorage):
    def url(self, name):
        url = super().url(name)
        path = self.path(name) if self.exists(name) else finders.find(name)
        if not path or not os.path.isfile(path):
            return url
        return f"{url}{'&' if '?' in url else '?'}v={int(os.path.getmtime(path))}"
