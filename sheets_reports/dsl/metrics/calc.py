from dataclasses import dataclass
from typing import ClassVar

from sheets_reports.dsl.calc_ops import CALC_OPS, CalcOp
from sheets_reports.dsl.context import SheetContext
from sheets_reports.dsl.metrics.base import METRICS, FlatContext, Metric, ScalarContext
from sheets_reports.dsl.schema import REF, REF_OR_NUMBER, enum_of


@METRICS.register
@dataclass(frozen=True)
class CalcMetric(Metric):
    """`left <op> right` entre otras métricas del mismo nivel (su `as`); `right` también puede
    ser un número. Se calcula en cada grupo o celda con los valores ya mostrados de las métricas
    a las que se refiere, sin importar en qué posición de la lista estén."""
    key: ClassVar[str] = "calc"
    label: ClassVar[str] = "«calculadas»"
    derived: ClassVar[bool] = True

    alias: str
    op: CalcOp
    left: str
    right: str | float

    @classmethod
    def schema(cls, ctx: SheetContext, *, for_ai=False, nested=False) -> dict:
        return {
            "type": "object",
            "additionalProperties": False,
            "required": ["type", "as", "op", "left", "right"],
            "properties": {
                "type": {"enum": [cls.key]},
                "as": REF,
                "op": enum_of(CALC_OPS),
                "left": REF,
                "right": REF_OR_NUMBER,
            },
        }

    @classmethod
    def from_dict(cls, raw):
        return cls(alias=raw["as"], op=CALC_OPS.get(raw["op"]), left=raw["left"], right=raw["right"])

    def to_dict(self):
        return {"type": self.key, "as": self.alias, "op": self.op.key, "left": self.left, "right": self.right}

    def refs(self) -> list[tuple[str, str]]:
        return [(side, ref) for side, ref in (("left", self.left), ("right", self.right)) if isinstance(ref, str)]

    def depends_on(self):
        return {ref for _, ref in self.refs()}

    def validate(self, ctx, scope, path):
        errors = []
        for side, ref in self.refs():
            if ref not in scope:
                errors.append(f"{path}.{side}: '{ref}' no es una de las métricas de la lista.")
            elif not scope[ref].is_numeric():
                errors.append(f"{path}.{side}: '{ref}' devuelve un grupo, no un número.")
        return errors

    def is_percent(self):
        return self.op.is_percent

    def _operand(self, values: dict, ref):
        return values.get(ref) if isinstance(ref, str) else ref

    def derive(self, values):
        return self.op.scalar(self._operand(values, self.left), self._operand(values, self.right))

    def scalar(self, ev: ScalarContext):
        return self.derive(ev.values)

    def flat(self, fc: FlatContext):
        right = fc.columns[self.right] if isinstance(self.right, str) else self.right
        return self.op.series(fc.columns[self.left], right), self.derive(fc.totals)
