"""
Lo que la cuenta de servicio ve en Google Drive: las hojas de cálculo compartidas con su email
(Drive v3) y las pestañas de cada una (Sheets v4). Lo usa el selector de fuente del editor.
Con caché corta: navegar el selector no golpea Google en cada clic.
"""
import logging

from django.core.cache import cache

from sheets_reports.services.sheets import SheetError

logger = logging.getLogger(__name__)

SPREADSHEET_MIME = "application/vnd.google-apps.spreadsheet"
MAX_SPREADSHEETS = 100
CACHE_TTL_SECONDS = 60


def _preferred_credentials():
    """Las credenciales del usuario que conectó su cuenta de Google (app google_auth)."""
    from google_auth.services import active_credentials

    return active_credentials()


def _service(name: str, version: str, credentials=None):
    creds = credentials or _preferred_credentials()
    if creds is None:
        raise SheetError("Conecta tu cuenta de Google para acceder a las hojas de Google Drive.")
    from googleapiclient.discovery import build

    return build(name, version, credentials=creds, cache_discovery=False)


def list_tabs(spreadsheet_id: str) -> dict:
    """Nombre del documento y sus pestañas en orden: {name, tabs: [{gid, title}]}."""
    key = f"drive_tabs:{spreadsheet_id}"
    cached = cache.get(key)
    if cached is not None:
        return cached
    try:
        response = _service("sheets", "v4").spreadsheets().get(
            spreadsheetId=spreadsheet_id,
            fields="properties.title,sheets.properties(sheetId,title,index)",
        ).execute()
    except SheetError:
        raise
    except Exception as e:  # noqa: BLE001
        logger.exception("Sheets spreadsheets.get")
        raise SheetError(f"No se pudieron leer las pestañas de la hoja: {e}") from e
    sheets = sorted((s.get("properties", {}) for s in response.get("sheets", [])),
                    key=lambda p: p.get("index", 0))
    result = {
        "name": response.get("properties", {}).get("title", ""),
        "tabs": [{"gid": str(p.get("sheetId", 0)), "title": p.get("title", "")} for p in sheets],
    }
    cache.set(key, result, CACHE_TTL_SECONDS)
    return result


def tab_status(spreadsheet_id: str, gid: str) -> str | None:
    """Si el usuario todavía llega a la pestaña de una fuente: "ok", "no_access" o "tab_missing"."""
    try:
        response = _service("sheets", "v4").spreadsheets().get(
            spreadsheetId=spreadsheet_id, fields="sheets.properties.sheetId",
        ).execute()
    except SheetError:
        return None
    except Exception as e:  # noqa: BLE001
        status = getattr(getattr(e, "resp", None), "status", None)
        if status in (403, 404):
            return "no_access"
        logger.warning("Sheets spreadsheets.get (estado): %s", e)
        return None
    gids = {str(s.get("properties", {}).get("sheetId", 0)) for s in response.get("sheets", [])}
    return "ok" if str(gid) in gids else "tab_missing"

