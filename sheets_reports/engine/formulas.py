"""
Campos calculados de una fuente: fórmulas al estilo de una hoja de cálculo sobre sus columnas.

La fórmula decide el tipo de campo:
  - **por fila** (sin agregaciones): `[Gasto_Real] - [Presupuesto_Asignado]`. Se calcula en cada
    fila antes de agrupar y queda como una columna más (dimensión, filtro o métrica).
  - **agregado** (con SUM, AVG, COUNT…): `SUM([Gasto_Real]) / SUM([Presupuesto_Asignado]) * 100`.
    Se calcula sobre las filas de cada grupo del widget y solo se usa como métrica
    (agregación «auto»: ya la trae la fórmula).

Sintaxis: columnas por nombre (`Ventas`) o entre corchetes si tienen espacios o signos
(`[Gasto Real]`); números (`1.5`), textos entre comillas (`"Sí"`); `+ - * /`, paréntesis,
comparaciones `= != <> > >= < <=`, `AND OR NOT`; funciones `IF(condición, sí, no)` y las
agregaciones `SUM AVG COUNT COUNT_DISTINCT MIN MAX`. Un parser propio: nunca se ejecuta código.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd

AGGREGATE_FUNCTIONS = {
    "SUM": "sum",
    "AVG": "mean",
    "COUNT": "count",
    "COUNT_DISTINCT": "nunique",
    "MIN": "min",
    "MAX": "max",
}
ROW_FUNCTIONS = {"IF"}
FORMATS = ("number", "percent")
MAX_FORMULA_LENGTH = 1000


class FormulaError(ValueError):
    """Fórmula mal escrita o con columnas que no existen: el mensaje es para el usuario."""


# ------------------------------------------------------------------ léxico
_TOKEN_RE = re.compile(r"""
    \s*(?:
        (?P<number>\d+(?:\.\d+)?)
      | (?P<string>"[^"]*"|'[^']*')
      | (?P<bracket>\[[^\]]*\])
      | (?P<op><>|!=|>=|<=|[-+*/(),=<>])
      | (?P<name>[^\W\d][\w]*)
    )""", re.VERBOSE | re.UNICODE)

_KEYWORDS = {"AND", "OR", "NOT"}


@dataclass(frozen=True)
class Token:
    kind: str      # number | string | column | op | func | keyword | end
    value: object
    text: str      # el texto original (para reescribir la fórmula al renombrar columnas)
    start: int


def tokenize(text: str) -> list[Token]:
    tokens, pos = [], 0
    text = text or ""
    while pos < len(text):
        if text[pos:].strip() == "":
            break
        match = _TOKEN_RE.match(text, pos)
        if not match or match.end() == pos:
            raise FormulaError(f"No se entiende la fórmula a partir de «{text[pos:].strip()[:20]}».")
        kind = match.lastgroup
        raw = match.group(kind)
        start = match.start(kind)
        if kind == "number":
            tokens.append(Token("number", float(raw), raw, start))
        elif kind == "string":
            tokens.append(Token("string", raw[1:-1], raw, start))
        elif kind == "bracket":
            name = raw[1:-1].strip()
            if not name:
                raise FormulaError("Hay un nombre de columna vacío: «[]».")
            tokens.append(Token("column", name, raw, start))
        elif kind == "op":
            tokens.append(Token("op", raw, raw, start))
        else:
            upper = raw.upper()
            rest = text[match.end():].lstrip()
            if upper in _KEYWORDS:
                tokens.append(Token("keyword", upper, raw, start))
            elif rest.startswith("("):
                tokens.append(Token("func", upper, raw, start))
            else:
                tokens.append(Token("column", raw, raw, start))
        pos = match.end()
    tokens.append(Token("end", None, "", len(text)))
    return tokens


# ------------------------------------------------------------------ sintaxis
@dataclass(frozen=True)
class Node:
    kind: str          # num | str | col | neg | not | bin | func
    value: object = None
    args: tuple = ()


class _Parser:
    """Precedencia (de menor a mayor): OR, AND, NOT, comparación, + -, * /, unario."""

    def __init__(self, tokens: list[Token]):
        self.tokens, self.i = tokens, 0

    @property
    def tok(self) -> Token:
        return self.tokens[self.i]

    def take(self) -> Token:
        token = self.tokens[self.i]
        self.i += 1
        return token

    def expect(self, value: str) -> None:
        if self.tok.kind == "op" and self.tok.value == value:
            self.i += 1
            return
        found = self.tok.text or "el final"
        raise FormulaError(f"Se esperaba «{value}» y se encontró «{found}».")

    def parse(self) -> Node:
        if self.tok.kind == "end":
            raise FormulaError("Escribe una fórmula.")
        node = self.or_()
        if self.tok.kind != "end":
            previous = self.tokens[self.i - 1]
            if self.tok.kind == "column" and previous.kind == "column":
                guess = f"{previous.text} {self.tok.text}"
                raise FormulaError(f"Si «{guess}» es una columna con espacios, escríbela entre "
                                   f"corchetes: [{guess}].")
            raise FormulaError(f"Sobra «{self.tok.text}» en la fórmula.")
        return node

    def or_(self) -> Node:
        node = self.and_()
        while self.tok.kind == "keyword" and self.tok.value == "OR":
            self.take()
            node = Node("bin", "OR", (node, self.and_()))
        return node

    def and_(self) -> Node:
        node = self.not_()
        while self.tok.kind == "keyword" and self.tok.value == "AND":
            self.take()
            node = Node("bin", "AND", (node, self.not_()))
        return node

    def not_(self) -> Node:
        if self.tok.kind == "keyword" and self.tok.value == "NOT":
            self.take()
            return Node("not", None, (self.not_(),))
        return self.comparison()

    def comparison(self) -> Node:
        node = self.additive()
        if self.tok.kind == "op" and self.tok.value in ("=", "!=", "<>", ">", ">=", "<", "<="):
            op = self.take().value
            node = Node("bin", "!=" if op == "<>" else op, (node, self.additive()))
        return node

    def additive(self) -> Node:
        node = self.term()
        while self.tok.kind == "op" and self.tok.value in ("+", "-"):
            op = self.take().value
            node = Node("bin", op, (node, self.term()))
        return node

    def term(self) -> Node:
        node = self.unary()
        while self.tok.kind == "op" and self.tok.value in ("*", "/"):
            op = self.take().value
            node = Node("bin", op, (node, self.unary()))
        return node

    def unary(self) -> Node:
        if self.tok.kind == "op" and self.tok.value == "-":
            self.take()
            return Node("neg", None, (self.unary(),))
        if self.tok.kind == "op" and self.tok.value == "+":
            self.take()
            return self.unary()
        return self.primary()

    def primary(self) -> Node:
        token = self.take()
        if token.kind == "number":
            return Node("num", token.value)
        if token.kind == "string":
            return Node("str", token.value)
        if token.kind == "column":
            return Node("col", token.value)
        if token.kind == "func":
            return self.call(token)
        if token.kind == "op" and token.value == "(":
            node = self.or_()
            self.expect(")")
            return node
        found = token.text or "el final"
        raise FormulaError(f"No se esperaba «{found}» en la fórmula.")

    def call(self, token: Token) -> Node:
        name = token.value
        if name not in AGGREGATE_FUNCTIONS and name not in ROW_FUNCTIONS:
            known = ", ".join([*AGGREGATE_FUNCTIONS, *sorted(ROW_FUNCTIONS)])
            raise FormulaError(f"La función {token.text} no existe; usa {known}.")
        self.expect("(")
        args = []
        if not (self.tok.kind == "op" and self.tok.value == ")"):
            args.append(self.or_())
            while self.tok.kind == "op" and self.tok.value == ",":
                self.take()
                args.append(self.or_())
        self.expect(")")
        if name == "IF" and len(args) != 3:
            raise FormulaError("IF lleva tres partes: IF(condición, valor si se cumple, valor si no).")
        if name in AGGREGATE_FUNCTIONS and len(args) != 1:
            raise FormulaError(f"{name} lleva una sola columna o expresión: {name}([Columna]).")
        return Node("func", name, tuple(args))


# ------------------------------------------------------------------ análisis
def _walk(node: Node):
    yield node
    for arg in node.args:
        yield from _walk(arg)


def _check_levels(node: Node, inside_aggregate: bool = False) -> bool:
    """¿Es agregada? Valida que no se aniden agregaciones ni se mezclen columnas sueltas
    con agregaciones."""
    if node.kind == "func" and node.value in AGGREGATE_FUNCTIONS:
        if inside_aggregate:
            raise FormulaError(f"No se puede poner una agregación dentro de otra ({node.value}).")
        _check_levels(node.args[0], inside_aggregate=True)
        return True
    return any(_check_levels(arg, inside_aggregate) for arg in node.args)


def _loose_columns(node: Node) -> list[str]:
    """Columnas que quedan fuera de toda agregación."""
    if node.kind == "func" and node.value in AGGREGATE_FUNCTIONS:
        return []
    if node.kind == "col":
        return [node.value]
    return [c for arg in node.args for c in _loose_columns(arg)]


@dataclass(frozen=True)
class Formula:
    """Una fórmula ya analizada contra las columnas de la fuente."""
    text: str
    tree: Node
    aggregated: bool
    columns: tuple[str, ...]

    def evaluate_rows(self, df: pd.DataFrame) -> pd.Series:
        """El valor de cada fila (campo por fila)."""
        result = _eval_row(self.tree, df)
        if not isinstance(result, pd.Series):
            result = pd.Series([result] * len(df), index=df.index)
        return _clean(result)

    def aggregate(self, rows: pd.DataFrame):
        """El valor del grupo `rows` (campo agregado)."""
        value = _eval_aggregate(self.tree, rows)
        if isinstance(value, (float, np.floating)) and not np.isfinite(value):
            return None
        return None if pd.isna(value) else value


def compile_formula(text: str, columns, aggregated_names=()) -> Formula:
    """Analiza `text` contra `columns` (las de la fuente, incluidos los campos por fila
    anteriores). Lanza FormulaError con un mensaje para el usuario."""
    text = (text or "").strip()
    if len(text) > MAX_FORMULA_LENGTH:
        raise FormulaError(f"La fórmula es demasiado larga (máximo {MAX_FORMULA_LENGTH} caracteres).")
    tree = _Parser(tokenize(text)).parse()
    known = {str(c) for c in columns}
    lowered = {c.casefold(): c for c in known}
    used = []
    resolved = {}
    for node in _walk(tree):
        if node.kind != "col":
            continue
        name = node.value
        if name in aggregated_names:
            raise FormulaError(f"«{name}» es un campo agregado: no se puede usar dentro de otra fórmula.")
        real = name if name in known else lowered.get(name.casefold())
        if real is None:
            raise FormulaError(f"La columna «{name}» no existe. Si el nombre tiene espacios, "
                               f"escríbelo entre corchetes: [{name}].")
        resolved[name] = real
        used.append(real)
    tree = _resolve(tree, resolved)
    aggregated = _check_levels(tree)
    if aggregated:
        loose = _loose_columns(tree)
        if loose:
            raise FormulaError(
                f"La fórmula mezcla valores agregados con la columna «{loose[0]}» fila a fila: "
                f"envuélvela en una agregación, por ejemplo SUM([{loose[0]}]).")
    return Formula(text, tree, aggregated, tuple(dict.fromkeys(used)))


def formula_tree(text: str) -> dict | None:
    """El árbol de la fórmula tal como está escrita ({kind, value, args}), para el constructor
    de bloques del editor de la fuente; None si no se entiende."""
    try:
        tree = _Parser(tokenize((text or "").strip())).parse()
    except FormulaError:
        return None

    def as_dict(node: Node) -> dict:
        return {"kind": node.kind, "value": node.value, "args": [as_dict(a) for a in node.args]}
    return as_dict(tree)


def _resolve(node: Node, names: dict) -> Node:
    if node.kind == "col":
        return Node("col", names.get(node.value, node.value))
    if not node.args:
        return node
    return Node(node.kind, node.value, tuple(_resolve(a, names) for a in node.args))


def rename_columns(text: str, mapping: dict[str, str]) -> str:
    """La fórmula con las columnas renombradas (`viejo → nuevo`), sin tocar el resto del texto."""
    if not mapping or not text:
        return text
    try:
        tokens = tokenize(text)
    except FormulaError:
        return text
    out, last = [], 0
    for token in tokens:
        if token.kind == "column" and token.value in mapping:
            out.append(text[last:token.start])
            out.append(f"[{mapping[token.value]}]")
            last = token.start + len(token.text)
    out.append(text[last:])
    return "".join(out)


# ------------------------------------------------------------------ evaluación
def _numeric(value):
    if isinstance(value, pd.Series):
        if pd.api.types.is_numeric_dtype(value) and not pd.api.types.is_bool_dtype(value):
            return value
        if pd.api.types.is_bool_dtype(value):
            return value.astype(float)
        return pd.to_numeric(value, errors="coerce")
    if isinstance(value, (bool, np.bool_)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.replace(",", ""))
        except ValueError:
            return np.nan
    return value


def _comparable(left, right):
    """Comparar texto con texto y número con número: «Sí» = "Sí", 2026 = "2026"."""
    def is_text(v):
        return isinstance(v, str) or (isinstance(v, pd.Series) and not pd.api.types.is_numeric_dtype(v))
    if is_text(left) or is_text(right):
        def as_text(v):
            if isinstance(v, pd.Series):
                return v.map(lambda x: x if pd.isna(x) else _text(x))
            return v if v is None else _text(v)
        return as_text(left), as_text(right)
    return _numeric(left), _numeric(right)


def _text(value) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


_ARITHMETIC: dict[str, Callable] = {
    "+": lambda a, b: a + b,
    "-": lambda a, b: a - b,
    "*": lambda a, b: a * b,
    "/": lambda a, b: a / b,
}
_COMPARE: dict[str, Callable] = {
    "=": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
}


def _truthy(value):
    if isinstance(value, pd.Series):
        return value.fillna(False).astype(bool)
    return bool(value) if not pd.isna(value) else False


def _binary(op, left, right):
    if op in _ARITHMETIC:
        with np.errstate(divide="ignore", invalid="ignore"):
            if op == "/" and not isinstance(right, pd.Series) and _numeric(right) == 0:
                return np.nan if not isinstance(left, pd.Series) else left * np.nan
            return _ARITHMETIC[op](_numeric(left), _numeric(right))
    if op in _COMPARE:
        a, b = _comparable(left, right)
        return _COMPARE[op](a, b)
    if op == "AND":
        return _truthy(left) & _truthy(right)
    return _truthy(left) | _truthy(right)  # OR


def _if(condition, yes, no, index=None):
    if isinstance(condition, pd.Series) or isinstance(yes, pd.Series) or isinstance(no, pd.Series):
        cond = _truthy(condition) if isinstance(condition, pd.Series) else pd.Series(_truthy(condition), index=index)
        return pd.Series(np.where(cond, _broadcast(yes, cond), _broadcast(no, cond)), index=cond.index).infer_objects()
    return yes if _truthy(condition) else no


def _broadcast(value, like: pd.Series):
    return value.reindex(like.index) if isinstance(value, pd.Series) else value


def _eval_row(node: Node, df: pd.DataFrame):
    kind = node.kind
    if kind == "num" or kind == "str":
        return node.value
    if kind == "col":
        return df[node.value]
    if kind == "neg":
        return -_numeric(_eval_row(node.args[0], df))
    if kind == "not":
        value = _eval_row(node.args[0], df)
        return ~_truthy(value) if isinstance(value, pd.Series) else not _truthy(value)
    if kind == "bin":
        return _binary(node.value, _eval_row(node.args[0], df), _eval_row(node.args[1], df))
    if node.value == "IF":
        return _if(*(_eval_row(a, df) for a in node.args), index=df.index)
    raise FormulaError(f"{node.value} es una agregación: no va en un campo por fila.")


def _eval_aggregate(node: Node, rows: pd.DataFrame):
    kind = node.kind
    if kind == "func" and node.value in AGGREGATE_FUNCTIONS:
        values = _eval_row(node.args[0], rows)
        if not isinstance(values, pd.Series):
            values = pd.Series([values] * len(rows), index=rows.index)
        func = AGGREGATE_FUNCTIONS[node.value]
        if func in ("count", "nunique"):
            return float(getattr(values.dropna(), func)())
        numbers = _numeric(values)
        return numbers.agg(func) if numbers.notna().any() else np.nan
    if kind == "num" or kind == "str":
        return node.value
    if kind == "neg":
        return -_numeric(_eval_aggregate(node.args[0], rows))
    if kind == "not":
        return not _truthy(_eval_aggregate(node.args[0], rows))
    if kind == "bin":
        return _binary(node.value, _eval_aggregate(node.args[0], rows), _eval_aggregate(node.args[1], rows))
    if node.value == "IF":
        return _if(*(_eval_aggregate(a, rows) for a in node.args))
    raise FormulaError("Fórmula agregada inválida.")


def _clean(series: pd.Series) -> pd.Series:
    if pd.api.types.is_numeric_dtype(series) and not pd.api.types.is_bool_dtype(series):
        return series.replace([np.inf, -np.inf], np.nan)
    if pd.api.types.is_bool_dtype(series):
        return series.astype(float)
    return series


# ------------------------------------------------------------------ fuente
@dataclass(frozen=True)
class AggregatedField:
    """Un campo agregado de la fuente, listo para el motor (`df.attrs["aggregated_fields"]`)."""
    name: str
    formula: Formula
    format: str = "number"

    @property
    def percent(self) -> bool:
        return self.format == "percent"


def apply_calculated_fields(df: pd.DataFrame, definitions: list[dict] | None,
                            strict: bool = False) -> pd.DataFrame:
    """Agrega a `df` los campos por fila (en orden: uno puede usar a los anteriores) y deja los
    agregados en `df.attrs["aggregated_fields"]` ({nombre: AggregatedField}). Un campo cuya
    fórmula ya no vale (ej. se quitó una columna) se omite; con `strict` lanza FormulaError
    con el nombre del campo."""
    aggregated: dict[str, AggregatedField] = {}
    if definitions:
        df = df.copy()
        for definition in definitions:
            name = str(definition.get("name") or "").strip()
            try:
                if not name:
                    raise FormulaError("Cada campo calculado necesita un nombre.")
                if name in df.columns or name in aggregated:
                    raise FormulaError(f"Ya hay una columna o un campo llamado «{name}».")
                formula = compile_formula(definition.get("formula", ""), df.columns, aggregated)
                if formula.aggregated:
                    aggregated[name] = AggregatedField(name, formula, definition.get("format") or "number")
                else:
                    df[name] = formula.evaluate_rows(df)
            except FormulaError as e:
                if strict:
                    raise FormulaError(f"«{name or 'Campo sin nombre'}»: {e}") from e
    df.attrs["aggregated_fields"] = aggregated
    return df


def aggregated_fields(df: pd.DataFrame) -> dict[str, AggregatedField]:
    return df.attrs.get("aggregated_fields") or {}
