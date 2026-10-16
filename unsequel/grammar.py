"""The `.unsequel` grammar v1.3 front-end: lexer, AST, and parser.

This is a Python port of the TypeScript compiler's front-end
(``compiler/src/lexer.ts``, ``ast.ts``, and ``parser.ts``) so the Python
package can parse the canonical UNSeQueL language. It is the basis of the
convergence plan: the same ``.unsequel`` files must parse the same way in
both implementations.

The language is frozen in ``compiler/docs/grammar.md`` (v1.3): lexical rules
(§2), stages (§3), ordering/composition rules (§4), and the expression
grammar (§5). Error messages mirror the compiler's, and stage locations are
attached the same way, so behavior can be compared directly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Union

from .errors import LocatedError

__all__ = [
    "GrammarError",
    "Token",
    "tokenize",
    "parse_program",
    "program_from_tokens",
    "Span",
    "Position",
    "Literal",
    "Name",
    "Binary",
    "Unary",
    "Like",
    "InExpr",
    "Between",
    "IsNull",
    "Case",
    "Cast",
    "Call",
    "Expr",
    "SourceRef",
    "NamedExpr",
    "NamedAggregate",
    "SelectItem",
    "SortKey",
    "WindowCall",
    "FromStage",
    "WithStage",
    "SetStage",
    "JoinStage",
    "FilterStage",
    "DeriveStage",
    "GroupByStage",
    "AggregateStage",
    "HavingStage",
    "WindowStage",
    "SelectStage",
    "SortStage",
    "SkipStage",
    "TakeStage",
    "DistinctStage",
    "RawSqlStage",
    "CreateStage",
    "IntoStage",
    "Stage",
    "Program",
    "expression_name",
]


class GrammarError(LocatedError):
    """A lex/parse error in a `.unsequel` program, located when possible."""

    def with_stage(self, index: int, keyword: str) -> "GrammarError":
        """Attach stage location, mirroring the compiler's PipelineError wrap."""
        return GrammarError(
            self.message, self.line, self.column, stage_index=index, keyword=keyword
        )


# --------------------------------------------------------------------------- #
# Lexer (grammar §2)
# --------------------------------------------------------------------------- #

KEYWORDS: frozenset[str] = frozenset(
    [
        # stages
        "from", "join", "left", "right", "full", "cross", "filter", "derive",
        "group", "by", "aggregate", "having", "window", "select", "sort",
        "skip", "take", "distinct", "sql", "with", "union", "intersect",
        "except", "create", "table", "view", "into",
        # clauses and modifiers
        "on", "as", "over", "partition", "order", "asc", "desc", "all",
        # expression keywords
        "and", "or", "not", "in", "like", "between", "is", "null",
        "case", "when", "then", "else", "end", "cast", "true", "false",
    ]
)

_TWO_CHAR_OPERATORS = ("||", "<=", ">=", "!=", "<>", "==")
_SINGLE_CHAR_OPERATORS = frozenset("+-*/%=<>|")
_PUNCTUATION = frozenset("(),.")
_ESCAPES = {"\\": "\\", "'": "'", '"': '"', "n": "\n"}

_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


@dataclass(frozen=True)
class Token:
    type: str
    value: str
    line: int
    column: int
    end_line: int
    end_column: int


def _is_digit(char: str) -> bool:
    return "0" <= char <= "9"


def _is_identifier_start(char: str) -> bool:
    return ("a" <= char <= "z") or ("A" <= char <= "Z") or char == "_"


def _is_identifier_part(char: str) -> bool:
    return _is_identifier_start(char) or _is_digit(char)


def _read_string(source: str, start: int, line: int, column: int) -> tuple[str, int]:
    quote = source[start]
    i = start + 1
    value: list[str] = []
    while i < len(source):
        current = source[i]
        if current in "\n\r":
            raise GrammarError("unterminated string (strings are single-line)", line, column)
        if current == "\\":
            escaped = source[i + 1] if i + 1 < len(source) else None
            if escaped is None:
                raise GrammarError("dangling backslash at end of input", line, column + (i - start))
            replacement = _ESCAPES.get(escaped)
            if replacement is None:
                raise GrammarError(f"unknown escape sequence \\{escaped}", line, column + (i - start))
            value.append(replacement)
            i += 2
            continue
        if current == quote:
            if i + 1 < len(source) and source[i + 1] == quote:
                value.append(quote)
                i += 2
                continue
            return "".join(value), i + 1
        value.append(current)
        i += 1
    raise GrammarError("unterminated string", line, column)


def _read_quoted_identifier(source: str, start: int, line: int, column: int) -> tuple[str, int]:
    i = start + 1
    value: list[str] = []
    while i < len(source):
        current = source[i]
        if current in "\n\r":
            raise GrammarError("quoted identifiers are single-line", line, column)
        if current == "`":
            if i + 1 < len(source) and source[i + 1] == "`":
                value.append("`")
                i += 2
                continue
            text = "".join(value)
            if text == "":
                raise GrammarError("quoted identifier cannot be empty", line, column)
            if "." in text:
                raise GrammarError(
                    "quoted identifier cannot contain '.'; quote each qualified part",
                    line, column,
                )
            return text, i + 1
        value.append(current)
        i += 1
    raise GrammarError("unterminated quoted identifier", line, column)


def tokenize(source: str) -> list[Token]:
    tokens: list[Token] = []
    index = 0
    line = 1
    column = 1

    def ch(offset: int = 0) -> str:
        return source[index + offset] if index + offset < len(source) else ""

    def emit(type_: str, value: str, start_line: int, start_column: int) -> None:
        tokens.append(Token(type_, value, start_line, start_column, line, column))

    while index < len(source):
        current = source[index]

        if current in " \t":
            index += 1
            column += 1
            continue
        if current in "\r\n":
            start_line, start_column = line, column
            if current == "\r" and ch(1) == "\n":
                index += 1
            index += 1
            line += 1
            column = 1
            tokens.append(Token("newline", "\n", start_line, start_column, line, column))
            continue
        if current == "#" or (current == "-" and ch(1) == "-"):
            while index < len(source) and source[index] not in "\n\r":
                index += 1
                column += 1
            continue
        if current in "'\"":
            start_line, start_column = line, column
            value, end = _read_string(source, index, line, column)
            column += end - index
            index = end
            emit("string", value, start_line, start_column)
            continue
        if current == "`":
            start_line, start_column = line, column
            value, end = _read_quoted_identifier(source, index, line, column)
            column += end - index
            index = end
            emit("quoted_identifier", value, start_line, start_column)
            continue
        if _is_digit(current):
            start_line, start_column = line, column
            raw = current
            index += 1
            column += 1
            while _is_digit(ch()):
                raw += source[index]
                index += 1
                column += 1
            if ch() == "." and _is_digit(ch(1)):
                raw += "."
                index += 1
                column += 1
                while _is_digit(ch()):
                    raw += source[index]
                    index += 1
                    column += 1
            # Scientific notation: 1e20, 1.5e-3 (the exponent needs a digit).
            if ch() in {"e", "E"}:
                offset = 2 if ch(1) in {"+", "-"} else 1
                if _is_digit(ch(offset)):
                    raw += source[index]
                    index += 1
                    column += 1
                    if offset == 2:
                        raw += source[index]
                        index += 1
                        column += 1
                    while _is_digit(ch()):
                        raw += source[index]
                        index += 1
                        column += 1
            emit("number", raw, start_line, start_column)
            continue
        if _is_identifier_start(current):
            start_line, start_column = line, column
            raw = current
            index += 1
            column += 1
            while _is_identifier_part(ch()):
                raw += source[index]
                index += 1
                column += 1
            lowered = raw.lower()
            if lowered in KEYWORDS:
                emit("keyword", lowered, start_line, start_column)
            else:
                emit("identifier", raw, start_line, start_column)
            continue

        two = source[index:index + 2]
        if two in _TWO_CHAR_OPERATORS:
            start_line, start_column = line, column
            index += 2
            column += 2
            emit("operator", two, start_line, start_column)
            continue
        if current in _SINGLE_CHAR_OPERATORS:
            start_line, start_column = line, column
            index += 1
            column += 1
            emit("operator", current, start_line, start_column)
            continue
        if current in _PUNCTUATION:
            start_line, start_column = line, column
            index += 1
            column += 1
            emit("punctuation", current, start_line, start_column)
            continue
        raise GrammarError(f"unexpected character {current!r}", line, column)

    tokens.append(Token("eof", "", line, column, line, column))
    return tokens


# --------------------------------------------------------------------------- #
# AST (mirrors compiler/src/ast.ts)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Position:
    line: int
    column: int


@dataclass(frozen=True)
class Span:
    start: Position
    end: Position


def _position_of(token: Token) -> Position:
    return Position(token.line, token.column)


def _token_span(token: Token) -> Span:
    return Span(Position(token.line, token.column), Position(token.end_line, token.end_column))


def _spanning(first: Span, last: Span) -> Span:
    return Span(first.start, last.end)


# Expressions (grammar §5) -- one frozen dataclass per ast.ts node.        #


@dataclass(frozen=True)
class Literal:
    value: object
    span: Span


@dataclass(frozen=True)
class Name:
    name: str
    span: Span


@dataclass(frozen=True)
class Binary:
    op: str
    left: "Expr"
    right: "Expr"
    span: Span


@dataclass(frozen=True)
class Unary:
    op: str
    operand: "Expr"
    span: Span


@dataclass(frozen=True)
class Like:
    negated: bool
    value: "Expr"
    pattern: "Expr"
    span: Span


@dataclass(frozen=True)
class InExpr:
    negated: bool
    value: "Expr"
    options: tuple["Expr", ...]
    span: Span


@dataclass(frozen=True)
class Between:
    negated: bool
    value: "Expr"
    lower: "Expr"
    upper: "Expr"
    span: Span


@dataclass(frozen=True)
class IsNull:
    negated: bool
    value: "Expr"
    span: Span


@dataclass(frozen=True)
class Case:
    branches: tuple[tuple["Expr", "Expr"], ...]
    else_expr: "Expr | None"
    span: Span


@dataclass(frozen=True)
class Cast:
    operand: "Expr"
    type_name: str
    span: Span


@dataclass(frozen=True)
class Call:
    name: str
    distinct: bool
    star: bool
    args: tuple["Expr", ...]
    span: Span


Expr = Union[Literal, Name, Binary, Unary, Like, InExpr, Between, IsNull, Case, Cast, Call]


def expression_name(expr: Expr) -> str:
    """Output name for an anonymous expression (ast.ts `expressionName`)."""
    if isinstance(expr, Name):
        return expr.name.split(".")[-1]
    if isinstance(expr, Call):
        return expr.name.lower()
    if isinstance(expr, Case):
        return "case"
    if isinstance(expr, Cast):
        return "cast"
    if isinstance(expr, InExpr):
        return "in"
    if isinstance(expr, Between):
        return "between"
    return "expression"


# Stages (grammar §3) -- one frozen dataclass per stage node.              #


@dataclass(frozen=True)
class SourceRef:
    name: str
    alias: "str | None"
    span: Span
    program: "Program | None" = None


@dataclass(frozen=True)
class NamedExpr:
    name: str
    expr: Expr
    span: Span


@dataclass(frozen=True)
class NamedAggregate:
    name: str
    call: Call
    span: Span


@dataclass(frozen=True)
class SelectItem:
    expr: Expr
    alias: "str | None"
    span: Span


@dataclass(frozen=True)
class SortKey:
    expr: Expr
    descending: bool
    span: Span


@dataclass(frozen=True)
class WindowCall:
    func: str
    args: tuple[Expr, ...]
    star: bool
    partition_by: tuple[Expr, ...]
    order_by: tuple[SortKey, ...]


@dataclass(frozen=True)
class Stage:
    index: int
    keyword: str
    span: Span


@dataclass(frozen=True)
class FromStage(Stage):
    source: SourceRef = None  # type: ignore[assignment]
    kind: str = field(default="from", init=False)


@dataclass(frozen=True)
class WithStage(Stage):
    name: str = ""
    program: "Program" = field(default_factory=list)
    kind: str = field(default="with", init=False)


@dataclass(frozen=True)
class SetStage(Stage):
    op: str = ""
    program: "Program" = field(default_factory=list)
    kind: str = field(default="set", init=False)


@dataclass(frozen=True)
class JoinStage(Stage):
    join_kind: str = ""
    source: SourceRef = None  # type: ignore[assignment]
    on: Expr = None  # type: ignore[assignment]
    kind: str = field(default="join", init=False)


@dataclass(frozen=True)
class FilterStage(Stage):
    condition: Expr = None  # type: ignore[assignment]
    kind: str = field(default="filter", init=False)


@dataclass(frozen=True)
class DeriveStage(Stage):
    phase: str = "row"
    items: tuple[NamedExpr, ...] = ()
    kind: str = field(default="derive", init=False)


@dataclass(frozen=True)
class GroupByStage(Stage):
    keys: tuple[NamedExpr, ...] = ()
    kind: str = field(default="group by", init=False)


@dataclass(frozen=True)
class AggregateStage(Stage):
    items: tuple[NamedAggregate, ...] = ()
    kind: str = field(default="aggregate", init=False)


@dataclass(frozen=True)
class HavingStage(Stage):
    condition: Expr = None  # type: ignore[assignment]
    kind: str = field(default="having", init=False)


@dataclass(frozen=True)
class WindowStage(Stage):
    name: str = ""
    call: WindowCall = None  # type: ignore[assignment]
    kind: str = field(default="window", init=False)


@dataclass(frozen=True)
class SelectStage(Stage):
    star: bool = False
    items: tuple[SelectItem, ...] = ()
    kind: str = field(default="select", init=False)


@dataclass(frozen=True)
class SortStage(Stage):
    keys: tuple[SortKey, ...] = ()
    kind: str = field(default="sort", init=False)


@dataclass(frozen=True)
class SkipStage(Stage):
    count: int = 0
    kind: str = field(default="skip", init=False)


@dataclass(frozen=True)
class TakeStage(Stage):
    count: int = 0
    kind: str = field(default="take", init=False)


@dataclass(frozen=True)
class DistinctStage(Stage):
    kind: str = field(default="distinct", init=False)


@dataclass(frozen=True)
class RawSqlStage(Stage):
    text: str = ""
    kind: str = field(default="sql", init=False)


@dataclass(frozen=True)
class CreateStage(Stage):
    object: str = "table"
    replace: bool = False
    name: str = ""
    program: "Program" = field(default_factory=list)
    kind: str = field(default="create", init=False)


@dataclass(frozen=True)
class IntoStage(Stage):
    name: str = ""
    program: "Program" = field(default_factory=list)
    kind: str = field(default="into", init=False)


Program = list[Stage]


# --------------------------------------------------------------------------- #
# Parser (compiler/src/parser.ts)
# --------------------------------------------------------------------------- #

_STAGE_STARTS = frozenset(
    [
        "from", "with", "union", "intersect", "except",
        "join", "left", "right", "full", "cross", "filter", "derive", "group",
        "aggregate", "having", "window", "select", "sort", "skip", "take",
        "distinct", "sql", "create", "into",
    ]
)

_AGGREGATES = frozenset(["count", "sum", "avg", "min", "max"])

_SCALAR_FUNCTIONS = frozenset(
    [
        "coalesce", "nullif", "lower", "upper", "abs", "round", "length",
        "trim", "concat", "strftime", "date_trunc",
    ]
)

_WINDOW_FUNCTIONS = frozenset(
    ["row_number", "rank", "dense_rank", "lag", "lead", "sum", "avg", "count", "min", "max"]
)

_COMPARISONS = frozenset(["=", "==", "!=", "<>", "<", "<=", ">", ">="])

_NEGATABLE_TAILS = frozenset(["in", "like", "between"])

_MAX_SAFE_INTEGER = 2**53 - 1


def parse_program(source: str) -> Program:
    return program_from_tokens(tokenize(source), False)


def _describe(token: Token) -> str:
    if token.type == "keyword":
        return f"keyword '{token.value}'"
    if token.type == "identifier":
        return f"identifier '{token.value}'"
    if token.type == "quoted_identifier":
        return f"quoted identifier `{token.value}`"
    if token.type == "string":
        return "string literal"
    if token.type == "number":
        return f"number '{token.value}'"
    if token.type in {"operator", "punctuation"}:
        return f"'{token.value}'"
    return token.type


def _is_identifier_token(token: Token | None) -> bool:
    return token is not None and token.type in {"identifier", "quoted_identifier"}


def _split_stages(tokens: list[Token]) -> list[list[Token]]:
    stages: list[list[Token]] = []
    current: list[Token] = []
    depth = 0
    for token in tokens:
        if token.type == "eof":
            break
        if token.type == "newline":
            # Inside parentheses a newline stays in the token stream so a
            # nested program can use it as a stage separator; at depth zero
            # it terminates the current stage.
            if depth > 0:
                current.append(token)
                continue
            if current:
                stages.append(current)
                current = []
            continue
        if token.type == "operator" and token.value == "|":
            if depth > 0:
                current.append(token)
                continue
            if current:
                stages.append(current)
                current = []
            continue
        if token.type == "punctuation" and token.value == "(":
            depth += 1
        if token.type == "punctuation" and token.value == ")":
            depth -= 1
        current.append(token)
    if current:
        stages.append(current)
    return stages


def _stage_header(tokens: list[Token]) -> tuple[str, list[Token]]:
    first = tokens[0]
    if first.type != "keyword" or first.value not in _STAGE_STARTS:
        if first.type in {"identifier", "quoted_identifier"}:
            raise GrammarError(
                f"expected a stage keyword; found identifier '{first.value}'",
                first.line, first.column,
            )
        raise GrammarError(
            f"expected a stage keyword; found {_describe(first)}", first.line, first.column
        )
    if first.value in {"left", "right", "full", "cross"}:
        second = tokens[1] if len(tokens) > 1 else None
        if second is None or second.type != "keyword" or second.value != "join":
            raise GrammarError(
                f"'{first.value}' must be followed by 'join'", first.line, first.column
            )
        return f"{first.value} join", tokens[2:]
    if first.value == "group":
        second = tokens[1] if len(tokens) > 1 else None
        if second is None or second.type != "keyword" or second.value != "by":
            raise GrammarError("'group' must be followed by 'by'", first.line, first.column)
        return "group by", tokens[2:]
    return first.value, tokens[1:]


class _TokenReader:
    def __init__(self, tokens: list[Token], start: Token) -> None:
        self._tokens = tokens
        self._start = start
        self._position = 0
        self.last_consumed: Token | None = None

    @property
    def done(self) -> bool:
        return self._position >= len(self._tokens)

    def peek(self, offset: int = 0) -> Token | None:
        position = self._position + offset
        return self._tokens[position] if position < len(self._tokens) else None

    def advance(self) -> Token:
        if self._position >= len(self._tokens):
            self.fail("unexpected end of stage")
        token = self._tokens[self._position]
        self._position += 1
        self.last_consumed = token
        return token

    def at_keyword(self, word: str) -> bool:
        token = self.peek()
        return token is not None and token.type == "keyword" and token.value == word

    def match_keyword(self, word: str) -> bool:
        if not self.at_keyword(word):
            return False
        self.advance()
        return True

    def expect_keyword(self, word: str, what: str | None = None) -> Token:
        token = self.peek()
        if token is None or token.type != "keyword" or token.value != word:
            self.fail(f"expected '{word}'{f' {what}' if what else ''}", token)
        return self.advance()

    def at_operator(self, value: str) -> bool:
        token = self.peek()
        return token is not None and token.type == "operator" and token.value == value

    def match_operator(self, value: str) -> bool:
        if not self.at_operator(value):
            return False
        self.advance()
        return True

    def expect_operator(self, value: str, what: str | None = None) -> Token:
        token = self.peek()
        if token is None or token.type != "operator" or token.value != value:
            self.fail(f"expected '{value}'{f' {what}' if what else ''}", token)
        return self.advance()

    def at_punct(self, value: str) -> bool:
        token = self.peek()
        return token is not None and token.type == "punctuation" and token.value == value

    def match_punct(self, value: str) -> bool:
        if not self.at_punct(value):
            return False
        self.advance()
        return True

    def expect_punct(self, value: str, what: str | None = None) -> Token:
        token = self.peek()
        if token is None or token.type != "punctuation" or token.value != value:
            self.fail(f"expected '{value}'{f' {what}' if what else ''}", token)
        return self.advance()

    def expect_identifier(self, what: str = "expected an identifier") -> Token:
        token = self.peek()
        if not _is_identifier_token(token):
            self.fail(what, token)
        return self.advance()

    def expect_end(self) -> None:
        if not self.done:
            token = self.peek()
            self.fail(f"unexpected {_describe(token)}", token)

    def read_balanced_parens(self, what: str) -> list[Token]:
        self.expect_punct("(", f"to open {what}")
        inner: list[Token] = []
        depth = 1
        while True:
            token = self.peek()
            if token is None:
                self.fail(f"unclosed '(' in {what}")
            if token.type == "punctuation" and token.value == "(":
                depth += 1
            if token.type == "punctuation" and token.value == ")":
                depth -= 1
                if depth == 0:
                    self.advance()
                    return inner
            inner.append(self.advance())

    def fail(self, message: str, token: Token | None = None) -> "None":
        if token is not None:
            raise GrammarError(message, token.line, token.column)
        if self.last_consumed is not None:
            raise GrammarError(
                message, self.last_consumed.end_line, self.last_consumed.end_column
            )
        raise GrammarError(message, self._start.line, self._start.column)


def _span_from(reader: _TokenReader, start: Token) -> Span:
    return _spanning(_token_span(start), _token_span(reader.last_consumed or start))


@dataclass
class _ExprContext:
    in_aggregate: bool


def program_from_tokens(tokens: list[Token], nested: bool = False) -> Program:
    raw_stages = _split_stages(tokens)
    if not raw_stages:
        eof = tokens[-1]
        raise GrammarError("a pipeline needs at least a from stage", eof.line, eof.column)

    entries = []
    for stage_tokens in raw_stages:
        keyword, rest = _stage_header(stage_tokens)
        entries.append((stage_tokens, keyword, rest))

    stages: Program = []
    saw_from = False
    saw_statement = False
    grouped = False
    aggregated = False
    group_pending = False
    after_hatch = False
    with_names: set[str] = set()

    for position, (stage_tokens, keyword, rest) in enumerate(entries):
        index = position + 1
        first = stage_tokens[0]
        span = _spanning(_token_span(first), _token_span(stage_tokens[-1]))

        def fail(message: str, at: Token | None = None, *, _i: int = index,
                 _k: str = keyword, _f: Token = first) -> "None":
            token = at if at is not None else _f
            raise GrammarError(
                message, token.line, token.column, stage_index=_i, keyword=_k
            )

        next_keyword = entries[position + 1][1] if position + 1 < len(entries) else None

        if index == 1 and keyword not in {"from", "with", "create", "into"}:
            fail("a pipeline must start with 'with', 'from', 'create', or 'into'")
        if nested and keyword in {"create", "into"}:
            fail(f"'{keyword}' can only appear in the top-level program")
        if keyword in {"create", "into"} and saw_from:
            fail(f"'{keyword}' cannot be combined with 'from'; "
                 "write statements in their own program")
        if saw_statement and keyword not in {"with", "create", "into"}:
            fail(f"'{keyword}' cannot be combined with create/into; "
                 "write statements in their own program")
        if keyword == "with" and saw_from:
            fail("'with' definitions must appear before 'from'")
        if keyword in {"union", "intersect", "except"} and not saw_from:
            fail(f"'{keyword}' requires a preceding from")
        if keyword == "from" and saw_from:
            fail("'from' may appear only once in a pipeline")
        if keyword in {"join", "left join", "right join", "full join", "cross join"} and after_hatch:
            fail("join cannot appear after a sql stage; the hatch output columns are opaque")
        if keyword == "group by" and next_keyword != "aggregate":
            fail("group by must be immediately followed by aggregate")
        if keyword == "aggregate" and not group_pending:
            fail("aggregate requires a preceding group by (they form one unit)")
        if keyword == "having" and not aggregated:
            fail("having requires a preceding aggregate (group by + aggregate)")

        reader = _TokenReader(rest, first)
        context = _ExprContext(in_aggregate=False)
        try:
            stage = _parse_stage_body(keyword, reader, context, index, span, grouped)
        except GrammarError as error:
            if error.stage_index is not None:
                raise
            raise error.with_stage(index, keyword) from None
        stages.append(stage)

        if keyword == "with":
            assert isinstance(stage, WithStage)
            if stage.name in with_names:
                fail(f"with '{stage.name}' is defined more than once")
            with_names.add(stage.name)
        if keyword == "from":
            saw_from = True
        if keyword in {"create", "into"}:
            saw_statement = True
        if keyword == "group by":
            group_pending = True
        if keyword == "aggregate":
            group_pending = False
            grouped = True
            aggregated = True
        if keyword == "sql":
            grouped = False
            aggregated = False
            group_pending = False
            after_hatch = True

    return stages


def _base(index: int, keyword: str, span: Span) -> dict:
    return {"index": index, "keyword": keyword, "span": span}


def _parse_stage_body(
    keyword: str,
    reader: _TokenReader,
    context: _ExprContext,
    index: int,
    span: Span,
    grouped: bool,
) -> Stage:
    if keyword == "from":
        source = _parse_source(reader, "from source")
        reader.expect_end()
        return FromStage(**_base(index, keyword, span), source=source)
    if keyword == "with":
        name = reader.expect_identifier("with needs a name: with name = (program)")
        reader.expect_operator("=", "after the with name")
        inner = reader.read_balanced_parens("the with program")
        reader.expect_end()
        return WithStage(**_base(index, keyword, span), name=name.value,
                         program=program_from_tokens(inner, True))
    if keyword in {"union", "intersect", "except"}:
        op = keyword
        if keyword == "union" and reader.match_keyword("all"):
            op = "union all"
        inner = reader.read_balanced_parens(f"the {op} program")
        reader.expect_end()
        return SetStage(**_base(index, keyword, span), op=op,
                        program=program_from_tokens(inner, True))
    if keyword in {"join", "left join", "right join", "full join", "cross join"}:
        source = _parse_source(reader, "join source")
        if keyword == "cross join":
            reader.expect_end()
            on: Expr = Literal(True, source.span)
        else:
            reader.expect_keyword("on", "after the joined table")
            on = _parse_expression(reader, context)
            reader.expect_end()
        join_kind = {
            "left join": "left", "right join": "right",
            "full join": "full", "cross join": "cross",
        }.get(keyword, "inner")
        return JoinStage(**_base(index, keyword, span), join_kind=join_kind,
                         source=source, on=on)
    if keyword == "filter":
        condition = _parse_expression(reader, context)
        reader.expect_end()
        return FilterStage(**_base(index, keyword, span), condition=condition)
    if keyword == "derive":
        items = _parse_comma_list(reader, lambda r: _parse_named_expr(r, "derive"))
        reader.expect_end()
        _reject_duplicate_names(
            [item.name for item in items],
            lambda name: reader.fail(f"derive defines '{name}' more than once"),
        )
        return DeriveStage(**_base(index, keyword, span),
                           phase="group" if grouped else "row", items=items)
    if keyword == "group by":
        keys = _parse_comma_list(reader, _parse_group_key)
        reader.expect_end()
        _reject_duplicate_names(
            [key.name for key in keys],
            lambda name: reader.fail(f"group key '{name}' appears more than once"),
        )
        return GroupByStage(**_base(index, keyword, span), keys=keys)
    if keyword == "aggregate":
        items = _parse_comma_list(reader, _parse_aggregate_item)
        reader.expect_end()
        _reject_duplicate_names(
            [item.name for item in items],
            lambda name: reader.fail(f"aggregate '{name}' is defined more than once"),
        )
        return AggregateStage(**_base(index, keyword, span), items=items)
    if keyword == "having":
        condition = _parse_expression(reader, context)
        reader.expect_end()
        return HavingStage(**_base(index, keyword, span), condition=condition)
    if keyword == "window":
        name_token = reader.expect_identifier(
            "window needs a name: window name = FUNC(...) over (...)"
        )
        reader.expect_operator("=", "after the window name")
        func_token = reader.expect_identifier("expected a window function name")
        func = func_token.value
        if func.lower() not in _WINDOW_FUNCTIONS:
            reader.fail(f"'{func}' is not a window function", func_token)
        reader.expect_punct("(")
        star = False
        args: list[Expr] = []
        if reader.at_operator("*"):
            reader.advance()
            if func.lower() != "count":
                reader.fail("only COUNT accepts '*'", func_token)
            star = True
        elif not reader.at_punct(")"):
            args = _parse_expr_list(reader, context)
        reader.expect_punct(")")
        reader.expect_keyword("over", "after the window function call")
        reader.expect_punct("(")
        partition_by: list[Expr] = []
        order_by: list[SortKey] = []
        if reader.match_keyword("partition"):
            reader.expect_keyword("by", "after 'partition'")
            partition_by = _parse_expr_list(reader, context)
        if reader.match_keyword("order"):
            reader.expect_keyword("by", "after 'order'")
            order_by = _parse_comma_list(reader, _parse_sort_key)
        reader.expect_punct(")", "to close the over clause")
        reader.expect_end()
        call = WindowCall(func, tuple(args), star, tuple(partition_by), tuple(order_by))
        return WindowStage(**_base(index, keyword, span), name=name_token.value, call=call)
    if keyword == "select":
        star = reader.match_operator("*")
        items: list[SelectItem] = []
        if star:
            if reader.match_punct(","):
                items = _parse_comma_list(reader, _parse_select_item)
        else:
            items = _parse_comma_list(reader, _parse_select_item)
        reader.expect_end()
        _reject_duplicate_names(
            [item.alias or expression_name(item.expr) for item in items],
            lambda name: reader.fail(
                f"select projects '{name}' more than once; add an alias to one of them"
            ),
        )
        return SelectStage(**_base(index, keyword, span), star=star, items=tuple(items))
    if keyword == "sort":
        keys = _parse_comma_list(reader, _parse_sort_key)
        reader.expect_end()
        return SortStage(**_base(index, keyword, span), keys=tuple(keys))
    if keyword == "skip":
        count = _parse_count(reader, "skip")
        return SkipStage(**_base(index, keyword, span), count=count)
    if keyword == "take":
        count = _parse_count(reader, "take")
        return TakeStage(**_base(index, keyword, span), count=count)
    if keyword == "distinct":
        reader.expect_end()
        return DistinctStage(**_base(index, keyword, span))
    if keyword == "create":
        replace = False
        if reader.match_keyword("or"):
            word = reader.peek()
            if word is None or word.value.lower() != "replace":
                reader.fail("expected 'replace' after 'create or': "
                            "create or replace table name = (program)", word)
            reader.advance()
            replace = True
        object_token = reader.peek()
        if object_token is None or object_token.type != "keyword" \
                or object_token.value not in {"table", "view"}:
            reader.fail("create needs 'table' or 'view': create table name = (program)",
                        object_token)
        reader.advance()
        name = reader.expect_identifier(
            "create needs a name: create table name = (program)")
        _reject_qualified_target(reader)
        reader.expect_operator("=", "after the create name")
        inner = reader.read_balanced_parens("the create program")
        reader.expect_end()
        return CreateStage(**_base(index, keyword, span), object=object_token.value,
                           replace=replace, name=name.value,
                           program=program_from_tokens(inner, True))
    if keyword == "into":
        name = reader.expect_identifier("into needs a table name: into name (program)")
        _reject_qualified_target(reader)
        inner = reader.read_balanced_parens("the into program")
        reader.expect_end()
        return IntoStage(**_base(index, keyword, span), name=name.value,
                         program=program_from_tokens(inner, True))
    if keyword == "sql":
        token = reader.peek()
        if token is None or token.type != "string":
            reader.fail(
                'sql requires one string literal, as in sql "SELECT * FROM __input__"',
                token,
            )
        reader.advance()
        reader.expect_end()
        text = token.value
        if text.strip() == "":
            reader.fail("sql stage text is empty", token)
        if ";" in text:
            reader.fail("sql stage must be a single SELECT statement; ';' is not allowed", token)
        first_word = _first_sql_word(text)
        if first_word not in {"SELECT", "WITH"}:
            reader.fail("sql stage must be a query; start it with SELECT or WITH", token)
        return RawSqlStage(**_base(index, keyword, span), text=text)
    reader.fail(f"unknown stage '{keyword}'")
    raise AssertionError("unreachable")


def _parse_source(reader: _TokenReader, what: str) -> SourceRef:
    start = reader.peek()
    if start is None:
        reader.fail(f"expected a table name for the {what}")
    if start.type == "punctuation" and start.value == "(":
        inner = reader.read_balanced_parens(f"the {what} subquery")
        if reader.match_keyword("as"):
            alias = reader.expect_identifier("expected an alias after 'as'").value
        else:
            reader.fail(
                f"a {what} subquery requires an alias: (...) as name", reader.peek()
            )
        return SourceRef("", alias, _span_from(reader, start),
                         program=program_from_tokens(inner, True))
    if not _is_identifier_token(start):
        reader.fail(f"expected a table name for the {what}", start)
    reader.advance()
    parts = [start.value]
    while reader.match_punct("."):
        parts.append(reader.expect_identifier("expected an identifier after '.'").value)
    alias = None
    if reader.match_keyword("as"):
        alias = reader.expect_identifier("expected an alias after 'as'").value
    return SourceRef(".".join(parts), alias, _span_from(reader, start))


def _parse_comma_list(reader: _TokenReader, parse_item) -> list:
    items = [parse_item(reader)]
    while reader.match_punct(","):
        items.append(parse_item(reader))
    return items


def _reject_duplicate_names(names: list[str], fail) -> None:
    seen: set[str] = set()
    for name in names:
        if name in seen:
            fail(name)
        seen.add(name)


def _first_sql_word(text: str) -> str:
    i = 0
    while i < len(text):
        char = text[i]
        if char.isspace():
            i += 1
            continue
        if text.startswith("--", i):
            newline = text.find("\n", i)
            i = len(text) if newline < 0 else newline + 1
            continue
        if text.startswith("/*", i):
            close = text.find("*/", i + 2)
            i = len(text) if close < 0 else close + 2
            continue
        break
    match = _IDENTIFIER_RE.match(text[i:])
    return match.group(0).upper() if match else ""


def _parse_named_expr(reader: _TokenReader, what: str) -> NamedExpr:
    start = reader.peek()
    name = reader.expect_identifier(f"{what} entries need a name: name = expression")
    reader.expect_operator("=", f"after the {what} name")
    expr = _parse_expression(reader, _ExprContext(in_aggregate=False))
    return NamedExpr(name.value, expr, _span_from(reader, start))


def _parse_group_key(reader: _TokenReader) -> NamedExpr:
    if reader.done:
        reader.fail("group by requires at least one key")
    start = reader.peek()
    if _is_identifier_token(start):
        next_token = reader.peek(1)
        if next_token is not None and next_token.type == "operator" and next_token.value == "=":
            name = reader.advance().value
            reader.advance()
            expr = _parse_expression(reader, _ExprContext(in_aggregate=False))
            return NamedExpr(name, expr, _span_from(reader, start))
        if next_token is not None and next_token.type == "punctuation" and next_token.value == ".":
            reader.fail(
                "group keys cannot be qualified names; write 'name = <expression>' instead",
                start,
            )
        reader.advance()
        return NamedExpr(start.value, Name(start.value, _token_span(start)),
                         _token_span(start))
    reader.fail("group keys must be a column name or name = expression", start)
    raise AssertionError("unreachable")


def _parse_aggregate_item(reader: _TokenReader) -> NamedAggregate:
    start = reader.peek()
    if start is None:
        reader.fail("aggregate requires at least one named aggregate call")
    name: str | None = None
    first = reader.peek()
    second = reader.peek(1)
    if _is_identifier_token(first) and second is not None \
            and second.type == "operator" and second.value == "=":
        name = reader.advance().value
        reader.advance()
    call = _parse_expression(reader, _ExprContext(in_aggregate=True))
    if not isinstance(call, Call):
        reader.fail("aggregate items must be an aggregate call: name = COUNT(*) etc.", start)
    lowered = call.name.lower()
    if lowered not in _AGGREGATES:
        reader.fail(
            f"'{call.name}' is not an aggregate; use COUNT, SUM, AVG, MIN, or MAX", start
        )
    if any(_contains_aggregate_call(arg) for arg in call.args):
        reader.fail("aggregate arguments cannot contain aggregate calls", start)
    if name is None:
        if lowered == "count" and call.star:
            name = "count"
        else:
            reader.fail("aggregates must be named: name = AGG(expression)", start)
    return NamedAggregate(name, call, _span_from(reader, start))


def _parse_select_item(reader: _TokenReader) -> SelectItem:
    start = reader.peek()
    if start is None:
        reader.fail("select requires at least one column or expression")
    if start.type == "operator" and start.value == "*":
        reader.fail("'*' must be the only select item", start)
    first = reader.peek()
    second = reader.peek(1)
    if _is_identifier_token(first) and second is not None \
            and second.type == "operator" and second.value == "=":
        alias = reader.advance().value
        reader.advance()
        expr = _parse_expression(reader, _ExprContext(in_aggregate=False))
        return SelectItem(expr, alias, _span_from(reader, start))
    expr = _parse_expression(reader, _ExprContext(in_aggregate=False))
    return SelectItem(expr, None, _span_from(reader, start))


def _parse_sort_key(reader: _TokenReader) -> SortKey:
    start = reader.peek()
    if start is None:
        reader.fail("sort requires at least one key")
    descending = reader.match_operator("-")
    expr = _parse_expression(reader, _ExprContext(in_aggregate=False))
    if reader.match_keyword("desc"):
        descending = True
    else:
        reader.match_keyword("asc")
    return SortKey(expr, descending, _span_from(reader, start))


def _parse_count(reader: _TokenReader, keyword: str) -> int:
    token = reader.peek()
    if token is None or token.type != "number" or "." in token.value:
        reader.fail(f"{keyword} requires a non-negative integer", token)
    reader.advance()
    reader.expect_end()
    value = float(token.value)  # "1e2" is an integer too (the TS track allows it)
    if not value.is_integer() or value > _MAX_SAFE_INTEGER:
        reader.fail(f"{keyword} exceeds the supported integer range", token)
    return int(value)


# --------------------------------------------------------------------------- #
# Expressions (grammar §5)
# --------------------------------------------------------------------------- #


def _parse_expression(reader: _TokenReader, context: _ExprContext) -> Expr:
    return _parse_or(reader, context)


def _parse_or(reader: _TokenReader, context: _ExprContext) -> Expr:
    start = reader.peek()
    if start is None:
        reader.fail("expected an expression")
    left = _parse_and(reader, context)
    while reader.match_keyword("or"):
        right = _parse_and(reader, context)
        left = Binary("or", left, right, _span_from(reader, start))
    return left


def _parse_and(reader: _TokenReader, context: _ExprContext) -> Expr:
    start = reader.peek()
    if start is None:
        reader.fail("expected an expression")
    left = _parse_not(reader, context)
    while reader.match_keyword("and"):
        right = _parse_not(reader, context)
        left = Binary("and", left, right, _span_from(reader, start))
    return left


def _parse_not(reader: _TokenReader, context: _ExprContext) -> Expr:
    start = reader.peek()
    if start is None:
        reader.fail("expected an expression")
    if reader.match_keyword("not"):
        operand = _parse_comparison(reader, context)
        return Unary("not", operand, _span_from(reader, start))
    return _parse_comparison(reader, context)


def _parse_comparison(reader: _TokenReader, context: _ExprContext) -> Expr:
    start = reader.peek()
    if start is None:
        reader.fail("expected an expression")
    left = _parse_additive(reader, context)
    with_tail = _parse_comparison_tail(reader, context, left)
    if _starts_comparison(reader):
        reader.fail("chained comparisons are not supported; combine them with AND (or use BETWEEN)")
    return with_tail


def _starts_comparison(reader: _TokenReader) -> bool:
    token = reader.peek()
    if token is None:
        return False
    if token.type == "operator" and token.value in _COMPARISONS:
        return True
    if token.type == "keyword":
        if token.value in {"is", "in", "like", "between"}:
            return True
        if token.value == "not":
            next_token = reader.peek(1)
            return (
                next_token is not None
                and next_token.type == "keyword"
                and next_token.value in _NEGATABLE_TAILS
            )
    return False


def _parse_comparison_tail(reader: _TokenReader, context: _ExprContext, left: Expr) -> Expr:
    token = reader.peek()
    if token is None:
        return left
    if token.type == "operator" and token.value in _COMPARISONS:
        op = reader.advance().value
        right = _parse_additive(reader, context)
        return Binary(op, left, right, _spanning(left.span, right.span))
    if reader.at_keyword("is"):
        reader.advance()
        negated = reader.match_keyword("not")
        reader.expect_keyword("null", "after 'is'")
        return IsNull(negated, left, _spanning(left.span, _token_span(reader.last_consumed)))
    negated = False
    if reader.at_keyword("not"):
        next_token = reader.peek(1)
        if next_token is not None and next_token.type == "keyword" \
                and next_token.value in _NEGATABLE_TAILS:
            reader.advance()
            negated = True
    if reader.at_keyword("in"):
        reader.advance()
        reader.expect_punct("(")
        options = _parse_expr_list(reader, context)
        reader.expect_punct(")", "to close the IN list")
        return InExpr(negated, left, tuple(options),
                      _spanning(left.span, _token_span(reader.last_consumed)))
    if reader.at_keyword("like"):
        reader.advance()
        pattern = _parse_additive(reader, context)
        return Like(negated, left, pattern, _spanning(left.span, pattern.span))
    if reader.at_keyword("between"):
        reader.advance()
        lower = _parse_additive(reader, context)
        reader.expect_keyword("and", "between the BETWEEN bounds")
        upper = _parse_additive(reader, context)
        return Between(negated, left, lower, upper, _spanning(left.span, upper.span))
    return left


def _parse_additive(reader: _TokenReader, context: _ExprContext) -> Expr:
    start = reader.peek()
    if start is None:
        reader.fail("expected an expression")
    left = _parse_multiplicative(reader, context)
    while reader.at_operator("+") or reader.at_operator("-") or reader.at_operator("||"):
        op = reader.advance().value
        right = _parse_multiplicative(reader, context)
        left = Binary(op, left, right, _span_from(reader, start))
    return left


def _parse_multiplicative(reader: _TokenReader, context: _ExprContext) -> Expr:
    start = reader.peek()
    if start is None:
        reader.fail("expected an expression")
    left = _parse_unary(reader, context)
    while reader.at_operator("*") or reader.at_operator("/") or reader.at_operator("%"):
        op = reader.advance().value
        right = _parse_unary(reader, context)
        left = Binary(op, left, right, _span_from(reader, start))
    return left


def _parse_unary(reader: _TokenReader, context: _ExprContext) -> Expr:
    start = reader.peek()
    if start is None:
        reader.fail("expected an expression")
    if reader.at_operator("+") or reader.at_operator("-"):
        op = reader.advance().value
        operand = _parse_unary(reader, context)
        return Unary(op, operand, _span_from(reader, start))
    return _parse_primary(reader, context)


def _parse_primary(reader: _TokenReader, context: _ExprContext) -> Expr:
    token = reader.peek()
    if token is None:
        reader.fail("expected an expression")
    if token.type == "number":
        reader.advance()
        raw = token.value
        value: object = (float(raw) if "." in raw or "e" in raw or "E" in raw
                         else int(raw))
        return Literal(value, _token_span(token))
    if token.type == "string":
        reader.advance()
        return Literal(token.value, _token_span(token))
    if token.type == "keyword":
        if token.value in {"true", "false", "null"}:
            reader.advance()
            literal: object = None if token.value == "null" else token.value == "true"
            return Literal(literal, _token_span(token))
        if token.value == "case":
            return _parse_case(reader, context)
        if token.value == "cast":
            return _parse_cast(reader, context)
        reader.fail(f"unexpected keyword '{token.value}' in expression", token)
    if _is_identifier_token(token):
        return _parse_name_or_call(reader, context)
    if token.type == "punctuation" and token.value == "(":
        reader.advance()
        inner = _parse_expression(reader, context)
        reader.expect_punct(")", "to close the expression")
        return inner
    reader.fail(f"unexpected {_describe(token)} in expression", token)
    raise AssertionError("unreachable")


def _parse_name_or_call(reader: _TokenReader, context: _ExprContext) -> Expr:
    start = reader.peek()
    name_token = reader.advance()
    parts = [name_token.value]
    while reader.match_punct("."):
        parts.append(reader.expect_identifier("expected an identifier after '.'").value)
    lowered = name_token.value.lower()
    if not reader.at_punct("("):
        return Name(".".join(parts), _span_from(reader, start))
    if len(parts) > 1:
        reader.fail("function names cannot be qualified", name_token)
    reader.advance()
    distinct = reader.match_keyword("distinct")
    star = False
    empty_args = False
    args: list[Expr] = []
    if reader.at_operator("*"):
        reader.advance()
        star = True
    elif reader.at_punct(")"):
        empty_args = True
    else:
        args = _parse_expr_list(reader, context)
    reader.expect_punct(")", "to close the call")
    if reader.at_keyword("over"):
        reader.fail("a window function is only valid in a window stage", reader.peek())
    if empty_args:
        reader.fail("function calls need at least one argument (or COUNT(*))", name_token)
    if star and lowered != "count":
        reader.fail("only COUNT accepts '*'", name_token)
    if distinct and lowered != "count":
        reader.fail("DISTINCT is only valid for COUNT(DISTINCT expression)", name_token)
    if distinct and star:
        reader.fail("COUNT(DISTINCT *) is not valid; name a column instead", name_token)
    if lowered not in _AGGREGATES and lowered not in _SCALAR_FUNCTIONS:
        reader.fail(
            f"unknown function '{name_token.value}'; supported functions: "
            "COUNT, SUM, AVG, MIN, MAX, COALESCE, NULLIF, LOWER, UPPER, ABS, ROUND, "
            "LENGTH, TRIM, CONCAT, STRFTIME, DATE_TRUNC",
            name_token,
        )
    if not context.in_aggregate and lowered in _AGGREGATES:
        reader.fail(
            f"aggregate '{name_token.value.upper()}' is only valid inside an aggregate "
            "stage; reference the aggregate name instead",
            name_token,
        )
    return Call(name_token.value, distinct, star, tuple(args), _span_from(reader, start))


def _parse_case(reader: _TokenReader, context: _ExprContext) -> Expr:
    start = reader.peek()
    reader.advance()
    branches: list[tuple[Expr, Expr]] = []
    while reader.match_keyword("when"):
        when = _parse_expression(reader, context)
        reader.expect_keyword("then", "in the CASE branch")
        then = _parse_expression(reader, context)
        branches.append((when, then))
    if not branches:
        reader.fail("case requires at least one 'when' branch", start)
    else_expr = None
    if reader.match_keyword("else"):
        else_expr = _parse_expression(reader, context)
    reader.expect_keyword("end", "to close the case expression")
    return Case(tuple(branches), else_expr, _span_from(reader, start))


def _parse_type_parameter(reader: _TokenReader, type_name: str) -> str:
    token = reader.peek()
    if token is None or token.type != "number" or re.search(r"[.eE]", token.value):
        reader.fail(f"expected an integer type parameter like {type_name}(10,2)", token)
    reader.advance()
    return token.value


def _parse_cast(reader: _TokenReader, context: _ExprContext) -> Expr:
    start = reader.peek()
    reader.advance()
    reader.expect_punct("(", "after CAST")
    operand = _parse_expression(reader, context)
    reader.expect_keyword("as", "in the CAST expression")
    type_name = reader.expect_identifier("expected a type name after 'as'")
    rendered = type_name.value
    if reader.match_punct("("):
        first = _parse_type_parameter(reader, rendered)
        if reader.match_punct(","):
            second = _parse_type_parameter(reader, rendered)
            rendered = f"{rendered}({first},{second})"
        else:
            rendered = f"{rendered}({first})"
        reader.expect_punct(")", "to close the type parameters")
    reader.expect_punct(")", "to close the CAST expression")
    return Cast(operand, rendered, _span_from(reader, start))


def _parse_expr_list(reader: _TokenReader, context: _ExprContext) -> list[Expr]:
    items = [_parse_expression(reader, context)]
    while reader.match_punct(","):
        items.append(_parse_expression(reader, context))
    return items


def _contains_aggregate_call(expr: Expr) -> bool:
    if isinstance(expr, Call):
        if expr.name.lower() in _AGGREGATES:
            return True
        return any(_contains_aggregate_call(arg) for arg in expr.args)
    if isinstance(expr, Binary):
        return _contains_aggregate_call(expr.left) or _contains_aggregate_call(expr.right)
    if isinstance(expr, Unary):
        return _contains_aggregate_call(expr.operand)
    if isinstance(expr, Like):
        return _contains_aggregate_call(expr.value) or _contains_aggregate_call(expr.pattern)
    if isinstance(expr, InExpr):
        return _contains_aggregate_call(expr.value) or any(
            _contains_aggregate_call(option) for option in expr.options
        )
    if isinstance(expr, Between):
        return (
            _contains_aggregate_call(expr.value)
            or _contains_aggregate_call(expr.lower)
            or _contains_aggregate_call(expr.upper)
        )
    if isinstance(expr, IsNull):
        return _contains_aggregate_call(expr.value)
    if isinstance(expr, Case):
        return any(
            _contains_aggregate_call(when) or _contains_aggregate_call(then)
            for when, then in expr.branches
        ) or (expr.else_expr is not None and _contains_aggregate_call(expr.else_expr))
    if isinstance(expr, Cast):
        return _contains_aggregate_call(expr.operand)
    return False


def _reject_qualified_target(reader) -> None:
    """`create table lib.x` / `into lib.x`: writes only go to the --db database."""
    following = reader.peek()
    if following is not None and following.type == "punctuation" and following.value == ".":
        reader.fail("writes go to the --db database; attached databases are read-only, "
                    "so a qualified name can't be a write target", following)

