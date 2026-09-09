"""Stage-preserving intermediate representation for the pipeline language.

`parse_pipeline_stages` turns `.pusql` text into an ordered list of typed stage
nodes — one node per pipeline stage — with each node carrying the source line it
came from. This is the representation both backends consume:

* `unsequel.pipeline.lower_to_query` collapses it onto the ordered-clause
  `Query` AST for the in-memory engine.
* `unsequel.codegen` (Step 6) emits one SQL CTE per stage from it.

Every error raised here is a `PipelineError`, which names the offending stage
(index and keyword) and its line, so failures point at an exact place instead of
a generic "parse error".
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .errors import ParseError
from .expressions import Call, Expr, Identifier, Wildcard, parse_expression_tokens
from .lexer import Token, tokenize
from .parser import FromSpec, OrderItem, SelectItem, _parse_table_ref, _split_top_level

_AGGREGATES = {"COUNT", "SUM", "AVG", "MIN", "MAX"}
_POST_SELECT = {"sort", "take", "skip", "distinct"}
_RESERVED = {"sql", "window", "right", "full", "cross"}


class PipelineError(ParseError):
    """A pipeline query error located at a specific stage."""

    def __init__(self, message: str, *, index: int | None = None,
                 keyword: str | None = None, line: int | None = None):
        self.index = index
        self.keyword = keyword
        self.line = line
        if index is not None and keyword is not None:
            where = f"stage {index} ({keyword})"
            if line is not None:
                where += f", line {line}"
            super().__init__(f"{where}: {message}")
        else:
            super().__init__(message)


# --------------------------------------------------------------------------- #
# Stage nodes
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Stage:
    keyword: str
    line: int


@dataclass(frozen=True)
class From(Stage):
    source: FromSpec


@dataclass(frozen=True)
class Join(Stage):
    kind: str  # "inner" | "left"
    source: FromSpec
    on: Expr


@dataclass(frozen=True)
class Derive(Stage):
    items: tuple[tuple[str, Expr], ...]
    phase: str = "row"  # "row" before group, "group" after


@dataclass(frozen=True)
class Where(Stage):
    condition: Expr
    phase: str = "row"


@dataclass(frozen=True)
class Group(Stage):
    keys: tuple[tuple[str, Expr], ...]
    aggregates: tuple[tuple[str, Call], ...]


@dataclass(frozen=True)
class Select(Stage):
    items: tuple[SelectItem, ...]
    star: bool = False


@dataclass(frozen=True)
class Sort(Stage):
    keys: tuple[OrderItem, ...]


@dataclass(frozen=True)
class Take(Stage):
    count: int


@dataclass(frozen=True)
class Skip(Stage):
    count: int


@dataclass(frozen=True)
class Distinct(Stage):
    pass


@dataclass(frozen=True)
class RawSql(Stage):
    text: str


# --------------------------------------------------------------------------- #
# Stage splitting (newline / top-level "|")
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class _Raw:
    keyword: str
    tokens: list[Token]
    line: int


def _line_of(text: str, position: int) -> int:
    return text.count("\n", 0, position) + 1


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


def _split_raw_stages(text: str) -> list[_Raw]:
    tokens = [t for t in tokenize(text) if t.kind != "EOF"]
    while tokens and tokens[-1].value == ";":
        tokens.pop()

    grouped: list[list[Token]] = []
    current: list[Token] = []
    current_line: int | None = None
    for token in tokens:
        line = _line_of(text, token.position)
        if current_line is None:
            current_line = line
        if line != current_line and current:
            grouped.append(current)
            current = []
            current_line = line
        current.append(token)
    if current:
        grouped.append(current)

    raws: list[_Raw] = []
    for line_tokens in grouped:
        for part in _split_pipes(line_tokens):
            keyword, rest = _stage_keyword(part)
            raws.append(_Raw(keyword, rest, _line_of(text, part[0].position)))
    return raws


def _stage_keyword(tokens: list[Token]) -> tuple[str, list[Token]]:
    if not tokens or tokens[0].kind != "IDENT":
        raise PipelineError("every pipeline stage must start with a stage keyword")
    head = str(tokens[0].value).lower()
    if head == "left" and len(tokens) > 1 and str(tokens[1].value).lower() == "join":
        return "left join", tokens[2:]
    if head in {"right", "full", "cross"} and len(tokens) > 1 \
            and str(tokens[1].value).lower() == "join":
        return f"{head} join", tokens[2:]
    return head, tokens[1:]


# --------------------------------------------------------------------------- #
# Per-stage token parsing
# --------------------------------------------------------------------------- #

def _parse_named(part: list[Token], what: str) -> tuple[str, Expr]:
    depth = 0
    for i, token in enumerate(part):
        if token.value == "(":
            depth += 1
        elif token.value == ")":
            depth -= 1
        elif depth == 0 and token.value == "=" and 0 < i < len(part) - 1:
            if part[i - 1].kind != "IDENT" or i - 1 != 0:
                raise PipelineError(f"{what} must use the form name = expression")
            return str(part[i - 1].value), parse_expression_tokens(part[i + 1:])
    raise PipelineError(f"{what} entries must be named with the form name = expression")


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
            if part[i - 1].kind != "IDENT" or i - 1 != 0:
                raise PipelineError(f"{what} names must be a single identifier before '='")
            return str(part[i - 1].value), parse_expression_tokens(part[i + 1:])
    return None, parse_expression_tokens(part)


def _output_name(name: str | None, expr: Expr, what: str) -> str:
    if name is not None:
        return name
    if isinstance(expr, Identifier):
        return expr.name.split(".")[-1]
    raise PipelineError(f"computed {what} entries must be named with the form name = expression")


def _parse_aggregate(part: list[Token]) -> tuple[str, Call]:
    name, expr = _parse_bare_or_named(part, "aggregate")
    if not isinstance(expr, Call) or str(expr.name).upper() not in _AGGREGATES:
        raise PipelineError("only COUNT, SUM, AVG, MIN, and MAX calls are allowed in group")
    if name is None:
        if (str(expr.name).upper() == "COUNT" and len(expr.args) == 1
                and isinstance(expr.args[0], Wildcard)):
            name = "count"
        else:
            raise PipelineError("aggregates must be named: name = AGG(expression)")
    return name, expr


def _parse_from(raw: _Raw) -> From:
    if not raw.tokens:
        raise PipelineError("from requires a table name or subquery")
    return From(raw.keyword, raw.line, _parse_table_ref(raw.tokens))


def _parse_join(raw: _Raw) -> Join:
    kind = "left" if raw.keyword == "left join" else "inner"
    depth = 0
    on_index = None
    for i, token in enumerate(raw.tokens):
        if token.value == "(":
            depth += 1
        elif token.value == ")":
            depth -= 1
        elif depth == 0 and str(token.value).upper() == "ON":
            on_index = i
            break
    if on_index is None:
        raise PipelineError("join requires an ON condition")
    if on_index == 0:
        raise PipelineError("join requires a table before ON")
    source = _parse_table_ref(raw.tokens[:on_index])
    on = parse_expression_tokens(raw.tokens[on_index + 1:])
    return Join(raw.keyword, raw.line, kind, source, on)


def _parse_derive(raw: _Raw) -> Derive:
    if not raw.tokens:
        raise PipelineError("derive requires at least one name = expression")
    items = tuple(_parse_named(part, "derive") for part in _split_top_level(raw.tokens))
    return Derive(raw.keyword, raw.line, items)


def _parse_where(raw: _Raw) -> Where:
    if not raw.tokens:
        raise PipelineError("where requires a condition")
    return Where(raw.keyword, raw.line, parse_expression_tokens(raw.tokens))


def _parse_group(raw: _Raw) -> Group:
    open_index = next((i for i, t in enumerate(raw.tokens) if t.value == "("), None)
    if open_index is None:
        raise PipelineError("group requires an aggregate list in parentheses")
    if not raw.tokens or raw.tokens[-1].value != ")":
        raise PipelineError("group must end with a closing ')'")
    key_tokens = raw.tokens[:open_index]
    agg_tokens = raw.tokens[open_index + 1:-1]
    if not key_tokens:
        raise PipelineError("group requires at least one key before '('")
    if not agg_tokens:
        raise PipelineError("group requires at least one aggregate inside '( )'")
    keys = []
    for part in _split_top_level(key_tokens):
        name, expr = _parse_bare_or_named(part, "group key")
        keys.append((_output_name(name, expr, "group key"), expr))
    aggregates = tuple(_parse_aggregate(part) for part in _split_top_level(agg_tokens))
    return Group(raw.keyword, raw.line, tuple(keys), aggregates)


def _parse_select(raw: _Raw) -> Select:
    if not raw.tokens:
        raise PipelineError("select requires at least one column or expression")
    if len(raw.tokens) == 1 and raw.tokens[0].value == "*":
        return Select(raw.keyword, raw.line, (), star=True)
    items = []
    for part in _split_top_level(raw.tokens):
        if len(part) == 1 and part[0].value == "*":
            raise PipelineError("'*' cannot be combined with other select items")
        name, expr = _parse_bare_or_named(part, "select")
        if isinstance(expr, Call) and str(expr.name).upper() in _AGGREGATES:
            raise PipelineError("aggregates belong in a group stage, not select")
        items.append(SelectItem(expr, name))
    return Select(raw.keyword, raw.line, tuple(items))


def _parse_sort(raw: _Raw) -> Sort:
    if not raw.tokens:
        raise PipelineError("sort requires at least one key")
    keys = []
    for part in _split_top_level(raw.tokens):
        descending = False
        if part and part[0].value == "-" and len(part) > 1:
            descending = True
            part = part[1:]
        if part and str(part[-1].value).upper() in {"ASC", "DESC"}:
            descending = str(part[-1].value).upper() == "DESC"
            part = part[:-1]
        if not part:
            raise PipelineError("sort key is missing an expression")
        keys.append(OrderItem(parse_expression_tokens(part), descending))
    return Sort(raw.keyword, raw.line, tuple(keys))


def _parse_count(raw: _Raw) -> int:
    if len(raw.tokens) != 1 or raw.tokens[0].kind != "NUMBER":
        raise PipelineError(f"{raw.keyword} requires one integer literal")
    value = raw.tokens[0].value
    if not isinstance(value, int) or value < 0:
        raise PipelineError(f"{raw.keyword} requires a non-negative integer")
    return value


def _parse_distinct(raw: _Raw) -> Distinct:
    if raw.tokens:
        raise PipelineError("distinct takes no arguments")
    return Distinct(raw.keyword, raw.line)


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #

_SINGLETONS = {"group", "take", "skip", "distinct", "from"}


def parse_pipeline_stages(text: str) -> list[Stage]:
    """Parse pipeline text into an ordered list of typed stage nodes."""
    raws = _split_raw_stages(text)
    if not raws:
        raise PipelineError("a pipeline needs at least a from stage")

    stages: list[Stage] = []
    seen: dict[str, int] = {}
    grouped = False
    projected = False

    for position, raw in enumerate(raws, start=1):
        kw = raw.keyword

        def fail(message: str) -> PipelineError:
            return PipelineError(message, index=position, keyword=kw, line=raw.line)

        if kw in _RESERVED or kw.endswith(" join") and kw != "left join":
            if kw in {"sql", "window"}:
                raise fail(f"{kw} is reserved and not implemented in v0.2")
            raise fail(f"{kw} is not available in the pipeline grammar yet; use ordered syntax")
        if kw != "from" and not stages:
            raise fail("a pipeline must start with 'from'")
        if position == 1 and kw != "from":
            raise fail("the first stage must be 'from'")
        if kw == "from" and stages:
            raise fail("'from' may appear only once in a pipeline")
        if kw in _SINGLETONS and kw in seen:
            raise fail(f"'{kw}' may appear only once (already at stage {seen[kw]})")
        if projected and kw not in _POST_SELECT:
            raise fail(f"{kw} cannot appear after select")

        try:
            if kw == "from":
                stages.append(_parse_from(raw))
            elif kw in {"join", "left join"}:
                stages.append(_parse_join(raw))
            elif kw == "derive":
                node = _parse_derive(raw)
                stages.append(Derive(node.keyword, node.line, node.items,
                                     "group" if grouped else "row"))
            elif kw == "where":
                node = _parse_where(raw)
                stages.append(Where(node.keyword, node.line, node.condition,
                                    "group" if grouped else "row"))
            elif kw == "group":
                stages.append(_parse_group(raw))
                grouped = True
            elif kw == "select":
                stages.append(_parse_select(raw))
                projected = True
            elif kw == "sort":
                stages.append(_parse_sort(raw))
            elif kw == "take":
                stages.append(Take(kw, raw.line, _parse_count(raw)))
            elif kw == "skip":
                stages.append(Skip(kw, raw.line, _parse_count(raw)))
            elif kw == "distinct":
                stages.append(_parse_distinct(raw))
            else:
                raise fail(f"unknown stage '{kw}'")
        except PipelineError as exc:
            if exc.index is None:
                raise fail(str(exc)) from None
            raise
        except ParseError as exc:
            raise fail(str(exc)) from None

        seen.setdefault(kw, position)

    return stages
