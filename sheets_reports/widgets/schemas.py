"""
Esquemas simplificados para widgets: campos de datos y estilo.
Reemplaza DataSpec, ViewOptions, ViewSpec, SpecPart, etc.
"""
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional


@dataclass
class WidgetFields:
    """Parámetros de consulta (data layer) - reemplaza DataSpec."""
    dimensions: List[str] = field(default_factory=list)
    metrics: List[Dict[str, Any]] = field(default_factory=lambda: [{"field": "monto", "agg": "count", "alias": "total"}])
    filters: List[Dict[str, Any]] = field(default_factory=list)
    pivots: List[str] = field(default_factory=list)
    columns: List[Dict[str, Any]] = field(default_factory=list)  # [{"field": "ventas", "label": "Monto"}]
    trend_by: Optional[str] = None  # columna de la mini tendencia (sparkline) del KPI
    sort_by: Optional[str] = None
    limit: Optional[int] = None

    @classmethod
    def from_dict(cls, data: dict) -> "WidgetFields":
        trend_by = data.get("trend_by")
        return cls(
            dimensions=data.get("dimensions", []),
            metrics=data.get("metrics", []),
            filters=data.get("filters", []),
            pivots=data.get("pivots", []),
            columns=_normalize_columns(data.get("columns", [])),
            trend_by=str(trend_by).strip() if trend_by else None,
            sort_by=data.get("sort_by"),
            limit=data.get("limit"),
        )

    def to_dict(self) -> dict:
        return {
            "dimensions": self.dimensions,
            "metrics": self.metrics,
            "filters": self.filters,
            "pivots": self.pivots,
            "columns": self.columns,
            "trend_by": self.trend_by,
            "sort_by": self.sort_by,
            "limit": self.limit,
        }


def _normalize_columns(raw: Any) -> List[Dict[str, Any]]:
    """`columns` acepta strings ("mes") u objetos ({"field": "mes", "label": "Mes"}): todo
    queda como objeto, con el label solo cuando tiene texto."""
    out: List[Dict[str, Any]] = []
    for item in raw or []:
        if isinstance(item, str):
            if item:
                out.append({"field": item})
        elif isinstance(item, dict):
            field = str(item.get("field") or "").strip()
            if not field:
                continue
            column = {"field": field}
            label = str(item.get("label") or "").strip()
            if label:
                column["label"] = label
            out.append(column)
    return out


@dataclass
class WidgetStyle:
    """Configuración visual (presentation layer) - reemplaza ViewOptions/ViewSpec.display."""
    values: Dict[str, Any] = field(default_factory=dict)  # {"title": "...", "stacked": true}

    @classmethod
    def from_dict(cls, data: dict) -> "WidgetStyle":
        return cls(values=data if isinstance(data, dict) else {})

    def to_dict(self) -> dict:
        return self.values

    def get(self, key: str, default=None):
        return self.values.get(key, default)

    def __getattr__(self, name):
        if name in self.values:
            return self.values[name]
        raise AttributeError(f"'WidgetStyle' object has no attribute '{name}'")


@dataclass
class WidgetForm:
    """Formulario completo del widget: datos + estilo."""
    fields: WidgetFields
    style: WidgetStyle

    @classmethod
    def from_dict(cls, data: dict) -> "WidgetForm":
        return cls(
            fields=WidgetFields.from_dict(data.get("fields", {})),
            style=WidgetStyle.from_dict(data.get("style", {})),
        )

    def to_dict(self) -> dict:
        return {
            "fields": self.fields.to_dict(),
            "style": self.style.to_dict(),
        }