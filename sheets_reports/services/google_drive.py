"""
Lo que la cuenta de servicio ve en Google Drive: las hojas de cálculo compartidas con su email
(Drive v3) y las pestañas de cada una (Sheets v4). Lo usa el selector de fuente del editor.
Con caché corta: navegar el selector no golpea Google en cada clic.
"""
import logging

from django.core.cache import cache

from sheets_reports.services.sheets import SheetError, service_account_credentials

logger = logging.getLogger(__name__)

SPREADSHEET_MIME = "application/vnd.google-apps.spreadsheet"
MAX_SPREADSHEETS = 100
CACHE_TTL_SECONDS = 60


def _service(name: str, version: str):
    creds = service_account_credentials()
    if creds is None:
        raise SheetError("Configura GOOGLE_SHEETS_CREDENTIALS_PATH para listar las hojas de Google Drive.")
    from googleapiclient.discovery import build

    return build(name, version, credentials=creds, cache_discovery=False)


def _escape(text: str) -> str:
    """Texto dentro de una comilla simple de la consulta de Drive."""
    return text.replace("\\", "\\\\").replace("'", "\\'")


def list_spreadsheets(query: str = "") -> list[dict]:
    """Hojas de cálculo visibles para la cuenta de servicio, las modificadas más recientemente
    primero: [{id, name, modified}]. `query` filtra por nombre."""
    query = (query or "").strip()
    key = f"drive_spreadsheets:{query.lower()}"
    cached = cache.get(key)
    if cached is not None:
        return cached
    q = f"mimeType='{SPREADSHEET_MIME}' and trashed=false"
    if query:
        q += f" and name contains '{_escape(query)}'"
    try:
        response = _service("drive", "v3").files().list(
            q=q, orderBy="modifiedTime desc", pageSize=MAX_SPREADSHEETS,
            fields="files(id,name,modifiedTime)",
            includeItemsFromAllDrives=True, supportsAllDrives=True,
        ).execute()
    except SheetError:
        raise
    except Exception as e:  # noqa: BLE001 - HttpError, red, credenciales: un mensaje legible
        logger.exception("Drive files.list")
        raise SheetError(f"No se pudo listar las hojas de Google Drive: {e}") from e
    result = [{"id": f["id"], "name": f.get("name", ""), "modified": f.get("modifiedTime")}
              for f in response.get("files", [])]
    cache.set(key, result, CACHE_TTL_SECONDS)
    return result


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
    """Si la cuenta de servicio todavía llega a la pestaña de una fuente, sin caché (lo que vale
    es el estado de ahora): "ok", "no_access" (no la puede abrir: se dejó de compartir o se
    borró), "tab_missing" (la abre, pero la pestaña ya no está) o None si no se pudo saber (sin
    credenciales, red)."""
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

