"""
Lectura de una pestaña de Google Sheets como DataFrame, vía el endpoint gviz/tq (CSV),
con caché en el cache framework de Django para no golpear Google en cada request.
"""
import io
import logging

import pandas as pd
import requests
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

GVIZ_URL = "https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq"
# gviz/tq acepta el token OAuth de la service account; drive.readonly cubre hojas compartidas
# con su email aunque no sean públicas.
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]
REQUEST_TIMEOUT_S = 30


class SheetError(Exception):
    """Error legible para el usuario al leer la hoja."""


def _access_token() -> str | None:
    """Token Bearer de la service account, o None si no hay credenciales configuradas
    (en ese caso la hoja debe ser pública por enlace)."""
    path = getattr(settings, "GOOGLE_SHEETS_CREDENTIALS_PATH", "")
    if not path:
        return None
    from google.auth.transport.requests import Request
    from google.oauth2.service_account import Credentials

    creds = Credentials.from_service_account_file(path, scopes=SCOPES)
    creds.refresh(Request())
    return creds.token


def _coerce_numeric_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Convierte a número las columnas de texto cuyos valores no vacíos son todos numéricos
    (gviz a veces las exporta como texto si la columna tiene celdas vacías o mezcladas)."""
    for col in df.columns:
        if df[col].dtype != object:
            continue
        values = df[col].where(df[col].isna(), df[col].astype(str).str.strip().str.replace(",", "", regex=False))
        non_empty = values.dropna()
        non_empty = non_empty[non_empty != ""]
        if non_empty.empty:
            continue
        if pd.to_numeric(non_empty, errors="coerce").notna().all():
            df[col] = pd.to_numeric(values, errors="coerce")
    return df


def fetch_sheet_dataframe(sheet_id: str, gid: str) -> pd.DataFrame:
    """Lee la pestaña sin caché. Preferir get_sheet_dataframe."""
    headers = {}
    token = _access_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        response = requests.get(
            GVIZ_URL.format(sheet_id=sheet_id),
            params={"tqx": "out:csv", "gid": gid, "headers": 1},
            headers=headers,
            timeout=REQUEST_TIMEOUT_S,
        )
    except requests.RequestException as e:
        raise SheetError(f"No se pudo conectar con Google Sheets: {e}") from e

    content_type = response.headers.get("Content-Type", "")
    if response.status_code != 200 or "text/csv" not in content_type:
        raise SheetError(
            "No se pudo leer la hoja. Verifica la URL, el gid de la pestaña y que la hoja "
            "esté compartida con la cuenta de servicio (o sea pública por enlace)."
        )

    df = pd.read_csv(io.StringIO(response.text), keep_default_na=False, na_values=[""])
    df.columns = [str(c).strip() for c in df.columns]
    # Columnas sin encabezado y totalmente vacías (gviz las agrega a veces al final).
    df = df.loc[:, [not (c.startswith("Unnamed:") and df[c].isna().all()) for c in df.columns]]
    return _coerce_numeric_columns(df)


def get_sheet_dataframe(sheet_id: str, gid: str, ttl: int | None = None) -> pd.DataFrame:
    """
    Lee la hoja vía el endpoint gviz/tq como CSV y cachea el DataFrame (cache framework de
    Django) durante `ttl` segundos por (sheet_id, gid). No golpea Google Sheets en cada request.
    """
    if ttl is None:
        ttl = settings.SHEET_CACHE_TTL
    key = f"sheet_df:{sheet_id}:{gid}"
    df = cache.get(key)
    if df is None:
        df = fetch_sheet_dataframe(sheet_id, gid)
        cache.set(key, df, ttl)
    return df.copy()


def invalidate_sheet_cache(sheet_id: str, gid: str) -> None:
    cache.delete(f"sheet_df:{sheet_id}:{gid}")


def get_sheet_schema(df: pd.DataFrame) -> dict:
    return {
        "all_fields": list(df.columns),
        "numeric_fields": list(df.select_dtypes(include="number").columns),
    }


def get_dimension_fields(df: pd.DataFrame, max_numeric_unique: int = 31) -> list[str]:
    """Columnas por las que tiene sentido agrupar: las de texto/fecha, más las numéricas
    enteras con pocos valores distintos (año, mes 1-12, trimestre...). Deja fuera montos y
    medidas continuas."""
    fields = []
    for col in df.columns:
        series = df[col].dropna()
        if not pd.api.types.is_numeric_dtype(df[col]):
            fields.append(col)
        elif (
            not series.empty
            and (series % 1 == 0).all()
            and series.nunique() <= max_numeric_unique
            # Que los valores se repitan (año, mes): los montos casi nunca se repiten tanto.
            and series.nunique() <= len(series) / 2
        ):
            fields.append(col)
    return fields


def get_field_samples(df: pd.DataFrame, max_unique: int = 15) -> dict:
    """Valores distintos de las columnas de texto con pocos valores (ej. categorías, meses),
    para que la IA escriba filtros con el valor exacto. Las columnas de alta cardinalidad
    (nombres, correos, ...) se omiten."""
    samples = {}
    for col in df.columns:
        if pd.api.types.is_numeric_dtype(df[col]):
            continue
        values = df[col].dropna().astype(str).str.strip()
        unique = [v for v in values.unique() if v]
        if 0 < len(unique) <= max_unique:
            samples[col] = unique
    return samples
