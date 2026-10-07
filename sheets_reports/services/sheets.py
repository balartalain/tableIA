"""
Lectura de una pestaña de Google Sheets como DataFrame, vía la exportación CSV de la hoja.
El DataFrame queda en caché (alias `sheets`) hasta que el usuario pulsa «Actualizar datos»:
el tablero no relee Google por su cuenta.
"""
import io
import logging
import string

import pandas as pd
import requests
from django.conf import settings
from django.core.cache import caches
from django.utils.timezone import now

from sheets_reports.engine.formulas import apply_calculated_fields
from sheets_reports.utils.data import time_order, to_key

logger = logging.getLogger(__name__)

# La exportación CSV devuelve las celdas tal cual se ven (gviz/tq infiere un tipo por columna y
# anula los valores del tipo minoritario). Acepta el token OAuth de la service account;
# drive.readonly cubre hojas compartidas con su email aunque no sean públicas.
EXPORT_URL = "https://docs.google.com/spreadsheets/d/{sheet_id}/export"
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]
REQUEST_TIMEOUT_S = 30


class SheetError(Exception):
    """Error legible para el usuario al leer la hoja."""


def service_account_credentials():
    """Credenciales de la service account, o None si no hay configuradas (en ese caso solo se
    leen hojas públicas por enlace y no se puede listar el Drive)."""
    path = getattr(settings, "GOOGLE_SHEETS_CREDENTIALS_PATH", "")
    if not path:
        return None
    from google.oauth2.service_account import Credentials

    return Credentials.from_service_account_file(path, scopes=SCOPES)


def service_account_email() -> str | None:
    """Email de la cuenta de servicio: con él se comparten las hojas que el tablero puede leer."""
    creds = service_account_credentials()
    return getattr(creds, "service_account_email", None) if creds else None


def _access_token() -> str | None:
    """Token Bearer de la service account, o None si no hay credenciales configuradas."""
    creds = service_account_credentials()
    if creds is None:
        return None
    from google.auth.transport.requests import Request

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


def column_letter(index: int) -> str:
    """0 → A, 25 → Z, 26 → AA: la letra de la columna en la hoja."""
    letters = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        letters = string.ascii_uppercase[rest] + letters
    return letters


def fetch_sheet_dataframe(sheet_id: str, gid: str, headers: bool = True) -> pd.DataFrame:
    """Lee la pestaña sin caché. Preferir get_sheet_dataframe. Con `headers=False` la primera
    fila también es un dato y las columnas se llaman por su letra («Columna A»)."""
    request_headers = {}
    token = _access_token()
    if token:
        request_headers["Authorization"] = f"Bearer {token}"
    try:
        response = requests.get(
            EXPORT_URL.format(sheet_id=sheet_id),
            params={"format": "csv", "gid": gid},
            headers=request_headers,
            timeout=REQUEST_TIMEOUT_S,
        )
    except requests.RequestException as e:
        raise SheetError(f"No se pudo conectar con Google Sheets: {e}") from e

    content_type = response.headers.get("Content-Type", "")
    if response.status_code != 200 or "text/csv" not in content_type:
        raise SheetError(
            "No se pudo leer la hoja. Verifica que la pestaña exista y que la hoja esté "
            "compartida con la cuenta de servicio (o sea pública por enlace)."
        )

    df = pd.read_csv(io.StringIO(response.text), keep_default_na=False, na_values=[""],
                     header=0 if headers else None, dtype=str)
    if headers:
        df.columns = [str(c).strip() for c in df.columns]
    else:
        df.columns = [f"Columna {column_letter(i)}" for i in range(len(df.columns))]
    # Columnas totalmente vacías sin encabezado (la exportación incluye las de relleno).
    unnamed = (lambda col: col.startswith("Unnamed:")) if headers else (lambda col: True)
    df = df.loc[:, [not (unnamed(col) and df[col].isna().all()) for col in df.columns]]
    return _coerce_numeric_columns(df)


def _cache_key(sheet_id: str, gid: str, headers: bool) -> str:
    return f"sheet_df:{sheet_id}:{gid}:{int(bool(headers))}"


def get_sheet_dataframe(sheet_id: str, gid: str, headers: bool = True) -> pd.DataFrame:
    """La pestaña desde el caché; solo se lee de Google la primera vez (o tras
    `refresh_sheet`). El caché no vence: los datos cambian cuando el usuario actualiza."""
    entry = caches["sheets"].get(_cache_key(sheet_id, gid, headers))
    if entry is None:
        entry = _store(sheet_id, gid, headers)
    return entry["df"].copy()


def refresh_sheet(sheet_id: str, gid: str, headers: bool = True) -> pd.DataFrame:
    """Relee la pestaña de Google y pisa el caché («Actualizar datos»)."""
    return _store(sheet_id, gid, headers)["df"].copy()


def _store(sheet_id: str, gid: str, headers: bool) -> dict:
    entry = {"df": fetch_sheet_dataframe(sheet_id, gid, headers), "fetched_at": now()}
    caches["sheets"].set(_cache_key(sheet_id, gid, headers), entry, None)
    return entry


def fetched_at(source):
    """Cuándo se leyó de Google la hoja de la fuente (None si todavía no está en caché)."""
    entry = caches["sheets"].get(_cache_key(source.sheet_id, source.gid, source.first_row_headers))
    return entry["fetched_at"] if entry else None


def load_source(source) -> pd.DataFrame:
    """La hoja de una fuente del tablero, con las columnas, los tipos y los nombres elegidos, y
    sus campos calculados: los por fila como columnas; los agregados en
    `df.attrs["aggregated_fields"]` para el motor."""
    df = get_sheet_dataframe(source.sheet_id, source.gid, headers=source.first_row_headers)
    return apply_calculated_fields(apply_column_config(df, source.columns), source.calculated_fields)


def source_key(source) -> str:
    """Identifica la hoja de una fuente (caché de sugerencias, contexto de la IA)."""
    return f"{source.sheet_id}:{source.gid}"


# ---------------------------------------------------------------- tipos de columna
COLUMN_TYPES = ("text", "number", "date")
# Cómo se muestran los valores de una columna numérica en todo el tablero (sin formato: número).
COLUMN_FORMATS = ("currency", "percent")


def infer_column_type(series: pd.Series) -> str:
    """Tipo que se propone al conectar la hoja: número si pandas ya la leyó numérica, fecha si
    sus valores son fechas o meses (ver utils/data.time_order), texto en otro caso."""
    if pd.api.types.is_numeric_dtype(series):
        return "number"
    values = list({to_key(v) for v in series.dropna() if str(v).strip()})
    if values and time_order(values) is not None:
        return "date"
    return "text"


def infer_column_types(df: pd.DataFrame) -> dict[str, str]:
    return {col: infer_column_type(df[col]) for col in df.columns}


def _as_number(series: pd.Series) -> pd.Series:
    if pd.api.types.is_numeric_dtype(series):
        return series
    text = series.where(series.isna(), series.astype(str).str.strip().str.replace(",", "", regex=False))
    return pd.to_numeric(text.replace("", None), errors="coerce")


def _as_text(series: pd.Series) -> pd.Series:
    # 2026.0 → "2026": un año leído como número no debe quedar con decimales al pasarlo a texto.
    return series.map(lambda v: v if pd.isna(v) else str(to_key(v))).astype(object)


def _as_date(series: pd.Series) -> pd.Series:
    """Fechas en ISO (AAAA-MM-DD): el motor las reconoce como columna de tiempo y las ordena.
    Años sueltos (2026) y meses por nombre (Ene, Feb...) no son fechas completas: quedan como
    texto, que el motor igual ordena como periodo."""
    if pd.api.types.is_numeric_dtype(series):
        return _as_text(series)
    dates = pd.to_datetime(series.astype("string"), errors="coerce", dayfirst=True, format="mixed")
    if dates.notna().sum() == 0:
        return _as_text(series)
    return dates.dt.strftime("%Y-%m-%d").where(dates.notna(), None).astype(object)


_CASTS = {"number": _as_number, "text": _as_text, "date": _as_date}


def column_display_name(column: dict) -> str:
    """El nombre con el que la columna se ve y se usa en el tablero: su `label` o, sin él, el
    encabezado de la hoja."""
    return str(column.get("label") or "").strip() or column["name"]


def apply_column_config(df: pd.DataFrame, columns: list[dict] | None) -> pd.DataFrame:
    """La hoja como la configuró el usuario al conectarla: sin las columnas excluidas, con el
    tipo elegido y con su nombre a mostrar (que reemplaza al encabezado en todo el tablero).
    Sin configuración devuelve la hoja tal cual. Las columnas que ya no existen en la hoja se
    ignoran; las nuevas se incluyen tal cual vienen. El formato de las numéricas (moneda, %)
    queda en `df.attrs["column_formats"]` ({nombre a mostrar: formato}) para el motor."""
    if not columns:
        return df
    df = df.copy()
    excluded = [c["name"] for c in columns if not c.get("include", True) and c.get("name") in df.columns]
    df = df.drop(columns=excluded)
    for column in columns:
        name, cast = column.get("name"), _CASTS.get(column.get("type"))
        if name in df.columns and cast and column.get("include", True):
            df[name] = cast(df[name])
    renames = {c["name"]: column_display_name(c) for c in columns
               if c.get("include", True) and c.get("name") in df.columns
               and column_display_name(c) != c["name"]}
    if renames:
        df = df.rename(columns=renames)
    df.attrs["column_formats"] = {
        column_display_name(c): c["format"] for c in columns
        if c.get("include", True) and c.get("type") == "number" and c.get("format") in COLUMN_FORMATS
        and column_display_name(c) in df.columns
    }
    return df


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
