"""POC pipeline front-end that lowers UNSeQueL pipeline syntax to the ordered Query AST.

The pipeline syntax is frozen as v0.1 for this proof of concept; see
docs/poc-syntax.md for the grammar and its prior-art credits.
"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import ParseError
from .expressions import (Call, Case, Cast, Between, Binary, Expr, Identifier, InList,
                          Literal, Unary, Wildcard, parse_expression_tokens)
from .lexer import Token, tokenize
from .parser import FromSpec, JoinSpec, OrderItem, Query, SelectItem, _parse_table_ref, _split_top_level

_AGGREGATES = {"COUNT", "SUM", "AVG", "MIN", "MAX"}
_POST_SELECT_STAGES = {"SORT", "TAKE", "SKIP", "DISTINCT"}


@dataclass(frozen=True)
class _Stage:
    kind: str
    tokens: list[Token]


def _line_number(text: str, position: int) -> int:
    return text.count("\n", 0, position)


def _split_stages(text: str) -> list[_Stage]:
    tokens = tokenize(text)[:-1]
    while tokens and tokens[-1].value == ";":
        tokens.pop()
    stages: list[_Stage] = []
    current: list[Token] = []
    current_line = None

    def flush() -> None:
        nonlocal current
        parts = _split_pipes(current)
        for part in parts:
            stages.append(_make_stage(part))
        current = []

    for token in tokens:
        line = _line_number(text, token.position)
        if current_line is None:
            current_line = line
        if line != current_line and current:
            flush()
            current_line = line
        current.append(token)
    if current:
        flush()
    return stages


def _split_pipes(tokens: list[Token]) -> list[list[Token]]:
    parts: list[list[Token]] = []
    current: list[Token] = []
    depth = 0
    for token in tokens:
        if token.value == "(":
            depth += 1
        elif token.value == ")":
            depth -= 1
        if token.value == "|" and depth == 0:
            if current:
                parts.append(current)
                current = []
            continue
        current.append(token)
    if current:
        parts.append(current)
    return [part for part in parts if part]


def _make_stage(tokens: list[Token]) -> _Stage:
    if not tokens or tokens[0].kind != "IDENT":
        raise ParseError("Each pipeline stage must start with a stage keyword")
    keyword = str(tokens[0].value).upper()
    rest = tokens[1:]
    if keyword == "LEFT":
        if not rest or str(rest[0].value).upper() != "JOIN":
            raise ParseError("LEFT must be followed by JOIN")
        return _Stage("LEFT JOIN", rest[1:])
    return _Stage(keyword, rest)


def _inline(expr: Expr, names: dict[str, Expr]) -> Expr:
    if isinstance(expr, Identifier):
        if expr.name in names:
            return names[expr.name]
        return expr
    if isinstance(expr, (Literal, Wildcard)):
        return expr
    if isinstance(expr, Unary):
        return Unary(expr.op, _inline(expr.operand, names))
    if isinstance(expr, Binary):
        return Binary(expr.op, _inline(expr.left, names), _inline(expr.right, names))
    if isinstance(expr, Call):
        return Call(expr.name, tuple(_inline(arg, names) for arg in expr.args), expr.distinct)
    if isinstance(expr, InList):
        return InList(_inline(expr.value, names),
                      tuple(_inline(option, names) for option in expr.options), expr.negated)
    if isinstance(expr, Between):
        return Between(_inline(expr.value, names), _inline(expr.lower, names),
                       _inline(expr.upper, names), expr.negated)
    if isinstance(expr, Case):
        branches = tuple((_inline(condition, names), _inline(result, names))
                         for condition, result in expr.branches)
        return Case(branches, _inline(expr.else_expr, names))
    if isinstance(expr, Cast):
        return Cast(_inline(expr.operand, names), expr.type_name)
    return expr


def _combine(left: Expr | None, right: Expr) -> Expr:
    return right if left is None else Binary("AND", left, right)


def _parse_named(part: list[Token], what: str) -> tuple[str, Expr]:
    depth = 0
    for i, token in enumerate(part):
        if token.value == "(":
            depth += 1
        elif token.value == ")":
            depth -= 1
        elif depth == 0 and token.value == "=" and 0 < i < len(part) - 1:
            name_token = part[i - 1]
            if name_token.kind != "IDENT":
                raise ParseError(f"The name before '=' in {what} must be an identifier")
            if i - 1 != 0:
                raise ParseError(f"{what} must use the form name = expression")
            return str(name_token.value), parse_expression_tokens(part[i + 1:])
    raise ParseError(f"{what} entries must be named with the form name = expression")


def _parse_bare_or_named(part: list[Token], what: str) -> tuple[str | None, Expr]:
    if len(part) == 1 and part[0].kind == "IDENT":
        return str(part[0].value), Identifier(str(part[0].value))
    depth = 0
    for i, token in enumerate(part):
        if token.value == "(":
            depth += 1
        elif token.value == ")":
            depth -= 1
        elif depth == 0 and token.value == "=" and 0 < i < len(part) - 1:
            name_token = part[i - 1]
            if name_token.kind != "IDENT" or i - 1 != 0:
                raise ParseError(f"{what} names must be a single identifier before '='")
            return str(name_token.value), parse_expression_tokens(part[i + 1:])
    return None, parse_expression_tokens(part)


def _output_name(name: str | None, expr: Expr, what: str) -> str:
    if name is not None:
        return name
    if isinstance(expr, Identifier):
        return expr.name.split(".")[-1]
    raise ParseError(f"Computed {what} entries must be named with the form name = expression")


def _parse_aggregate(part: list[Token]) -> tuple[str, Call]:
    name, expr = _parse_bare_or_named(part, "aggregate")
    if not isinstance(expr, Call) or str(expr.name).upper() not in _AGGREGATES:
        raise ParseError("Only COUNT, SUM, AVG, MIN, and MAX calls are allowed in group")
    if name is None:
        if len(expr.args) == 1 and isinstance(expr.args[0], Wildcard) \
                and str(expr.name).upper() == "COUNT":
            name = "count"
        else:
            raise ParseError("Aggregates must be named with the form name = AGG(expression)")
    return name, expr


class _PipelineBuilder:
    def __init__(self):
        self.source: FromSpec | None = None
        self.joins: list[JoinSpec] = []
        self.where: Expr | None = None
        self.having: Expr | None = None
        self.group_keys: list[tuple[str, Expr]] = []
        self.aggregates: list[tuple[str, Call]] = []
        self.derives: dict[str, Expr] = {}
        self.post_derives: list[tuple[str, Expr]] = []
        self.final_select: list[SelectItem] | None = None
        self.order_by: list[OrderItem] = []
        self.limit: int | None = None
        self.offset: int = 0
        self.distinct = False
        self.grouped = False
        self.projected = False

    @property
    def row_names(self) -> dict[str, Expr]:
        return dict(self.derives)

    @property
    def group_names(self) -> dict[str, Expr]:
        names = {name: expr for name, expr in self.group_keys}
        names.update({name: expr for name, expr in self.aggregates})
        names.update({name: expr for name, expr in self.post_derives})
        return names

    def require_from(self, stage: str) -> None:
        if self.source is None:
            raise ParseError(f"The pipeline must start with from; {stage} came first")

    def require_not_grouped(self, stage: str) -> None:
        if self.grouped:
            raise ParseError(f"{stage} cannot appear after group")

    def require_not_projected(self, stage: str) -> None:
        if self.projected:
            raise ParseError(f"{stage} cannot appear after select in the POC pipeline grammar")

    def add_from(self, tokens: list[Token]) -> None:
        if self.source is not None:
            raise ParseError("from may appear only once in a pipeline")
        self.source = _parse_table_ref(tokens)

    def add_join(self, tokens: list[Token], kind: str) -> None:
        self.require_from("join")
        self.require_not_grouped("join")
        self.require_not_projected("join")
        depth = 0
        on_index = None
        for i, token in enumerate(tokens):
            if token.value == "(":
                depth += 1
            elif token.value == ")":
                depth -= 1
            elif depth == 0 and str(token.value).upper() == "ON":
                on_index = i
                break
        if on_index is None:
            raise ParseError("join requires an ON condition")
        table = _parse_table_ref(tokens[:on_index])
        condition = _inline(parse_expression_tokens(tokens[on_index + 1:]), self.row_names)
        self.joins.append(JoinSpec(table.name, table.alias, kind, condition, table.subquery))

    def add_where(self, tokens: list[Token]) -> None:
        self.require_from("where")
        self.require_not_projected("where")
        expr = _inline(parse_expression_tokens(tokens), self.row_names)
        if self.grouped:
            self.having = _combine(self.having, _inline(expr, self.group_names))
        else:
            self.where = _combine(self.where, expr)

    def add_derive(self, tokens: list[Token]) -> None:
        self.require_from("derive")
        self.require_not_projected("derive")
        for part in _split_top_level(tokens):
            name, expr = _parse_named(part, "derive")
            if self.grouped:
                self.post_derives.append((name, _inline(expr, self.group_names)))
            else:
                self.derives[name] = _inline(expr, self.derives)

    def add_group(self, tokens: list[Token]) -> None:
        self.require_from("group")
        if self.grouped:
            raise ParseError("group may appear only once in a pipeline")
        open_index = next((i for i, token in enumerate(tokens) if token.value == "("), None)
        if open_index is None:
            raise ParseError("group requires an aggregate list in parentheses")
        if tokens[-1].value != ")":
            raise ParseError("group must end with a closing ')'")
        key_tokens = tokens[:open_index]
        aggregate_tokens = tokens[open_index + 1:-1]
        for part in _split_top_level(key_tokens):
            name, expr = _parse_bare_or_named(part, "group key")
            output = _output_name(name, expr, "group key")
            self.group_keys.append((output, _inline(expr, self.derives)))
        if not self.group_keys:
            raise ParseError("group requires at least one key expression")
        for part in _split_top_level(aggregate_tokens):
            name, expr = _parse_aggregate(part)
            self.aggregates.append((name, _inline(expr, self.derives)))
        self.grouped = True

    def add_select(self, tokens: list[Token]) -> None:
        self.require_from("select")
        if self.final_select is not None:
            raise ParseError("select may appear only once in a pipeline")
        self.projected = True
        if self.grouped:
            known = set(self.group_names)
            items: list[SelectItem] = []
            for part in _split_top_level(tokens):
                if len(part) == 1 and part[0].value == "*":
                    items.extend(self._default_items())
                    continue
                name, expr = _parse_bare_or_named(part, "select")
                if isinstance(expr, Call) and str(expr.name).upper() in _AGGREGATES:
                    raise ParseError("Aggregates belong in the group stage, not select")
                if name is None and isinstance(expr, Identifier) and expr.name in known:
                    items.append(SelectItem(Identifier(expr.name)))
                elif name is not None:
                    items.append(SelectItem(expr, name))
                else:
                    items.append(SelectItem(expr))
            self.final_select = items
        else:
            items = []
            for part in _split_top_level(tokens):
                if len(part) == 1 and part[0].value == "*":
                    items.append(SelectItem(Wildcard()))
                    continue
                name, expr = _parse_bare_or_named(part, "select")
                if isinstance(expr, Call) and str(expr.name).upper() in _AGGREGATES:
                    raise ParseError("Aggregates belong in a group stage; there is no group yet")
                alias = name if name is not None else None
                items.append(SelectItem(_inline(expr, self.derives), alias))
            self.final_select = items

    def add_sort(self, tokens: list[Token]) -> None:
        self.require_from("sort")
        for part in _split_top_level(tokens):
            descending = False
            if part and part[0].value == "-" and len(part) > 1:
                descending = True
                part = part[1:]
            if part and str(part[-1].value).upper() in {"ASC", "DESC"}:
                descending = str(part[-1].value).upper() == "DESC"
                part = part[:-1]
            expr = _inline(parse_expression_tokens(part),
                           {**self.row_names, **self.group_names})
            self.order_by.append(OrderItem(expr, descending))

    def _parse_count(self, tokens: list[Token], stage: str) -> int:
        if len(tokens) != 1 or tokens[0].kind != "NUMBER":
            raise ParseError(f"{stage} requires one integer literal")
        value = tokens[0].value
        if not isinstance(value, int) or value < 0:
            raise ParseError(f"{stage} requires a non-negative integer")
        return value

    def add_take(self, tokens: list[Token]) -> None:
        self.require_from("take")
        if self.limit is not None:
            raise ParseError("take may appear only once in a pipeline")
        self.limit = self._parse_count(tokens, "take")

    def add_skip(self, tokens: list[Token]) -> None:
        self.require_from("skip")
        if self.offset:
            raise ParseError("skip may appear only once in a pipeline")
        self.offset = self._parse_count(tokens, "skip")

    def add_distinct(self, tokens: list[Token]) -> None:
        self.require_from("distinct")
        if tokens:
            raise ParseError("distinct takes no arguments")
        if self.distinct:
            raise ParseError("distinct may appear only once in a pipeline")
        self.distinct = True

    def _default_items(self) -> list[SelectItem]:
        if self.grouped:
            items = [SelectItem(expr, name) for name, expr in self.group_keys]
            items.extend(SelectItem(expr, name) for name, expr in self.aggregates)
            items.extend(SelectItem(expr, name) for name, expr in self.post_derives)
            return items
        if self.post_derives:
            return [SelectItem(expr, name) for name, expr in self.post_derives]
        if self.derives:
            return [SelectItem(Identifier(name), name) for name in self.derives]
        return [SelectItem(Wildcard())]

    def build(self) -> Query:
        if self.source is None:
            raise ParseError("A pipeline must start with a from stage")
        if self.final_select is not None:
            select = self.final_select
        else:
            select = self._default_items()
        if not select:
            raise ParseError("The pipeline produces no output columns")
        query = Query(
            source=self.source,
            joins=self.joins,
            where=self.where,
            group_by=[expr for _, expr in self.group_keys],
            having=self.having,
            select=select,
            order_by=self.order_by,
            limit=self.limit,
            offset=self.offset,
            distinct=self.distinct,
        )
        return query


def parse_pipeline(text: str) -> Query:
    """Parse the frozen v0.1 pipeline syntax into the ordered-clause Query AST."""
    builder = _PipelineBuilder()
    for stage in _split_stages(text):
        kind = stage.kind
        if kind == "FROM":
            builder.add_from(stage.tokens)
        elif kind in {"JOIN", "LEFT JOIN"}:
            builder.add_join(stage.tokens, "inner" if kind == "JOIN" else "left")
        elif kind == "WHERE":
            builder.add_where(stage.tokens)
        elif kind == "DERIVE":
            builder.add_derive(stage.tokens)
        elif kind == "GROUP":
            builder.add_group(stage.tokens)
        elif kind == "SELECT":
            builder.add_select(stage.tokens)
        elif kind == "SORT":
            builder.add_sort(stage.tokens)
        elif kind == "TAKE":
            builder.add_take(stage.tokens)
        elif kind == "SKIP":
            builder.add_skip(stage.tokens)
        elif kind == "DISTINCT":
            builder.add_distinct(stage.tokens)
        else:
            raise ParseError(f"Unknown pipeline stage {kind.lower()}")
    return builder.build()
