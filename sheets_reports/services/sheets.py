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
from sheets_reports.services import form_transform
from sheets_reports.utils.data import as_dates, find_id_column, is_id_column, time_order, to_key

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


def _read_attempts() -> list[str | None]:
    """Tokens con los que intentar leer la hoja: el token del usuario conectado (app google_auth).
    Si no hay token de usuario, se intenta sin token por si la hoja es pública por enlace."""
    from google_auth.services import active_access_token

    token = active_access_token()
    return [token] if token else [None]


def _request_csv(sheet_id: str, gid: str, token: str | None):
    """Pide la exportación CSV de la pestaña con (o sin) token Bearer."""
    request_headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        return requests.get(
            EXPORT_URL.format(sheet_id=sheet_id),
            params={"format": "csv", "gid": gid},
            headers=request_headers,
            timeout=REQUEST_TIMEOUT_S,
        )
    except requests.RequestException as e:
        raise SheetError(f"No se pudo conectar con Google Sheets: {e}") from e


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


def fetch_sheet_dataframe(sheet_id: str, gid: str, headers: bool = True,
                          coerce: bool = True) -> pd.DataFrame:
    """Lee la pestaña sin caché. Preferir get_sheet_dataframe. Con `headers=False` la primera
    fila también es un dato y las columnas se llaman por su letra («Columna A»).
    Con `coerce=False` devuelve las celdas tal cual, sin convertir a número
    las columnas de texto numérico: lo usan la detección y el tidy de
    respuestas de formulario, que deben ver el crudo (p. ej. «1, 2» de una
    casilla son dos opciones, no el número 12)."""
    response = None
    for token in _read_attempts():
        response = _request_csv(sheet_id, gid, token)
        content_type = response.headers.get("Content-Type", "")
        if response.status_code == 200 and "text/csv" in content_type:
            break
    else:
        raise SheetError(
            "No se pudo leer la hoja. Verifica que la pestaña exista y que la hoja esté "
            "compartida con la cuenta de servicio (o sea pública por enlace), o vuelve a "
            "conectar tu cuenta de Google."
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
    return _coerce_numeric_columns(df) if coerce else df


def _cache_key(sheet_id: str, gid: str, headers: bool) -> str:
    return f"sheet_df:{sheet_id}:{gid}:{int(bool(headers))}"


# Caché de una pestaña (caches["sheets"], no vence): solo el frame que usa la fuente, ya listo.
#   - `…:coerced`: hoja normal, con los números convertidos;
#   - `…:transformed`: respuestas de formulario (casillas expandidas y después los números).
# Cada uno es {df, fetched_at, version}; su fecha va además sola en `…:<tipo>:fetched_at`, para
# mostrar «actualizado» sin cargar el frame. La hoja original no se guarda: se lee de Google al
# conectar la pestaña, con «Actualizar datos» o cuando cambia `_FRAME_VERSION`.
COERCED, TRANSFORMED = "coerced", "transformed"
_FRAME_VERSION = 6


def _frame_key(kind: str, sheet_id: str, gid: str, headers: bool) -> str:
    return f"{_cache_key(sheet_id, gid, headers)}:{kind}"


def _coerced_cache_key(sheet_id: str, gid: str, headers: bool) -> str:
    return _frame_key(COERCED, sheet_id, gid, headers)


def _transformed_cache_key(sheet_id: str, gid: str, headers: bool) -> str:
    return _frame_key(TRANSFORMED, sheet_id, gid, headers)


_tidy_cache_key = _transformed_cache_key


def _build(kind: str, raw: pd.DataFrame) -> pd.DataFrame:
    """El frame de una fuente a partir de la hoja cruda (todo texto, con su ID). En un
    formulario primero se expanden las casillas —«1, 2» son dos opciones, no un número— y
    después se convierten los números."""
    if kind == TRANSFORMED:
        return _coerce_numeric_columns(form_transform.transform(raw))
    return _coerce_numeric_columns(raw.copy())


def _save(kind: str, sheet_id: str, gid: str, headers: bool, raw: pd.DataFrame, stamp) -> pd.DataFrame:
    key = _frame_key(kind, sheet_id, gid, headers)
    df = _build(kind, raw)
    caches["sheets"].set(key, {"df": df, "fetched_at": stamp, "version": _FRAME_VERSION}, None)
    caches["sheets"].set(f"{key}:fetched_at", stamp, None)
    return df


def _read_google(sheet_id: str, gid: str, headers: bool) -> pd.DataFrame:
    return _with_row_id(fetch_sheet_dataframe(sheet_id, gid, headers, coerce=False))


def _frame(kind: str, sheet_id: str, gid: str, headers: bool) -> pd.DataFrame:
    """El frame desde su caché; solo se lee de Google si no está (o es de otra versión)."""
    entry = caches["sheets"].get(_frame_key(kind, sheet_id, gid, headers))
    if entry is not None and entry.get("version") == _FRAME_VERSION:
        return entry["df"].copy()
    return _save(kind, sheet_id, gid, headers, _read_google(sheet_id, gid, headers), now()).copy()


def get_sheet_dataframe(sheet_id: str, gid: str, headers: bool = True) -> pd.DataFrame:
    """La pestaña con los números convertidos, desde el caché; solo se lee de Google la primera
    vez (o tras `refresh_sheet`). El caché no vence: los datos cambian cuando el usuario
    actualiza."""
    return _frame(COERCED, sheet_id, gid, headers)


def get_transformed_dataframe(sheet_id: str, gid: str, headers: bool = True) -> pd.DataFrame:
    """El frame de una pestaña de respuestas de formulario (ver services/form_transform.py),
    desde el caché. Los ajustes de columnas (incluir, tipo, nombre a mostrar) se aplican fuera,
    con `apply_column_config`."""
    return _frame(TRANSFORMED, sheet_id, gid, headers)


get_tidy_dataframe = get_transformed_dataframe


def refresh_sheet(sheet_id: str, gid: str, headers: bool = True, form: bool = False) -> pd.DataFrame:
    """Relee la pestaña de Google y pisa el caché («Actualizar datos») → el frame nuevo de la
    versión pedida (`form`: respuestas de formulario). Si la pestaña también está en caché en
    la otra versión (conectada de las dos formas), se rehace con la misma lectura."""
    raw = _read_google(sheet_id, gid, headers)
    stamp = now()
    wanted, other = (TRANSFORMED, COERCED) if form else (COERCED, TRANSFORMED)
    if caches["sheets"].get(f"{_frame_key(other, sheet_id, gid, headers)}:fetched_at") is not None:
        _save(other, sheet_id, gid, headers, raw, stamp)
    return _save(wanted, sheet_id, gid, headers, raw, stamp).copy()


def _with_row_id(df: pd.DataFrame) -> pd.DataFrame:
    """El frame con la columna de sistema ID: la «id» de la hoja
    (sin mayúsculas) si existe, o una generada `1..n`. Es de solo
    lectura —el editor la muestra pero no se puede cambiar ni excluir—
    y el «Conteo» cuenta filas distintas sobre ella (ver
    utils/data.find_id_column)."""
    if find_id_column(df.columns) is not None:
        return df
    df = df.copy()
    df["_ID"] = range(1, len(df) + 1)
    return df


def fetched_at(source):
    """Cuándo se leyó de Google la hoja de la fuente (None si todavía no está en caché)."""
    kind = TRANSFORMED if source.is_form_response else COERCED
    key = _frame_key(kind, source.sheet_id, source.gid, source.first_row_headers)
    return caches["sheets"].get(f"{key}:fetched_at")


def load_source(source) -> pd.DataFrame:
    """La hoja de una fuente del tablero, con las columnas, los tipos y los nombres
    elegidos, y sus campos calculados: los por fila como columnas; los agregados en
    `df.attrs["aggregated_fields"]` para el motor. Una fuente de
    respuestas de Google Forms (`is_form_response`) parte del frame
    transformado de `services/form_transform.py` (con casillas expandidas)
    y sobre él se aplica la misma configuración de columnas que a
    cualquier hoja."""
    headers = source.first_row_headers
    if source.is_form_response:
        df = get_transformed_dataframe(source.sheet_id, source.gid, headers=headers)
    else:
        df = get_sheet_dataframe(source.sheet_id, source.gid, headers=headers)
    out = apply_calculated_fields(apply_column_config(df, source.columns),
                                  source.calculated_fields)
    if source.is_form_response:
        out.attrs["submission_field"] = submission_field(df, source.columns)
    return out


def submission_field(df: pd.DataFrame, columns: list[dict] | None) -> str | None:
    """La columna de una hoja de respuestas de Google Forms que dice cuándo se envió cada
    respuesta («Marca temporal», «Timestamp»...): la primera de la hoja (sin contar ID), si es
    de fechas. No es una fecha de los datos: la IA no la usa para comparar periodos. Devuelve su
    nombre a mostrar según la configuración `columns`; None si no hay o el usuario la excluyó."""
    first = next((c for c in df.columns if not is_id_column(c)), None)
    if first is None or as_dates(df[first]) is None:
        return None
    config = next((c for c in columns or [] if c.get("name") == first), None)
    if config is None:
        return first
    return column_display_name(config) if config.get("include", True) else None


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


def free_alias(position: int, used: set[str]) -> str:
    """Alias de una columna que no tiene uno guardado (la de sistema ID, o una nueva de la
    hoja): el de su posición en la hoja (`col_1` la primera) o, si ya está en uso, el
    siguiente libre. Lo anota en `used`. El editor y el motor usan esta misma regla."""
    n = position + 1
    while f"col_{n}" in used:
        n += 1
    used.add(f"col_{n}")
    return f"col_{n}"


def apply_column_config(df: pd.DataFrame, columns: list[dict] | None) -> pd.DataFrame:
    """La hoja como la configuró el usuario al conectarla: sin las columnas excluidas, con el
    tipo elegido y con su nombre a mostrar (que reemplaza al encabezado en todo el tablero).
    Sin configuración devuelve la hoja tal cual. Las columnas que ya no existen en la hoja se
    ignoran; las nuevas se incluyen tal cual vienen. El formato de las numéricas (moneda, %)
    queda en `df.attrs["column_formats"]` ({nombre a mostrar: formato}) para el motor. Los alias
    quedan en `df.attrs["column_aliases"]` ({nombre a mostrar: alias}) para la IA, y la escala de
    medida guardada en `df.attrs["column_scales"]` ({nombre a mostrar: {scale, order?}})."""
    if not columns:
        df.attrs.setdefault("column_aliases", {col: f"col_{i + 1}" for i, col in enumerate(df.columns)})
        return df
    df = df.copy()
    position = {col: i for i, col in enumerate(df.columns)}
    excluded = [c["name"] for c in columns if not c.get("include", True) and c.get("name") in df.columns]
    df = df.drop(columns=excluded)
    for column in columns:
        name, cast = column.get("name"), _CASTS.get(column.get("type"))
        if name in df.columns and cast and column.get("include", True):
            converted = cast(df[name])
            # Un tipo guardado que ya no vale para la columna (ej. «número» sobre una que ahora
            # trae texto) la dejaría vacía: se ignora y queda como viene de la hoja.
            if converted.notna().any() or not df[name].notna().any():
                df[name] = converted
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
    # Escala de medida confirmada por el usuario o la IA (la detectada no se guarda).
    df.attrs["column_scales"] = {
        column_display_name(c): {"scale": c["scale"], **({"order": c["order"]} if c.get("order") else {})}
        for c in columns
        if c.get("include", True) and c.get("scale") and column_display_name(c) in df.columns
    }
    column_aliases = {}
    for i, c in enumerate(columns):
        if c.get("include", True):
            disp = column_display_name(c)
            if disp in df.columns:
                column_aliases[disp] = c.get("alias") or f"col_{i + 1}"
    # Sin alias guardado (el ID, una columna nueva): no repite el de ninguna guardada.
    used = {c["alias"] for c in columns if c.get("alias")} | set(column_aliases.values())
    for col in df.columns:
        if col not in column_aliases:
            column_aliases[col] = free_alias(position[col], used)
    df.attrs["column_aliases"] = column_aliases
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
