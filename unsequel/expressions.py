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
    distinct: bool = False


@dataclass(frozen=True)
class InList(Expr):
    value: Expr
    options: tuple[Expr, ...]
    negated: bool = False


@dataclass(frozen=True)
class Between(Expr):
    value: Expr
    lower: Expr
    upper: Expr
    negated: bool = False


@dataclass(frozen=True)
class Case(Expr):
    branches: tuple[tuple[Expr, Expr], ...]
    else_expr: Expr


@dataclass(frozen=True)
class Cast(Expr):
    operand: Expr
    type_name: str


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
    "IN": 3,
    "BETWEEN": 3,
    "NOT IN": 3,
    "NOT LIKE": 3,
    "NOT BETWEEN": 3,
    "+": 4,
    "-": 4,
    "||": 4,
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
            if operator == "NOT" and self.index + 1 < len(self.tokens):
                next_operator = str(self.tokens[self.index + 1].value).upper()
                if next_operator in {"IN", "LIKE", "BETWEEN"}:
                    operator = f"NOT {next_operator}"
                    precedence = _PRECEDENCE[operator]
                    if precedence < minimum_precedence:
                        break
                    self.advance()
                    self.advance()
                    left = self._parse_special_binary(left, operator, precedence)
                    continue
            precedence = _PRECEDENCE.get(operator)
            if precedence is None or precedence < minimum_precedence:
                break
            self.advance()
            if operator == "IS" and self.match("NOT"):
                operator = "IS NOT"
            if operator in {"IN", "NOT IN"}:
                left = self._parse_special_binary(left, operator, precedence)
                continue
            if operator in {"BETWEEN", "NOT BETWEEN"}:
                left = self._parse_special_binary(left, operator, precedence)
                continue
            right = self.parse_expression(precedence + 1)
            left = Binary(operator, left, right)
        return left

    def _parse_special_binary(self, left: Expr, operator: str, precedence: int) -> Expr:
        if operator in {"IN", "NOT IN"}:
            if not self.match("("):
                raise ParseError(f"{operator} requires a parenthesized value list")
            inner: list[Token] = []
            depth = 1
            while depth and self.current.kind != "EOF":
                token = self.advance()
                if token.value == "(":
                    depth += 1
                elif token.value == ")":
                    depth -= 1
                    if depth == 0:
                        break
                inner.append(token)
            if depth:
                raise ParseError(f"{operator} list is missing ')'")
            options = _split_expression_tokens(inner)
            if not options:
                raise ParseError(f"{operator} requires at least one value")
            return InList(left, tuple(parse_expression_tokens(option) for option in options),
                          operator == "NOT IN")
        if operator in {"LIKE", "NOT LIKE"}:
            return Binary(operator, left, self.parse_expression(precedence + 1))
        lower = self.parse_expression(precedence + 1)
        if not self.match("AND"):
            raise ParseError(f"{operator} requires a lower and upper bound")
        upper = self.parse_expression(precedence + 1)
        return Between(left, lower, upper, operator == "NOT BETWEEN")

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
            if keyword == "CASE":
                branches: list[tuple[Expr, Expr]] = []
                while self.match("WHEN"):
                    condition = self.parse_expression(0)
                    if not self.match("THEN"):
                        raise ParseError("CASE WHEN requires THEN")
                    branches.append((condition, self.parse_expression(0)))
                if not branches:
                    raise ParseError("CASE requires at least one WHEN branch")
                else_expr = Literal(None)
                if self.match("ELSE"):
                    else_expr = self.parse_expression(0)
                if not self.match("END"):
                    raise ParseError("CASE expression requires END")
                return Case(tuple(branches), else_expr)
            if self.current.value == "(":
                self.advance()
                if keyword == "CAST":
                    operand = self.parse_expression(0)
                    if not self.match("AS") or self.current.kind != "IDENT":
                        raise ParseError("CAST requires an expression and a target type")
                    type_name = str(self.advance().value)
                    if not self.match(")"):
                        raise ParseError("CAST requires a closing ')'")
                    return Cast(operand, type_name)
                args: list[Expr] = []
                distinct = False
                if self.match("DISTINCT"):
                    distinct = True
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
                return Call(str(token.value), tuple(args), distinct)
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


def _split_expression_tokens(tokens: list[Token]) -> list[list[Token]]:
    parts: list[list[Token]] = []
    current: list[Token] = []
    depth = 0
    for token in tokens:
        if token.value == "(":
            depth += 1
        elif token.value == ")":
            depth -= 1
        if token.value == "," and depth == 0:
            if not current:
                raise ParseError("Empty expression between separators")
            parts.append(current)
            current = []
        else:
            current.append(token)
    if current:
        parts.append(current)
    elif parts:
        raise ParseError("Trailing separator in expression list")
    return parts


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
    if isinstance(expr, InList):
        value = evaluate(expr.value, row, rows, aliases)
        if value is None:
            return None
        found_null = False
        for option in expr.options:
            candidate = evaluate(option, row, rows, aliases)
            if candidate is None:
                found_null = True
            elif value == candidate:
                return False if expr.negated else True
        if found_null:
            return None
        return True if expr.negated else False
    if isinstance(expr, Between):
        value = evaluate(expr.value, row, rows, aliases)
        lower = evaluate(expr.lower, row, rows, aliases)
        upper = evaluate(expr.upper, row, rows, aliases)
        if value is None or lower is None or upper is None:
            return None
        result = lower <= value <= upper
        return not result if expr.negated else result
    if isinstance(expr, Case):
        for condition, result in expr.branches:
            if evaluate(condition, row, rows, aliases) is True:
                return evaluate(result, row, rows, aliases)
        return evaluate(expr.else_expr, row, rows, aliases)
    if isinstance(expr, Cast):
        value = evaluate(expr.operand, row, rows, aliases)
        if value is None:
            return None
        target = expr.type_name.upper()
        try:
            if target in {"INT", "INTEGER"}:
                return int(value)
            if target in {"FLOAT", "REAL", "DOUBLE"}:
                return float(value)
            if target in {"BOOL", "BOOLEAN"}:
                if isinstance(value, str):
                    return value.strip().lower() in {"1", "true", "yes", "on"}
                return bool(value)
            if target in {"TEXT", "STRING", "VARCHAR"}:
                return str(value)
        except (TypeError, ValueError) as exc:
            raise ExecutionError(f"Could not CAST {value!r} AS {expr.type_name}") from exc
        raise ExecutionError(f"Unsupported CAST type {expr.type_name!r}")
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
        if op == "NOT LIKE":
            result = _like(left, right)
            return None if result is None else not result
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
        if op == "||":
            return str(left) + str(right)
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
                    values = [True] * len(rows)
                else:
                    values = [evaluate(expr.args[0], item) for item in rows]
                values = [value for value in values if value is not None]
                if expr.distinct:
                    values = _unique_values(values)
                return len(values)
            values = [evaluate(expr.args[0], item) for item in rows] if expr.args else []
            values = [value for value in values if value is not None]
            if expr.distinct:
                values = _unique_values(values)
            if not values:
                return None
            if name == "SUM":
                return sum(values)
            if name == "AVG":
                return sum(values) / len(values)
            if name == "MIN":
                return min(values)
            return max(values)
        if expr.distinct:
            raise ExecutionError(f"DISTINCT is only supported by aggregate functions, not {name}()")
        values = [evaluate(arg, row, rows, aliases) for arg in expr.args]
        if name == "COALESCE":
            return next((value for value in values if value is not None), None)
        if name == "LOWER":
            if len(values) != 1:
                raise ExecutionError("LOWER() requires one argument")
            return None if values[0] is None else str(values[0]).lower()
        if name == "UPPER":
            if len(values) != 1:
                raise ExecutionError("UPPER() requires one argument")
            return None if values[0] is None else str(values[0]).upper()
        if name == "ABS":
            if len(values) != 1:
                raise ExecutionError("ABS() requires one argument")
            return None if values[0] is None else abs(values[0])
        if name == "LENGTH":
            if len(values) != 1:
                raise ExecutionError("LENGTH() requires one argument")
            return None if values[0] is None else len(str(values[0]))
        if name == "ROUND":
            if not values or len(values) > 2:
                raise ExecutionError("ROUND() requires one or two arguments")
            if values[0] is None:
                return None
            digits = int(values[1]) if len(values) > 1 else 0
            return round(values[0], digits)
        if name == "NULLIF":
            if len(values) != 2:
                raise ExecutionError("NULLIF() requires two arguments")
            return None if values[0] == values[1] else values[0]
        if name == "TRIM":
            if len(values) != 1:
                raise ExecutionError("TRIM() requires one argument")
            return None if values[0] is None else str(values[0]).strip()
        if name == "CONCAT":
            return "".join(str(value) for value in values if value is not None)
        raise ExecutionError(f"Unsupported function {expr.name!r}")
    raise ExecutionError(f"Unsupported expression node {type(expr).__name__}")


def contains_aggregate(expr: Expr | None) -> bool:
    """Return True if an expression contains a group aggregate call."""
    if expr is None:
        return False
    if isinstance(expr, Call):
        return expr.name.upper() in {"COUNT", "SUM", "AVG", "MIN", "MAX"} \
            or any(contains_aggregate(arg) for arg in expr.args)
    if isinstance(expr, InList):
        return contains_aggregate(expr.value) or any(contains_aggregate(arg) for arg in expr.options)
    if isinstance(expr, Between):
        return contains_aggregate(expr.value) or contains_aggregate(expr.lower) \
            or contains_aggregate(expr.upper)
    if isinstance(expr, Case):
        return any(contains_aggregate(condition) or contains_aggregate(result)
                   for condition, result in expr.branches) \
            or contains_aggregate(expr.else_expr)
    if isinstance(expr, Cast):
        return contains_aggregate(expr.operand)
    if isinstance(expr, Unary):
        return contains_aggregate(expr.operand)
    if isinstance(expr, Binary):
        return contains_aggregate(expr.left) or contains_aggregate(expr.right)
    return False


def _unique_values(values: list[Any]) -> list[Any]:
    unique: list[Any] = []
    for value in values:
        if not any(value == existing for existing in unique):
            unique.append(value)
    return unique


def expression_name(expr: Expr) -> str:
    """Produce a readable fallback column name for SELECT without AS."""
    if isinstance(expr, Identifier):
        return expr.name.split(".")[-1]
    if isinstance(expr, Call):
        return f"{expr.name.lower()}"
    if isinstance(expr, Case):
        return "case"
    if isinstance(expr, Cast):
        return "cast"
    if isinstance(expr, InList):
        return "in"
    if isinstance(expr, Between):
        return "between"
    if isinstance(expr, Wildcard):
        return "*"
    return "expression"


def format_string(value: str) -> str:
    """Render an expression string literal in canonical single-quoted SQL form."""
    escaped = value.replace("\\", "\\\\").replace("'", "''")
    return f"'{escaped}'"


def format_expr(expr: Expr, parent_precedence: int = 0) -> str:
    """Render an expression in the canonical pipeline form."""
    if isinstance(expr, Literal):
        if expr.value is None:
            return "NULL"
        if expr.value is True:
            return "TRUE"
        if expr.value is False:
            return "FALSE"
        if isinstance(expr.value, str):
            return format_string(expr.value)
        return str(expr.value)
    if isinstance(expr, Identifier):
        return expr.name
    if isinstance(expr, Wildcard):
        return "*"
    if isinstance(expr, Unary):
        operand = format_expr(expr.operand, 6)
        if expr.op == "NOT":
            return f"NOT {operand}"
        return f"{expr.op}{operand}"
    if isinstance(expr, Binary):
        precedence = _PRECEDENCE.get(expr.op, 0)
        left = format_expr(expr.left, precedence)
        right = format_expr(expr.right, precedence + 1)
        text = f"{left} {expr.op} {right}"
        if precedence < parent_precedence:
            return f"({text})"
        return text
    if isinstance(expr, Call):
        distinct = "DISTINCT " if expr.distinct else ""
        args = ", ".join(format_expr(arg) for arg in expr.args)
        return f"{expr.name}({distinct}{args})"
    if isinstance(expr, InList):
        options = ", ".join(format_expr(option) for option in expr.options)
        keyword = "NOT IN" if expr.negated else "IN"
        text = f"{format_expr(expr.value, 3)} {keyword} ({options})"
        if parent_precedence > 3:
            return f"({text})"
        return text
    if isinstance(expr, Between):
        keyword = "NOT BETWEEN" if expr.negated else "BETWEEN"
        text = (f"{format_expr(expr.value, 3)} {keyword} "
                f"{format_expr(expr.lower, 4)} AND {format_expr(expr.upper, 4)}")
        if parent_precedence > 3:
            return f"({text})"
        return text
    if isinstance(expr, Case):
        branches = " ".join(f"WHEN {format_expr(condition)} THEN {format_expr(result)}"
                            for condition, result in expr.branches)
        return f"CASE {branches} ELSE {format_expr(expr.else_expr)} END"
    if isinstance(expr, Cast):
        return f"CAST({format_expr(expr.operand)} AS {expr.type_name})"
    raise ExecutionError(f"Unsupported expression node {type(expr).__name__}")
