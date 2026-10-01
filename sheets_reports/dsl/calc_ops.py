"""Operaciones de una métrica calculada (left <op> right). Nunca se evalúa texto: cada
operación es una función fija, que sirve tanto para escalares como para Series."""
import operator
from typing import Any, ClassVar

import numpy as np
import pandas as pd

from sheets_reports.dsl.registry import Registry
from sheets_reports.dsl.values import to_python

CALC_OPS: Registry["CalcOp"] = Registry("Operación de cálculo")


class CalcOp:
    key: ClassVar[str]
    function: ClassVar[Any]
    divides: ClassVar[bool] = False
    # El resultado es un porcentaje (se redondea a 2 decimales y se muestra con "%").
    is_percent: ClassVar[bool] = False

    def scalar(self, left, right):
        """Cálculo escalar; None si falta un lado o se divide entre cero."""
        left, right = to_python(left), to_python(right)
        if left is None or right is None or isinstance(left, dict) or isinstance(right, dict):
            return None
        if self.divides and not right:
            return None
        value = type(self).function(left, right)
        return round(value, 2) if self.is_percent else value

    def series(self, left: pd.Series, right) -> pd.Series:
        """Cálculo por grupo (vectorizado); los grupos sin valor o con divisor 0 quedan en NaN."""
        left = pd.to_numeric(left, errors="coerce")
        right = pd.to_numeric(right, errors="coerce") if isinstance(right, pd.Series) else right
        if self.divides:
            right = right.where(right != 0) if isinstance(right, pd.Series) else (right or np.nan)
        value = type(self).function(left, right)
        return value.round(2) if self.is_percent else value


@CALC_OPS.register
class Add(CalcOp):
    key, function = "add", operator.add


@CALC_OPS.register
class Sub(CalcOp):
    key, function = "sub", operator.sub


@CALC_OPS.register
class Mul(CalcOp):
    key, function = "mul", operator.mul


@CALC_OPS.register
class Div(CalcOp):
    key, function, divides = "div", operator.truediv, True


def _ratio_pct(left, right):
    return left / right * 100


def _diff_pct(left, right):
    return (left - right) / right * 100


@CALC_OPS.register
class RatioPct(CalcOp):
    """left / right × 100: margen, % de cumplimiento."""
    key, function, divides, is_percent = "ratio_pct", staticmethod(_ratio_pct), True, True


@CALC_OPS.register
class DiffPct(CalcOp):
    """(left − right) / right × 100: variación, crecimiento."""
    key, function, divides, is_percent = "diff_pct", staticmethod(_diff_pct), True, True
