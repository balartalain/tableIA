"""
Condiciones sobre GRUPOS ({left, op, right}): comparan métricas ya calculadas por grupo, entre
sí o contra un número.

El tipo se usa en dos lugares con alcances distintos, y por eso con dos nombres:
- `DataSpec.having`: grupos de la primera dimensión del widget; sus referencias son las
  métricas del widget.
- `GroupedMetric.inner_having`: grupos de `group_by` dentro de una métrica «por grupo»; sus
  referencias son sus métricas internas.
Los nombres válidos se pasan siempre de forma explícita (`names`), nunca se infieren.
"""
import operator
from dataclasses import dataclass

import pandas as pd

from sheets_reports.dsl.schema import MAX_HAVING, REF, REF_OR_NUMBER

COMPARATORS = {
    "eq": operator.eq,
    "ne": operator.ne,
    "lt": operator.lt,
    "lte": operator.le,
    "gt": operator.gt,
    "gte": operator.ge,
}


def group_condition_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["left", "op", "right"],
        "properties": {"left": REF, "op": {"enum": list(COMPARATORS)}, "right": REF_OR_NUMBER},
    }


def group_conditions_schema() -> dict:
    return {"type": "array", "maxItems": MAX_HAVING, "items": group_condition_schema()}


@dataclass(frozen=True)
class GroupCondition:
    left: str
    op: str
    right: str | float

    @classmethod
    def from_dict(cls, raw: dict) -> "GroupCondition":
        return cls(left=raw["left"], op=raw["op"], right=raw["right"])

    def to_dict(self) -> dict:
        return {"left": self.left, "op": self.op, "right": self.right}

    def refs(self) -> list[tuple[str, str]]:
        return [(side, ref) for side, ref in (("left", self.left), ("right", self.right)) if isinstance(ref, str)]

    def validate(self, names: set[str], path: str) -> list[str]:
        return [
            f"{path}.{side}: '{ref}' no es una de las métricas ({', '.join(sorted(names)) or 'ninguna'})."
            for side, ref in self.refs() if ref not in names
        ]

    def mask(self, table: pd.DataFrame) -> pd.Series:
        left = pd.to_numeric(table[self.left], errors="coerce")
        right = pd.to_numeric(table[self.right], errors="coerce") if isinstance(self.right, str) else self.right
        cond = COMPARATORS[self.op](left, right) & left.notna()
        if isinstance(right, pd.Series):
            cond &= right.notna()
        return cond.fillna(False).astype(bool)


def parse_group_conditions(raw: list[dict] | None) -> list[GroupCondition]:
    return [GroupCondition.from_dict(c) for c in raw or []]


def group_conditions_errors(conditions: list[GroupCondition], names: set[str], path: str) -> list[str]:
    errors = []
    for i, cond in enumerate(conditions):
        errors += cond.validate(names, f"{path}[{i}]")
    return errors


def having_mask(table: pd.DataFrame, conditions: list[GroupCondition]) -> pd.Series:
    mask = pd.Series(True, index=table.index)
    for cond in conditions or []:
        mask &= cond.mask(table)
    return mask
