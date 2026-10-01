"""
WidgetType: el núcleo del diseño. Cada tipo de widget es una clase registrada en WIDGETS que
declara:
- capacidades (DataCapabilities): qué data_spec admite; de ahí salen su JSON Schema y las
  reglas genéricas (nada de `if widget_type == ...` en el resto del sistema);
- opciones de vista (ViewOptions): su presentación, que genera el view_spec;
- reglas propias: además de las genéricas (dsl/rules.py), cada una con su prioridad;
- plan de ejecución (PLANS) y compilación al formato que dibuja el frontend.

Agregar un widget = una subclase registrada (más su componente en el frontend). Si necesita
una forma de datos que no existe, además un ResultPlan registrado (engine/plans/).
"""
import dataclasses
from dataclasses import dataclass, field
from typing import ClassVar, Generic, TypeVar

import pandas as pd

from sheets_reports.dsl.context import SheetContext
from sheets_reports.dsl.errors import SpecValidationError, schema_errors, unique
from sheets_reports.dsl.registry import Registry
from sheets_reports.dsl.rules import DEFAULT_RULES, Rule, Stage
from sheets_reports.dsl.schema import MAX_METRICS
from sheets_reports.dsl.spec import DataSpec
from sheets_reports.engine import run
from sheets_reports.engine.plans import PLANS, PlanResult, ResultPlan
from sheets_reports.widgets.presentation import humanize

R = TypeVar("R", bound=PlanResult)
V = TypeVar("V", bound="ViewOptions")


@dataclass(frozen=True)
class DataCapabilities:
    dimensions: tuple[int, int]          # (mín, máx) de columnas de filas / eje X (agrupan)
    pivots: tuple[int, int]              # (mín, máx) de columnas de pivote / series (agrupan)
    columns: tuple[int, int] = (0, 0)    # (mín, máx) de columnas que se muestran tal cual
    metrics: tuple[int, int] = (1, MAX_METRICS)
    # Tipos de métrica que admite (claves de METRICS). Lo declara el widget, no la métrica.
    metric_types: frozenset = frozenset({"agg", "calc"})
    # Varias métricas a la vez que un pivote (una subcolumna por métrica en cada valor).
    multi_metric_with_pivot: bool = False
    having: bool = True
    sort: bool = True
    limit: bool = True
    trend: bool = False


@dataclass(frozen=True)
class ViewOptions:
    """
    Opciones de presentación de un widget (nunca cambian el cálculo). Se guardan aplanadas en
    Widget.view_spec junto con lo que se deriva del data_spec.

    Política vista → datos: las opciones que nombran algo del data_spec se reconcilian con él
    (`reconcile`) y nunca invalidan un spec: editar datos no falla por una preferencia de
    presentación.
    """
    title: str = ""
    labels: dict = field(default_factory=dict)   # {as | columna: "Texto legible"}
    display: dict = field(default_factory=dict)  # preferencias puramente de UI del frontend

    # --- construcción

    @classmethod
    def from_request(cls, data: dict, previous: "ViewOptions | None" = None):
        """Desde el body del builder o la respuesta normalizada de la IA. Lo que no viene se
        toma de `previous` (las opciones actuales del widget, al editar)."""
        return cls(**cls._request_fields(data or {}, previous))

    @classmethod
    def _request_fields(cls, data: dict, previous) -> dict:
        title = data.get("title") if "title" in data else (previous.title if previous else "")
        labels = data.get("labels") if isinstance(data.get("labels"), dict) else (previous.labels if previous else {})
        if previous is not None:
            display = previous.display
        else:
            display = data.get("display") if isinstance(data.get("display"), dict) else {}
        return {
            "title": str(title or "").strip(),
            "labels": {k: v.strip() for k, v in (labels or {}).items() if isinstance(v, str) and v.strip()},
            "display": dict(display or {}),
        }

    @classmethod
    def from_view(cls, view: dict | None):
        """Desde un view_spec guardado."""
        view = view or {}
        return cls(**cls._view_fields(view))

    @classmethod
    def _view_fields(cls, view: dict) -> dict:
        return {
            "title": view.get("title") or "",
            "labels": dict(view.get("labels") or {}),
            "display": dict(view.get("display") or {}),
        }

    # --- IA

    @classmethod
    def ai_properties(cls) -> dict:
        """Propiedades que este widget agrega a `view_options` en la tool de la IA."""
        return {}

    @classmethod
    def ai_required(cls) -> list[str]:
        return []

    # --- reglas vista → datos

    def reconcile(self, spec: DataSpec):
        """Copia sin referencias a lo que el data_spec ya no tiene (ver la tabla de la
        arquitectura). Las subclases agregan las suyas."""
        names = {*spec.dimensions, *spec.pivots, *spec.columns, *spec.aliases}
        if spec.trend_by:
            names.add(spec.trend_by)
        return dataclasses.replace(self, labels={k: v for k, v in self.labels.items() if k in names})

    def view_fields(self) -> dict:
        """Campos propios de este tipo de opciones en el view_spec."""
        return {}

    # --- presentación

    def label(self, name) -> str:
        return self.labels.get(name) or humanize(name)


class WidgetType(Generic[R, V]):
    key: ClassVar[str]
    label: ClassVar[str]
    capabilities: ClassVar[DataCapabilities]
    options_cls: ClassVar[type[ViewOptions]] = ViewOptions
    # Clave del plan en PLANS; un widget cuya forma depende del spec sobrescribe plan().
    plan_key: ClassVar[str]

    # --- data_spec ----------------------------------------------------------------------

    def data_schema(self, ctx: SheetContext, *, for_ai: bool = False) -> dict:
        caps = self.capabilities
        return DataSpec.schema(
            ctx, for_ai=for_ai, dimensions=caps.dimensions, pivots=caps.pivots, columns=caps.columns,
            metrics=caps.metrics, metric_types=caps.metric_types,
        )

    def rules(self) -> list[Rule]:
        return list(DEFAULT_RULES)

    def errors(self, raw, ctx: SheetContext) -> list[str]:
        """
        Errores legibles de `raw` (vacío si es válido). Nunca lanza. Template method:
        1. reglas PRE_SCHEMA sobre el dict crudo;
        2. JSON Schema construido desde la hoja real y las capacidades;
        3. reglas SEMANTIC sobre el DataSpec.
        Las reglas se ordenan por (etapa, prioridad); una regla `blocking` que falla devuelve
        solo sus errores.
        """
        rules = sorted(self.rules(), key=lambda r: (r.stage, r.priority))
        errors: list[str] = []
        for rule in [r for r in rules if r.stage == Stage.PRE_SCHEMA]:
            found = rule.check(raw, self, ctx)
            if found and rule.blocking:
                return unique(found)
            errors += found
        errors += schema_errors(self.data_schema(ctx), raw, ctx)
        if errors:
            return unique(errors)
        spec = DataSpec.from_dict(raw)
        for rule in [r for r in rules if r.stage == Stage.SEMANTIC]:
            found = rule.check(spec, self, ctx)
            if found and rule.blocking:
                return unique(found)
            errors += found
        return unique(errors)

    def validate(self, raw, ctx: SheetContext) -> DataSpec:
        errors = self.errors(raw, ctx)
        if errors:
            raise SpecValidationError(errors)
        return DataSpec.from_dict(raw)

    # --- view_spec ----------------------------------------------------------------------

    def options(self, data: dict | None = None, previous_view: dict | None = None) -> V:
        """Opciones de vista desde un request (o la IA), sobre las del view_spec actual."""
        previous = self.options_cls.from_view(previous_view) if previous_view is not None else None
        return self.options_cls.from_request(data or {}, previous)

    def build_view(self, spec: DataSpec, options: V | None = None) -> dict:
        options = (options or self.options_cls()).reconcile(spec)
        return {
            "widget": self.key,
            **self.data_view(spec),
            **options.view_fields(),
            "percent": [m.alias for m in spec.metrics if m.is_percent()],
            "title": options.title or self.default_title(spec, options),
            "labels": options.labels,
            "display": options.display,
        }

    def data_view(self, spec: DataSpec) -> dict:
        """Lo que el view_spec deriva del data_spec (ej. eje X y series de un gráfico)."""
        return {}

    def default_title(self, spec: DataSpec, options: V) -> str:
        metric = options.label(spec.metrics[0].alias)
        if spec.dimensions:
            return f"{metric} por {' y '.join(spec.dimensions)}"
        return metric

    # --- ejecución y presentación -------------------------------------------------------

    def plan(self, spec: DataSpec) -> ResultPlan[R]:
        return PLANS.get(self.plan_key)

    def execute(self, spec: DataSpec, df: pd.DataFrame) -> R:
        return run(spec, df, self.plan(spec))

    def compile(self, result: R, options: V, spec: DataSpec) -> dict:
        """El resultado del plan + las opciones, en el formato exacto que dibuja el frontend."""
        raise NotImplementedError

    def render(self, data_spec: dict, view_spec: dict, df: pd.DataFrame) -> dict:
        """Calcula y compila un widget guardado (data_spec ya validado al guardarse)."""
        spec = DataSpec.from_dict(data_spec)
        return self.compile(self.execute(spec, df), self.options_cls.from_view(view_spec), spec)


WIDGETS: Registry[WidgetType] = Registry("Tipo de widget")


def percent_metrics(spec: DataSpec) -> list[str]:
    return [m.alias for m in spec.metrics if m.is_percent()]
