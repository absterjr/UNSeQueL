from __future__ import annotations

from dataclasses import dataclass, field

from .errors import ParseError
from .expressions import Expr, expression_name, parse_expression_tokens
from .lexer import Token, tokenize


@dataclass(frozen=True)
class FromSpec:
    name: str
    alias: str | None = None


@dataclass(frozen=True)
class JoinSpec:
    name: str
    alias: str | None
    kind: str
    condition: Expr


@dataclass(frozen=True)
class SelectItem:
    expression: Expr
    alias: str | None = None

    @property
    def output_name(self) -> str:
        return self.alias or expression_name(self.expression)


@dataclass(frozen=True)
class OrderItem:
    expression: Expr
    descending: bool = False


@dataclass
class Query:
    source: FromSpec
    joins: list[JoinSpec] = field(default_factory=list)
    where: Expr | None = None
    group_by: list[Expr] = field(default_factory=list)
    having: Expr | None = None
    select: list[SelectItem] = field(default_factory=list)
    order_by: list[OrderItem] = field(default_factory=list)
    limit: int | None = None


@dataclass(frozen=True)
class _Clause:
    kind: str
    tokens: list[Token]


def _upper(token: Token) -> str:
    return str(token.value).upper()


def _split_top_level(tokens: list[Token], separator: str = ",") -> list[list[Token]]:
    parts: list[list[Token]] = []
    current: list[Token] = []
    depth = 0
    for token in tokens:
        if token.value == "(":
            depth += 1
        elif token.value == ")":
            depth -= 1
        if token.value == separator and depth == 0:
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


def _clause_starts(tokens: list[Token]) -> list[tuple[int, str, int]]:
    """Find line-independent clause headers at parenthesis depth zero."""
    starts: list[tuple[int, str, int]] = []
    depth = 0
    i = 0
    while i < len(tokens):
        value = _upper(tokens[i])
        if tokens[i].value == "(":
            depth += 1
            i += 1
            continue
        if tokens[i].value == ")":
            depth -= 1
            i += 1
            continue
        if depth == 0:
            if value in {"FROM", "WHERE", "HAVING", "SELECT", "LIMIT", "JOIN"}:
                starts.append((i, value, 1))
                i += 1
                continue
            elif value in {"LEFT", "INNER"} and i + 1 < len(tokens) and _upper(tokens[i + 1]) == "JOIN":
                starts.append((i, f"{value} JOIN", 2))
                i += 2
                continue
            elif value in {"GROUP", "ORDER"} and i + 1 < len(tokens) and _upper(tokens[i + 1]) == "BY":
                starts.append((i, f"{value} BY", 2))
                i += 2
                continue
        i += 1
    return starts


def _make_clauses(tokens: list[Token]) -> list[_Clause]:
    starts = _clause_starts(tokens)
    if not starts or starts[0][1] != "FROM":
        raise ParseError("A UNSeQueL query must start with FROM")
    clauses: list[_Clause] = []
    for index, (start, kind, header_length) in enumerate(starts):
        end = starts[index + 1][0] if index + 1 < len(starts) else len(tokens)
        clauses.append(_Clause(kind, tokens[start + header_length:end]))
    return clauses


def _parse_table_ref(tokens: list[Token]) -> FromSpec:
    if not tokens or tokens[0].kind != "IDENT":
        raise ParseError("Expected a table name after FROM or JOIN")
    name = str(tokens[0].value)
    rest = tokens[1:]
    alias: str | None = None
    if rest:
        if len(rest) == 2 and _upper(rest[0]) == "AS" and rest[1].kind == "IDENT":
            alias = str(rest[1].value)
        elif len(rest) == 1 and rest[0].kind == "IDENT":
            alias = str(rest[0].value)
        else:
            raise ParseError("Expected an optional alias after the table name")
    return FromSpec(name, alias)


def _parse_join(clause: _Clause) -> JoinSpec:
    on_index = next((i for i, token in enumerate(clause.tokens) if _upper(token) == "ON"), None)
    if on_index is None:
        raise ParseError("JOIN requires an ON condition")
    table = _parse_table_ref(clause.tokens[:on_index])
    condition = parse_expression_tokens(clause.tokens[on_index + 1:])
    kind = "left" if clause.kind == "LEFT JOIN" else "inner"
    return JoinSpec(table.name, table.alias, kind, condition)


def _parse_select(tokens: list[Token]) -> list[SelectItem]:
    items: list[SelectItem] = []
    for part in _split_top_level(tokens):
        depth = 0
        alias_index = None
        for i, token in enumerate(part):
            if token.value == "(":
                depth += 1
            elif token.value == ")":
                depth -= 1
            elif depth == 0 and _upper(token) == "AS":
                alias_index = i
                break
        alias = None
        expression_tokens = part
        if alias_index is not None:
            expression_tokens = part[:alias_index]
            alias_tokens = part[alias_index + 1:]
            if len(alias_tokens) != 1 or alias_tokens[0].kind != "IDENT":
                raise ParseError("SELECT AS must be followed by one alias name")
            alias = str(alias_tokens[0].value)
        items.append(SelectItem(parse_expression_tokens(expression_tokens), alias))
    if not items:
        raise ParseError("SELECT requires at least one expression")
    return items


def _parse_order(tokens: list[Token]) -> list[OrderItem]:
    items: list[OrderItem] = []
    for part in _split_top_level(tokens):
        descending = False
        if part and _upper(part[-1]) in {"ASC", "DESC"}:
            descending = _upper(part[-1]) == "DESC"
            part = part[:-1]
        items.append(OrderItem(parse_expression_tokens(part), descending))
    return items


def parse_query(text: str) -> Query:
    """Parse a full UNSeQueL query and validate its clause order."""
    tokens = tokenize(text)[:-1]
    while tokens and tokens[-1].value == ";":
        tokens.pop()
    clauses = _make_clauses(tokens)

    rank = {"FROM": 0, "JOIN": 0, "LEFT JOIN": 0, "INNER JOIN": 0,
            "WHERE": 1, "GROUP BY": 2, "HAVING": 3, "SELECT": 4,
            "ORDER BY": 5, "LIMIT": 6}
    previous = -1
    seen: set[str] = set()
    source: FromSpec | None = None
    query = Query(source=FromSpec(""))

    for clause in clauses:
        current_rank = rank[clause.kind]
        if current_rank < previous:
            raise ParseError(
                f"Clause {clause.kind} is out of order; UNSeQueL follows "
                "FROM -> WHERE -> GROUP BY -> HAVING -> SELECT -> ORDER BY -> LIMIT"
            )
        if clause.kind in seen and clause.kind == "FROM":
            raise ParseError("FROM may appear only once")
        if clause.kind not in {"JOIN", "LEFT JOIN", "INNER JOIN"} and clause.kind in seen:
            raise ParseError(f"Clause {clause.kind} may appear only once")
        if clause.kind not in {"JOIN", "LEFT JOIN", "INNER JOIN"}:
            seen.add(clause.kind)
        previous = current_rank

        if clause.kind == "FROM":
            source = _parse_table_ref(clause.tokens)
        elif clause.kind in {"JOIN", "LEFT JOIN", "INNER JOIN"}:
            query.joins.append(_parse_join(clause))
        elif clause.kind == "WHERE":
            query.where = parse_expression_tokens(clause.tokens)
        elif clause.kind == "GROUP BY":
            query.group_by = [parse_expression_tokens(part) for part in _split_top_level(clause.tokens)]
        elif clause.kind == "HAVING":
            query.having = parse_expression_tokens(clause.tokens)
        elif clause.kind == "SELECT":
            query.select = _parse_select(clause.tokens)
        elif clause.kind == "ORDER BY":
            query.order_by = _parse_order(clause.tokens)
        elif clause.kind == "LIMIT":
            if len(clause.tokens) != 1 or clause.tokens[0].kind != "NUMBER":
                raise ParseError("LIMIT requires one integer literal")
            value = clause.tokens[0].value
            if not isinstance(value, int) or value < 0:
                raise ParseError("LIMIT requires a non-negative integer")
            query.limit = value

    if source is None:
        raise ParseError("FROM is required")
    if not query.select:
        raise ParseError("SELECT is required")
    query.source = source
    return query
