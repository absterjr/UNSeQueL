from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .errors import ExecutionError, ParseError
from .lexer import Token, tokenize


class Expr:
    """Marker base class for expression nodes."""


@dataclass(frozen=True)
class Literal(Expr):
    value: Any


@dataclass(frozen=True)
class Identifier(Expr):
    name: str


@dataclass(frozen=True)
class Wildcard(Expr):
    pass


@dataclass(frozen=True)
class Unary(Expr):
    op: str
    operand: Expr


@dataclass(frozen=True)
class Binary(Expr):
    op: str
    left: Expr
    right: Expr


@dataclass(frozen=True)
class Call(Expr):
    name: str
    args: tuple[Expr, ...]


_PRECEDENCE = {
    "OR": 1,
    "AND": 2,
    "=": 3,
    "!=": 3,
    "<>": 3,
    "<": 3,
    "<=": 3,
    ">": 3,
    ">=": 3,
    "LIKE": 3,
    "IS": 3,
    "+": 4,
    "-": 4,
    "*": 5,
    "/": 5,
    "%": 5,
}


class ExpressionParser:
    """Pratt parser for the small expression language used by UNSeQueL."""

    def __init__(self, tokens: list[Token]):
        self.tokens = tokens + [Token("EOF", "", tokens[-1].position if tokens else 0)]
        self.index = 0

    @property
    def current(self) -> Token:
        return self.tokens[self.index]

    def advance(self) -> Token:
        token = self.current
        self.index += 1
        return token

    def match(self, value: str) -> bool:
        if str(self.current.value).upper() == value.upper():
            self.advance()
            return True
        return False

    def parse(self) -> Expr:
        expression = self.parse_expression(0)
        if self.current.kind != "EOF":
            raise ParseError(f"Unexpected token {self.current.value!r} in expression")
        return expression

    def parse_expression(self, minimum_precedence: int) -> Expr:
        left = self.parse_prefix()
        while True:
            operator = str(self.current.value).upper()
            precedence = _PRECEDENCE.get(operator)
            if precedence is None or precedence < minimum_precedence:
                break
            self.advance()
            if operator == "IS" and self.match("NOT"):
                operator = "IS NOT"
            right = self.parse_expression(precedence + 1)
            left = Binary(operator, left, right)
        return left

    def parse_prefix(self) -> Expr:
        token = self.advance()
        if token.kind == "NUMBER" or token.kind == "STRING":
            return Literal(token.value)
        if token.kind == "OP" and token.value in ("+", "-", "!"):
            return Unary(str(token.value), self.parse_expression(6))
        if token.kind == "IDENT":
            keyword = str(token.value).upper()
            if keyword in ("TRUE", "FALSE", "NULL"):
                return Literal({"TRUE": True, "FALSE": False, "NULL": None}[keyword])
            if keyword == "NOT":
                return Unary("NOT", self.parse_expression(6))
            if self.current.value == "(":
                self.advance()
                args: list[Expr] = []
                if self.current.value != ")":
                    if self.current.value == "*":
                        self.advance()
                        args.append(Wildcard())
                    else:
                        while True:
                            args.append(self.parse_expression(0))
                            if not self.match(","):
                                break
                if not self.match(")"):
                    raise ParseError("Expected ')' after function arguments")
                return Call(str(token.value), tuple(args))
            parts = [str(token.value)]
            while self.match("."):
                if self.current.kind != "IDENT":
                    raise ParseError("Expected an identifier after '.'")
                parts.append(str(self.advance().value))
            return Identifier(".".join(parts))
        if token.kind == "OP" and token.value == "*":
            return Wildcard()
        if token.kind == "OP" and token.value == "(":
            expression = self.parse_expression(0)
            if not self.match(")"):
                raise ParseError("Expected ')' after expression")
            return expression
        raise ParseError(f"Unexpected token {token.value!r} in expression")


def parse_expression_tokens(tokens: list[Token]) -> Expr:
    if not tokens:
        raise ParseError("Expected an expression")
    return ExpressionParser(tokens).parse()


def _resolve(row: dict, name: str) -> Any:
    if name in row:
        return row[name]
    lowered = name.lower()
    for key, value in row.items():
        if key.lower() == lowered:
            return value
    raise ExecutionError(f"Column {name!r} was not found in the current row")


def _truth(value: Any) -> bool:
    return value is not None and bool(value)


def _like(value: Any, pattern: Any) -> bool | None:
    if value is None or pattern is None:
        return None
    expression = "".join(".*" if char == "%" else "." if char == "_" else re.escape(char)
                          for char in str(pattern))
    return re.fullmatch(expression, str(value), flags=re.IGNORECASE) is not None


def evaluate(expr: Expr, row: dict, rows: list[dict] | None = None,
             aliases: dict[str, Any] | None = None) -> Any:
    """Evaluate an expression against one row or an aggregate group."""
    aliases = aliases or {}
    if isinstance(expr, Literal):
        return expr.value
    if isinstance(expr, Wildcard):
        return expr
    if isinstance(expr, Identifier):
        if expr.name in aliases:
            return aliases[expr.name]
        return _resolve(row, expr.name)
    if isinstance(expr, Unary):
        value = evaluate(expr.operand, row, rows, aliases)
        if expr.op == "NOT":
            return None if value is None else not _truth(value)
        if value is None:
            return None
        return +value if expr.op == "+" else -value
    if isinstance(expr, Binary):
        left = evaluate(expr.left, row, rows, aliases)
        right = evaluate(expr.right, row, rows, aliases)
        op = expr.op
        if op == "AND":
            if left is False or right is False:
                return False
            if left is None or right is None:
                return None
            return _truth(left) and _truth(right)
        if op == "OR":
            if _truth(left) or _truth(right):
                return True
            if left is None or right is None:
                return None
            return False
        if op in ("IS", "IS NOT"):
            result = left is right if right is None else left == right
            return not result if op == "IS NOT" else result
        if op == "LIKE":
            return _like(left, right)
        if left is None or right is None:
            return None
        if op == "+":
            return left + right
        if op == "-":
            return left - right
        if op == "*":
            return left * right
        if op == "/":
            return None if right == 0 else left / right
        if op == "%":
            return None if right == 0 else left % right
        if op in ("=", "=="):
            return left == right
        if op in ("!=", "<>"):
            return left != right
        if op == "<":
            return left < right
        if op == "<=":
            return left <= right
        if op == ">":
            return left > right
        if op == ">=":
            return left >= right
        raise ExecutionError(f"Unsupported operator {op!r}")
    if isinstance(expr, Call):
        name = expr.name.upper()
        if name in {"COUNT", "SUM", "AVG", "MIN", "MAX"}:
            if rows is None:
                raise ExecutionError(f"{name}() is only valid after grouping")
            if name == "COUNT":
                if not expr.args or isinstance(expr.args[0], Wildcard):
                    return len(rows)
                return sum(evaluate(expr.args[0], item) is not None for item in rows)
            values = [evaluate(expr.args[0], item) for item in rows] if expr.args else []
            values = [value for value in values if value is not None]
            if not values:
                return None
            if name == "SUM":
                return sum(values)
            if name == "AVG":
                return sum(values) / len(values)
            if name == "MIN":
                return min(values)
            return max(values)
        values = [evaluate(arg, row, rows, aliases) for arg in expr.args]
        if name == "COALESCE":
            return next((value for value in values if value is not None), None)
        if name == "LOWER":
            return None if values[0] is None else str(values[0]).lower()
        if name == "UPPER":
            return None if values[0] is None else str(values[0]).upper()
        if name == "ABS":
            return None if values[0] is None else abs(values[0])
        if name == "LENGTH":
            return None if values[0] is None else len(str(values[0]))
        if name == "ROUND":
            if values[0] is None:
                return None
            digits = int(values[1]) if len(values) > 1 else 0
            return round(values[0], digits)
        raise ExecutionError(f"Unsupported function {expr.name!r}")
    raise ExecutionError(f"Unsupported expression node {type(expr).__name__}")


def contains_aggregate(expr: Expr | None) -> bool:
    """Return True if an expression contains a group aggregate call."""
    if expr is None:
        return False
    if isinstance(expr, Call):
        return expr.name.upper() in {"COUNT", "SUM", "AVG", "MIN", "MAX"} \
            or any(contains_aggregate(arg) for arg in expr.args)
    if isinstance(expr, Unary):
        return contains_aggregate(expr.operand)
    if isinstance(expr, Binary):
        return contains_aggregate(expr.left) or contains_aggregate(expr.right)
    return False


def expression_name(expr: Expr) -> str:
    """Produce a readable fallback column name for SELECT without AS."""
    if isinstance(expr, Identifier):
        return expr.name.split(".")[-1]
    if isinstance(expr, Call):
        return f"{expr.name.lower()}"
    if isinstance(expr, Wildcard):
        return "*"
    return "expression"
