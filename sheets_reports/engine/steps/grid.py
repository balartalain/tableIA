"""
Cuadrículas de Google Forms como columnas virtuales.

Una pregunta de cuadrícula llega ancha: una columna por aspecto («Evaluación [Contenido]»,
«Evaluación [Profesor]»…). La fuente aporta además dos columnas virtuales por pregunta,
«<pregunta> · Aspecto» y «<pregunta> · Respuesta», que solo existen dentro de la consulta de un
widget que las usa: `run_steps` filtra primero en ancho (una fila por respuesta, sin
duplicados), después desdobla la cuadrícula (`expand_grid`, una fila por respuesta y aspecto),
aplica los filtros sobre las virtuales y agrega como siempre.

El conteo del motor es de filas distintas sobre el ID: al desdoblar, el ID pasa a ser
`id·aspecto`, así que cada celda Aspecto × Respuesta cuenta respuestas y el % de la fila es
sobre las respuestas a ese aspecto. En una cuadrícula de casillas una persona tiene varias filas
en el mismo aspecto (una por opción marcada) con el mismo ID: el % de la fila es sobre las
personas y puede sumar más de 100 %.

Las cuadrículas de la fuente viajan en `df.attrs["grid_store"]` (ver `services/sheets.load_source`).
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from sheets_reports.engine.formulas import aggregated_fields
from sheets_reports.utils.data import require_id_column

ASPECT, VALUE = "aspect", "value"


@dataclass(frozen=True)
class Grid:
    """Una cuadrícula de la fuente: su pregunta, el nombre a mostrar de cada virtual (None si
    el usuario la excluyó), su formato largo (`row` = índice de la fila de la fuente, `aspect`,
    `value`) y sus columnas anchas (con su nombre a mostrar)."""
    question: str
    aspect: str | None
    value: str | None
    long: pd.DataFrame
    columns: tuple[str, ...] = ()

    @property
    def names(self) -> list[str]:
        return [name for name in (self.aspect, self.value) if name]


class GridStore:
    """Las cuadrículas de una fuente. pandas copia `df.attrs` con deepcopy en cada operación:
    el almacén no se copia, se comparte (sus frames no cambian)."""

    def __init__(self, grids):
        self.grids: list[Grid] = [grid for grid in grids if grid.names]

    def __deepcopy__(self, memo):
        return self

    def fields(self) -> dict[str, tuple[Grid, str]]:
        """{nombre de la virtual: (cuadrícula, ASPECT | VALUE)}."""
        out = {}
        for grid in self.grids:
            if grid.aspect:
                out[grid.aspect] = (grid, ASPECT)
            if grid.value:
                out[grid.value] = (grid, VALUE)
        return out


def grid_fields(df: pd.DataFrame) -> dict[str, tuple[Grid, str]]:
    store = df.attrs.get("grid_store")
    return store.fields() if store else {}


def virtual_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Las virtuales de las cuadrículas como columnas de un frame aparte (cada una en formato
    largo): para describirlas igual que a las reales (tipo, escala, valores)."""
    return pd.DataFrame({name: grid.long[role].reset_index(drop=True)
                         for name, (grid, role) in grid_fields(df).items()})


def query_names(value) -> set[str]:
    """Todos los textos de una consulta (dimensiones, métricas, filtros, orden…): entre ellos,
    las columnas que usa."""
    if isinstance(value, str):
        return {value}
    if isinstance(value, dict):
        value = list(value.values())
    if isinstance(value, (list, tuple)):
        return set().union(*(query_names(v) for v in value)) if value else set()
    if hasattr(value, "to_dict"):
        return query_names(value.to_dict())
    return set()


def grid_in_use(df: pd.DataFrame, fields) -> Grid | None:
    """La cuadrícula cuyas virtuales usa la consulta, o None. Dos a la vez serían un producto
    cartesiano de sus aspectos: se rechaza."""
    virtual = grid_fields(df)
    if not virtual:
        return None
    used = {virtual[name][0].question: virtual[name][0] for name in query_names(fields) if name in virtual}
    if len(used) > 1:
        raise ValueError("Un widget puede usar las columnas de una sola cuadrícula: "
                         f"{' y '.join(f'«{q}»' for q in used)} no se pueden combinar.")
    return next(iter(used.values()), None)


def split_filters(filters: list[dict] | None, names) -> tuple[list[dict], list[dict]]:
    """Las condiciones sobre columnas reales (se aplican en ancho) y sobre las virtuales
    `names` (después de desdoblar)."""
    names = set(names)
    real, virtual = [], []
    for condition in filters or []:
        (virtual if query_names(condition) & names else real).append(condition)
    return real, virtual


def expand_grid(df: pd.DataFrame, grid: Grid, fields) -> pd.DataFrame:
    """Las filas de `df` (ya filtradas en ancho) desdobladas por la cuadrícula: una por
    respuesta y aspecto, con las columnas que usa la consulta, el ID `id·aspecto` y las
    virtuales. Con un campo calculado agregado (su fórmula puede leer cualquier columna) se
    conservan todas."""
    long = grid.long[grid.long["row"].isin(df.index)]
    id_column = require_id_column(df)
    used = query_names(fields)
    if used & set(aggregated_fields(df)):
        keep = list(df.columns)
    else:
        keep = [c for c in df.columns if c in used or c == id_column]
    out = df.loc[long["row"], keep].reset_index(drop=True)
    out[id_column] = out[id_column].astype(str) + "·" + long["aspect"].astype(str).to_numpy()
    if grid.aspect:
        out[grid.aspect] = long["aspect"].to_numpy()
    if grid.value:
        out[grid.value] = long["value"].to_numpy()
    return out
