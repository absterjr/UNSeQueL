from __future__ import annotations

from dataclasses import dataclass, field

from .errors import ParseError
from .expressions import Expr, Literal, contains_aggregate, expression_name, parse_expression_tokens
from .lexer import Token, tokenize


@dataclass(frozen=True)
class FromSpec:
    name: str | None
    alias: str | None = None
    subquery: Query | None = None

    @property
    def display_name(self) -> str:
        return self.alias or self.name or "subquery"


@dataclass(frozen=True)
class JoinSpec:
    name: str | None
    alias: str | None
    kind: str
    condition: Expr
    subquery: Query | None = None


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


@dataclass(frozen=True)
class SetOperation:
    kind: str
    query: Query


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
    offset: int = 0
    distinct: bool = False
    ctes: dict[str, Query] = field(default_factory=dict)
    set_operations: list[SetOperation] = field(default_factory=list)


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
            if value in {"FROM", "WHERE", "HAVING", "SELECT", "LIMIT", "OFFSET", "JOIN"}:
                starts.append((i, value, 1))
                i += 1
                continue
            elif value in {"LEFT", "INNER", "RIGHT", "FULL", "CROSS"} \
                    and i + 1 < len(tokens) and _upper(tokens[i + 1]) == "JOIN":
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


def _matching_parenthesis(tokens: list[Token], start: int) -> int:
    if start >= len(tokens) or tokens[start].value != "(":
        raise ParseError("Expected '('")
    depth = 0
    for index in range(start, len(tokens)):
        if tokens[index].value == "(":
            depth += 1
        elif tokens[index].value == ")":
            depth -= 1
            if depth == 0:
                return index
    raise ParseError("Unclosed parenthesized query")


def _parse_table_ref(tokens: list[Token]) -> FromSpec:
    if not tokens or tokens[0].kind != "IDENT":
        if tokens and tokens[0].value == "(":
            close = _matching_parenthesis(tokens, 0)
            if close == 1:
                raise ParseError("Expected a query inside FROM parentheses")
            inner = _parse_query_tokens(tokens[1:close])
            rest = tokens[close + 1:]
            alias: str | None = None
            if len(rest) == 2 and _upper(rest[0]) == "AS" and rest[1].kind == "IDENT":
                alias = str(rest[1].value)
            elif len(rest) == 1 and rest[0].kind == "IDENT":
                alias = str(rest[0].value)
            else:
                raise ParseError("A subquery in FROM requires one alias")
            return FromSpec(None, alias, inner)
        raise ParseError("Expected a table name or subquery after FROM or JOIN")
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
    depth = 0
    on_index = None
    for i, token in enumerate(clause.tokens):
        if token.value == "(":
            depth += 1
        elif token.value == ")":
            depth -= 1
        elif depth == 0 and _upper(token) == "ON":
            on_index = i
            break
    if on_index is None and clause.kind != "CROSS JOIN":
        raise ParseError("JOIN requires an ON condition")
    table_tokens = clause.tokens if on_index is None else clause.tokens[:on_index]
    table = _parse_table_ref(table_tokens)
    condition = Literal(True) if on_index is None else parse_expression_tokens(clause.tokens[on_index + 1:])
    if contains_aggregate(condition):
        raise ParseError("Aggregate functions belong in HAVING or SELECT, not JOIN")
    kind = {
        "LEFT JOIN": "left",
        "RIGHT JOIN": "right",
        "FULL JOIN": "full",
        "CROSS JOIN": "cross",
    }.get(clause.kind, "inner")
    return JoinSpec(table.name, table.alias, kind, condition, table.subquery)


def _parse_select(tokens: list[Token]) -> tuple[list[SelectItem], bool]:
    distinct = bool(tokens and _upper(tokens[0]) == "DISTINCT")
    if distinct:
        tokens = tokens[1:]
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
    return items, distinct


def _parse_order(tokens: list[Token]) -> list[OrderItem]:
    items: list[OrderItem] = []
    for part in _split_top_level(tokens):
        descending = False
        if part and _upper(part[-1]) in {"ASC", "DESC"}:
            descending = _upper(part[-1]) == "DESC"
            part = part[:-1]
        items.append(OrderItem(parse_expression_tokens(part), descending))
    return items


def _parse_integer(tokens: list[Token], label: str) -> int:
    if len(tokens) != 1 or tokens[0].kind != "NUMBER":
        raise ParseError(f"{label} requires one integer literal")
    value = tokens[0].value
    if not isinstance(value, int) or value < 0:
        raise ParseError(f"{label} requires a non-negative integer")
    return value


def _split_set_operations(tokens: list[Token]) -> tuple[list[list[Token]], list[str]]:
    segments: list[list[Token]] = []
    operators: list[str] = []
    start = 0
    depth = 0
    i = 0
    while i < len(tokens):
        if tokens[i].value == "(":
            depth += 1
        elif tokens[i].value == ")":
            depth -= 1
        elif depth == 0 and _upper(tokens[i]) in {"UNION", "INTERSECT", "EXCEPT"}:
            if i == start:
                raise ParseError(f"{_upper(tokens[i])} requires a query on both sides")
            segments.append(tokens[start:i])
            operator = _upper(tokens[i])
            if i + 1 < len(tokens) and _upper(tokens[i + 1]) in {"ALL", "DISTINCT"}:
                modifier = _upper(tokens[i + 1])
                if operator != "UNION" and modifier == "DISTINCT":
                    raise ParseError(f"{operator} does not accept DISTINCT")
                operators.append(f"{operator} {modifier}")
                i += 1
            else:
                operators.append(operator)
            start = i + 1
        i += 1
    if start == len(tokens):
        raise ParseError("Set operation requires a query on both sides")
    segments.append(tokens[start:])
    return segments, operators


def _parse_ctes(tokens: list[Token]) -> tuple[dict[str, Query], list[Token]]:
    if not tokens or _upper(tokens[0]) != "WITH":
        return {}, tokens
    index = 1
    if index < len(tokens) and _upper(tokens[index]) == "RECURSIVE":
        raise ParseError("WITH RECURSIVE is not supported yet")
    ctes: dict[str, Query] = {}
    while True:
        if index >= len(tokens) or tokens[index].kind != "IDENT":
            raise ParseError("WITH requires a CTE name")
        name = str(tokens[index].value)
        index += 1
        if index >= len(tokens) or _upper(tokens[index]) != "AS":
            raise ParseError("A CTE must use the form name AS (query)")
        index += 1
        close = _matching_parenthesis(tokens, index)
        key = name.lower()
        if key in ctes:
            raise ParseError(f"CTE {name!r} is defined more than once")
        ctes[key] = _parse_query_tokens(tokens[index + 1:close])
        index = close + 1
        if index >= len(tokens) or tokens[index].value != ",":
            break
        index += 1
    if index >= len(tokens):
        raise ParseError("WITH must be followed by a query")
    return ctes, tokens[index:]


def _parse_single_query(tokens: list[Token]) -> Query:
    clauses = _make_clauses(tokens)
    rank = {"FROM": 0, "JOIN": 0, "LEFT JOIN": 0, "INNER JOIN": 0,
            "RIGHT JOIN": 0, "FULL JOIN": 0, "CROSS JOIN": 0,
            "WHERE": 1, "GROUP BY": 2, "HAVING": 3, "SELECT": 4,
            "ORDER BY": 5, "LIMIT": 6, "OFFSET": 7}
    previous = -1
    seen: set[str] = set()
    source: FromSpec | None = None
    query = Query(source=FromSpec(None))

    for clause in clauses:
        current_rank = rank[clause.kind]
        if current_rank < previous:
            raise ParseError(
                f"Clause {clause.kind} is out of order; UNSeQueL follows "
                "FROM -> WHERE -> GROUP BY -> HAVING -> SELECT -> ORDER BY -> LIMIT -> OFFSET"
            )
        if clause.kind in seen and clause.kind == "FROM":
            raise ParseError("FROM may appear only once")
        join_kinds = {"JOIN", "LEFT JOIN", "INNER JOIN", "RIGHT JOIN", "FULL JOIN", "CROSS JOIN"}
        if clause.kind not in join_kinds and clause.kind in seen:
            raise ParseError(f"Clause {clause.kind} may appear only once")
        if clause.kind not in join_kinds:
            seen.add(clause.kind)
        previous = current_rank

        if clause.kind == "FROM":
            source = _parse_table_ref(clause.tokens)
        elif clause.kind in join_kinds:
            query.joins.append(_parse_join(clause))
        elif clause.kind == "WHERE":
            if contains_aggregate(parse_expression_tokens(clause.tokens)):
                raise ParseError("Aggregate functions belong in HAVING or SELECT, not WHERE")
            query.where = parse_expression_tokens(clause.tokens)
        elif clause.kind == "GROUP BY":
            query.group_by = [parse_expression_tokens(part) for part in _split_top_level(clause.tokens)]
            if any(contains_aggregate(expr) for expr in query.group_by):
                raise ParseError("GROUP BY expressions cannot contain aggregate functions")
        elif clause.kind == "HAVING":
            query.having = parse_expression_tokens(clause.tokens)
        elif clause.kind == "SELECT":
            query.select, query.distinct = _parse_select(clause.tokens)
        elif clause.kind == "ORDER BY":
            query.order_by = _parse_order(clause.tokens)
        elif clause.kind == "LIMIT":
            query.limit = _parse_integer(clause.tokens, "LIMIT")
        elif clause.kind == "OFFSET":
            query.offset = _parse_integer(clause.tokens, "OFFSET")

    if source is None:
        raise ParseError("FROM is required")
    if not query.select:
        raise ParseError("SELECT is required")
    query.source = source
    return query


def _parse_query_tokens(tokens: list[Token]) -> Query:
    while tokens and tokens[-1].value == ";":
        tokens = tokens[:-1]
    if not tokens:
        raise ParseError("A query cannot be empty")
    ctes, body = _parse_ctes(tokens)
    segments, operators = _split_set_operations(body)
    queries = [_parse_single_query(segment) for segment in segments]
    for query in queries:
        query.ctes = dict(ctes)
    root = queries[0]
    root.set_operations = [SetOperation(operator, query)
                           for operator, query in zip(operators, queries[1:])]
    return root


def parse_query(text: str) -> Query:
    """Parse a full UNSeQueL query and validate its clause order."""
    tokens = tokenize(text)[:-1]
    while tokens and tokens[-1].value == ";":
        tokens.pop()
    return _parse_query_tokens(tokens)
