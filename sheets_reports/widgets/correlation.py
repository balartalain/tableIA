"""
Matriz de correlación: qué tan relacionadas están entre sí varias columnas, sobre las filas que
cumplen los filtros. Las variables viven en `fields.columns` (como en la Tabla) y son numéricas
(continuas o discretas), ordinales o nominales (`columns_scaled`).

Una nominal entra como variables dummy (one-hot): una fila 0/1 por categoría («Recinto:
Santiago», ¿pertenece o no?), hasta `nominal_max_categories`; una dicotómica (Sí/No), una sola
fila, la del «sí». Las dummies se crean aquí, sobre las filas filtradas, y no se guardan en
ningún lado. Entre dos dummies de la misma variable no hay medida (son excluyentes por
construcción: la celda va vacía, `same_variable`).

La escala de cada columna es la guardada en la fuente o, sin ella, la detectada
(`utils/scales.effective_scales`). Cada par se mide con `engine/steps/association.py`: el método
de `style.method` (Pearson o Spearman) entre numéricas, y Spearman si una es ordinal (por la
posición de su valor en el orden de la escala) o una dummy. De -1 a 1.

Cada par usa sus filas completas y guarda cuántas usó (`n`). Con menos de MIN_ROWS filas, o con
una columna constante, no hay medida: la celda va vacía (None), no un 0 engañoso.
"""
import copy
from typing import Any, ClassVar, Dict, List, Optional

import pandas as pd

from sheets_reports.engine.steps.association import CORRELATED_SCALES, associate, prepare
from sheets_reports.utils.data import date_fields
from sheets_reports.utils.scales import (DEFAULT_IGNORED_VALUES, effective_scales, mask_ignored,
                                          normalize_text, parse_ignored_values, profile_column)
from sheets_reports.widgets.base import WIDGETS, BaseWidget, WidgetResult
from sheets_reports.widgets.presentation import humanize
from sheets_reports.widgets.schemas import WidgetFields, WidgetStyle

# Pares en «lo más relacionado».
TOP_PAIRS = 3
METHODS = ("pearson", "spearman")


@WIDGETS.register
class CorrelationWidget(BaseWidget):
    key = "correlation"
    type_key = "correlation"
    label = "Matriz de correlación"
    ai_doc = ('qué tan relacionadas están entre sí varias columnas, como mapa de calor. Las '
              'variables van en columns y son numéricas, ordinales o nominales (ver la escala de '
              'cada columna); una nominal se muestra como una fila 0/1 por categoría (hasta 10 '
              'categorías), así que úsala solo si tiene pocas. Para «los principales indicadores» '
              'elige de 4 a 10 columnas que midan resultados o evaluaciones (montos, notas, '
              'escalas de satisfacción o acuerdo), sin IDs, fechas ni texto libre.')
    ai_examples = [
        (   'qué relación hay entre ventas, costo y plan',
            {   'widget_type': 'correlation',
                'title': 'Relación entre ventas, costo y plan',
                'fields': {'columns': [{'field': 'ventas'}, {'field': 'costo'}, {'field': 'plan'}]},
                'style': {'method': 'pearson'}}),
        (   'cómo se relacionan las ventas con la categoría',
            {   'widget_type': 'correlation',
                'title': 'Ventas por categoría',
                'fields': {'columns': [{'field': 'ventas'}, {'field': 'costo'}, {'field': 'categoria'}]},
                'style': {'method': 'spearman'}}),
    ]

    capabilities: ClassVar[dict] = {
        "columns": [2, 12],
        "columns_scaled": True,
        # Una nominal es una fila por categoría: tope de categorías por nominal y de filas.
        "nominal_max_categories": 10,
        "matrix_max_rows": 30,
        "columns_label": "Variables a correlacionar",
        # Sin texto de ayuda bajo las variables («Cómo se lee» ya explica la escala).
        "columns_hint": "",
        "dimensions": [0, 0],
        "pivots": [0, 0],
        "metrics": [0, 0],
        "sort": False,
        "limit": False,
        "filters": True,
        "windows": [],
    }

    style_schema: ClassVar[List[Dict[str, Any]]] = [
        {"key": "title", "label": "Título", "type": "string", "default": "Matriz de correlación"},
        {"key": "method", "label": "Método", "type": "choice", "default": "pearson", "options": [
            {"value": "pearson", "label": "Pearson (relación lineal)"},
            {"value": "spearman", "label": "Spearman (por rangos)"},
        ]},
        {"key": "ignoreNonApplicable", "label": "Ignorar respuestas no evaluables", "type": "boolean", "default": False},
        {"key": "ignoredValues", "label": "Valores a ignorar", "type": "string",
         "default": DEFAULT_IGNORED_VALUES},
        {"key": "showValues", "label": "Mostrar valores", "type": "boolean", "default": True},
        {"key": "lowerOnly", "label": "Solo la mitad inferior", "type": "boolean", "default": False},
    ]

    def process_query(self, df: pd.DataFrame, fields: WidgetFields) -> WidgetResult:
        """Las filas que pasan los filtros (sin agrupar), solo con las variables pedidas, y la
        escala de cada una (sobre la hoja entera: la escala es de la columna, no del filtro)."""
        keep = [c["field"] for c in (fields.columns or []) if c["field"] in df.columns]
        scales = effective_scales(df, df.attrs.get("column_scales"), only=keep,
                                  date_columns=date_fields(df[keep]),
                                  submission=df.attrs.get("submission_field"))
        result = super().process_query(df, fields)
        frame = result.rows[[c for c in keep if c in result.rows.columns]]
        metadata = {**result.metadata, "scales": scales}
        return WidgetResult(data=None, metadata=metadata, fields=fields, type="rows", frame=frame)

    def compile(
        self,
        result: WidgetResult,
        style: WidgetStyle,
        fields: Optional[WidgetFields] = None,
        metadata: Optional[dict] = None,
    ) -> dict:
        style_dict = style.to_dict()
        method = style_dict.get("method") if style_dict.get("method") in METHODS else "pearson"
        labels = {c["field"]: c.get("label") or humanize(c["field"]) for c in ((fields.columns if fields else None) or [])}

        ignore_na = bool(style_dict.get("ignoreNonApplicable"))
        ignored_raw = style_dict.get("ignoredValues") if ignore_na else None
        if ignored_raw is None and ignore_na:
            ignored_raw = DEFAULT_IGNORED_VALUES
        ignored_set = parse_ignored_values(ignored_raw) if ignore_na else set()

        frame = result.rows
        base_scales = (metadata or {}).get("scales") or {}
        scales = effective_scales(frame, saved=base_scales, ignored_set=ignored_set)

        # Las filas de la matriz: una por variable, o una por dummy en una nominal.
        rows = []
        for column in frame.columns:
            series = mask_ignored(frame[column], ignored_set)
            profile = scales.get(column) or profile_column(series, column, ignored_set=ignored_set)
            label = labels.get(column, humanize(column))
            for ready in prepare(series, profile["scale"], profile.get("order")):
                row = {**ready, "field": column, "label": label}
                val = ready.get("value")
                if val:
                    row["label"] = f"{label}: {val}"
                rows.append(row)

        size = len(rows)
        matrix = [[None] * size for _ in range(size)]
        measures = [[None] * size for _ in range(size)]
        n = [[0] * size for _ in range(size)]
        for i in range(size):
            for j in range(i, size):
                a, b = rows[i], rows[j]
                if not {a["scale"], b["scale"]} <= set(CORRELATED_SCALES):
                    continue
                if i != j and "value" in a and a["field"] == b["field"]:
                    # Dos categorías de la misma variable: excluyentes, no se comparan.
                    measures[i][j] = measures[j][i] = "same_variable"
                    continue
                value, count, measure = associate(a, b, method)
                matrix[i][j] = matrix[j][i] = value
                measures[i][j] = measures[j][i] = measure
                n[i][j] = n[j][i] = count

        pairs = [(i, j) for i in range(size) for j in range(i + 1, size)
                 if matrix[i][j] is not None]
        pairs.sort(key=lambda p: (-abs(matrix[p[0]][p[1]]), p))
        top = [{"a": rows[i]["label"], "b": rows[j]["label"],
                "r": matrix[i][j], "n": n[i][j], "measure": measures[i][j]}
               for i, j in pairs[:TOP_PAIRS]]

        variables = []
        for row in rows:
            variable = {"field": row["field"], "label": row["label"], "scale": row["scale"]}
            if "value" in row:
                variable.update(value=row["value"], group=row["field"])
            if row["unmatched"]:
                # Filas cuyo valor no está en el orden de la ordinal: cuentan como vacías.
                variable["unmatched"] = row["unmatched"]
            variables.append(variable)

        return {
            "type": "correlation",
            "method": method,
            "variables": variables,
            "matrix": matrix,
            "measures": measures,
            "n": n,
            "rows": len(frame),
            "top": top,
            "style": style_dict,
        }
