"""
WidgetType: el núcleo del diseño. Cada tipo de widget es una clase registrada en WIDGETS que
declara:
- su data_spec (`spec_cls`, una subclase de DataSpec): qué piezas admite y con qué cotas; de
  ahí salen su JSON Schema, sus reglas y su manifiesto (nada de `if widget_type == ...` en el
  resto del sistema);
- opciones de vista (ViewOptions): su presentación, que genera el view_spec;
- reglas propias: además de las de sus piezas, cada una con su prioridad;
- plan de ejecución y compilación al formato que dibuja el frontend.

Agregar un widget = una subclase registrada (más su componente en el frontend). Si necesita
una forma de datos que no existe, además su ResultPlan; si necesita un dato nuevo en el
data_spec, además su SpecPart. Todo puede ir en un solo archivo (widgets/ext/).
"""
import dataclasses
from dataclasses import dataclass, field
from typing import ClassVar, Generic, TypeVar

import pandas as pd

from sheets_reports.dsl.context import SheetContext
from sheets_reports.dsl.errors import SpecValidationError, schema_errors, unique
from sheets_reports.dsl.parts import SPEC_PARTS, UnsupportedPart
from sheets_reports.dsl.registry import Registry
from sheets_reports.dsl.rules import Rule, Stage
from sheets_reports.dsl.spec import DataSpec
from sheets_reports.engine import run
from sheets_reports.engine.plans import PLANS, PlanResult, ResultPlan
from sheets_reports.widgets.presentation import humanize

R = TypeVar("R", bound=PlanResult)
V = TypeVar("V", bound="ViewOptions")


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

    # Cómo usar las opciones que agrega ESTA clase (no las heredadas), para el prompt de la
    # IA: una línea por propiedad, "- clave: …". El prompt agrega a qué widgets aplica.
    ai_doc: ClassVar[str] = ""

    @classmethod
    def ai_properties(cls) -> dict:
        """Propiedades que este widget agrega a `view_options` en la tool de la IA."""
        return {}

    @classmethod
    def ai_required(cls) -> list[str]:
        return []

    @classmethod
    def from_ai(cls, view_options: dict) -> dict:
        """`view_options` de la tool de la IA -> body del builder (lo que lee from_request).
        Por defecto pasa tal cual las propiedades de ai_properties(); una subclase lo
        sobrescribe si su forma para la IA es distinta (ej. los roles del KPI)."""
        labels = {
            item["name"]: item["label"]
            for item in view_options.get("labels") or []
            if isinstance(item, dict) and isinstance(item.get("name"), str) and isinstance(item.get("label"), str)
        }
        own = {key: view_options[key] for key in cls.ai_properties() if key in view_options}
        return {"labels": labels, **own}

    # --- reglas vista → datos

    def reconcile(self, spec: DataSpec):
        """Copia sin referencias a lo que el data_spec ya no tiene (ver la tabla de la
        arquitectura). Las subclases agregan las suyas."""
        names = spec.names()
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
    # Su data_spec: las piezas que admite y con qué cotas.
    spec_cls: ClassVar[type[DataSpec]]
    options_cls: ClassVar[type[ViewOptions]] = ViewOptions
    # Clave del plan en PLANS; un widget cuya forma depende del spec sobrescribe plan().
    plan_key: ClassVar[str]
    # Se calcula con los filtros del tablero aplicados (False: la caja de filtros, cuyas
    # opciones no deben achicarse con su propia selección).
    board_filtered: ClassVar[bool] = True
    # Cuántos widgets de este tipo admite un tablero (None: sin límite).
    max_per_dashboard: ClassVar[int | None] = None
    # La IA puede proponer este tipo de widget.
    ai_enabled: ClassVar[bool] = True
    # Para el prompt de la IA: cuándo elegir este widget (lo que admite se agrega solo desde
    # sus capacidades) y ejemplos [(pedido del usuario, argumentos de create_widget)].
    ai_doc: ClassVar[str] = ""
    ai_examples: ClassVar[tuple[tuple[str, dict], ...]] = ()

    # --- manifiesto ----------------------------------------------------------------------

    def manifest(self) -> dict:
        """Lo que el editor necesita saber de este tipo para armar su panel: qué admite su
        data_spec y qué opciones de vista propias tiene. Sale de sus piezas (lo que aporta cada
        una, o lo que vale si no la tiene) y de los campos de `options_cls`, así el frontend no
        repite flags (agregar una pieza o un campo ya lo publica)."""
        data = {}
        for part_cls in SPEC_PARTS:
            part = self.spec_cls.part(part_cls.key)
            data.update(part.manifest() if part is not None else part_cls.absent_manifest())
        base = {f.name for f in dataclasses.fields(ViewOptions)}
        return {
            "data": data,
            "view": sorted(f.name for f in dataclasses.fields(self.options_cls) if f.name not in base),
            "max_per_dashboard": self.max_per_dashboard,
        }

    # --- data_spec ----------------------------------------------------------------------

    def data_schema(self, ctx: SheetContext, *, for_ai: bool = False) -> dict:
        return self.spec_cls.schema(ctx, for_ai=for_ai)

    def rules(self) -> list[Rule]:
        """Las claves que no admite y las reglas de cada una de sus piezas."""
        return [UnsupportedPart(), *(rule for part in self.spec_cls.parts for rule in part.rules())]

    def errors(self, raw, ctx: SheetContext) -> list[str]:
        """
        Errores legibles de `raw` (vacío si es válido). Nunca lanza. Template method:
        0. `spec_cls.normalize`: sin las claves ajenas vacías, con los valores por defecto;
        1. reglas PRE_SCHEMA sobre el dict crudo (ej. claves que el widget no admite);
        2. JSON Schema de su data_spec, construido desde la hoja real y sus piezas;
        3. reglas SEMANTIC sobre el DataSpec.
        Las reglas se ordenan por (etapa, prioridad); una regla `blocking` que falla devuelve
        solo sus errores.
        """
        raw = self.spec_cls.normalize(raw)
        rules = sorted(self.rules(), key=lambda r: (r.stage, r.priority))
        errors: list[str] = []
        for rule in [r for r in rules if r.stage == Stage.PRE_SCHEMA]:
            found = rule.check(raw, self, ctx)
            if found and rule.blocking:
                return unique(found)
            errors += found
        # Lo ajeno ya lo reportó UnsupportedPart: el schema valida solo sus claves.
        own = self.spec_cls.own(raw) if isinstance(raw, dict) else raw
        errors += schema_errors(self.data_schema(ctx), own, ctx, self.spec_cls.parts)
        if errors:
            return unique(errors)
        spec = self.spec_cls.from_dict(raw)
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
        return self.spec_cls.from_dict(self.spec_cls.normalize(raw))

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
        """La primera métrica (y por qué se agrupa); sin métricas, las columnas que muestra."""
        if not spec.metrics:
            return ", ".join(options.label(c) for c in spec.get("columns", [])) or self.label
        metric = options.label(spec.metrics[0].alias)
        if spec.get("dimensions"):
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
        spec = self.spec_cls.from_dict(data_spec)
        return self.compile(self.execute(spec, df), self.options_cls.from_view(view_spec), spec)


WIDGETS: Registry[WidgetType] = Registry("Tipo de widget")


def percent_metrics(spec: DataSpec) -> list[str]:
    return [m.alias for m in spec.metrics if m.is_percent()]
