"""DuckDB SQL code generation for `.unsequel` programs.

Port of ``compiler/src/codegen.ts``. ``emit_sql`` lowers the stage IR to a
``WITH`` chain where each lowering unit (grammar §1.1) becomes one CTE: every
stage is its own unit except ``group by`` + ``aggregate``, which share one.
``stop_at`` truncates the program at a 1-based grammar stage ordinal, keeping
the whole unit that contains it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

from .errors import LocatedError
from .grammar import (
    AggregateStage,
    Between,
    Binary,
    Call,
    Case,
    Cast,
    CreateStage,
    DeriveStage,
    DistinctStage,
    Expr,
    FilterStage,
    FromStage,
    GroupByStage,
    HavingStage,
    InExpr,
    IntoStage,
    IsNull,
    JoinStage,
    Like,
    Literal,
    Name,
    Program,
    RawSqlStage,
    SelectStage,
    SkipStage,
    SortKey,
    SortStage,
    SourceRef,
    Stage,
    TakeStage,
    Unary,
    WindowCall,
    WindowStage,
    WithStage,
    SetStage,
    expression_name,
)
from .schema import Schema

__all__ = ["CodegenError", "emit_sql"]

_SAFE_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# DuckDB reserved words: identifiers that must be quoted when emitted.
_SQL_KEYWORDS = frozenset(
    [
        "all", "and", "anti", "any", "array", "as", "asc", "between", "both", "by",
        "case", "cast", "check", "column", "constraint", "create", "cross",
        "current", "default", "delete", "desc", "distinct", "do", "drop", "else",
        "end", "except", "exists", "false", "fetch", "filter", "for", "foreign",
        "from", "full", "function", "grant", "group", "having", "in", "inner",
        "insert", "intersect", "into", "is", "join", "lateral", "left", "like",
        "limit", "natural", "not", "null", "offset", "on", "or", "order", "outer",
        "over", "partition", "primary", "qualify", "range", "right", "row",
        "rows", "select", "set", "some", "table", "then", "to", "true", "trailing",
        "union", "unique", "update", "using", "values", "when", "where", "window",
        "with",
    ]
)

_INPUT_NAME = "__input__"
_INPUT_NAME_LENGTH = len(_INPUT_NAME)


class CodegenError(LocatedError):
    """A program that cannot be lowered to SQL."""


def _ident(name: str) -> str:
    if _SAFE_IDENT.match(name) and name.lower() not in _SQL_KEYWORDS:
        return name
    return '"' + name.replace('"', '""') + '"'


def _qualified(name: str) -> str:
    return ".".join(_ident(part) for part in name.split("."))


def _stage_error(stage: Stage, message: str) -> CodegenError:
    return CodegenError(
        f"stage {stage.index} ({stage.keyword}): {message}",
        stage.span.start.line,
        stage.span.start.column,
    )


class _Cols:
    """Resolves a program-level column name to a SQL token for one CTE."""

    __slots__ = ("lookup", "opaque", "ambiguous")

    def __init__(
        self,
        lookup: dict[str, str] | None = None,
        opaque: bool = False,
        ambiguous: "set[str] | None" = None,
    ) -> None:
        self.lookup = lookup if lookup is not None else {}
        self.opaque = opaque
        self.ambiguous = ambiguous if ambiguous is not None else set()

    def resolve(self, name: str) -> str:
        if "." not in name and name.lower() in self.ambiguous:
            raise CodegenError(
                f"column '{name}' is ambiguous after a join; qualify it with a source label"
            )
        direct = self.lookup.get(name)
        if direct is not None:
            return direct
        if self.opaque:
            # After a sql stage the columns are the author's responsibility.
            return _qualified(name)
        raise CodegenError(f"cannot resolve column '{name}' in this stage")

    @staticmethod
    def flat(names: "list[str] | tuple[str, ...]") -> "_Cols":
        return _Cols({name: _ident(name) for name in names})


def _aliased(rendered: str, name: str) -> str:
    return rendered if rendered == _ident(name) else f"{rendered} AS {_ident(name)}"


# --------------------------------------------------------------------------- #
# Expression rendering
# --------------------------------------------------------------------------- #


def _number_text(value: float) -> str:
    if value.is_integer() and abs(value) < 1e21:
        return str(int(value))
    return repr(value)


def _literal(value: object) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return _number_text(value) if isinstance(value, float) else str(value)
    return "'" + str(value).replace("'", "''") + "'"


def _render(expr: Expr, cols: _Cols) -> str:
    if isinstance(expr, Literal):
        return _literal(expr.value)
    if isinstance(expr, Name):
        return cols.resolve(expr.name)
    if isinstance(expr, Unary):
        operand = _render(expr.operand, cols)
        return f"(NOT {operand})" if expr.op == "not" else f"({expr.op}{operand})"
    if isinstance(expr, Binary):
        op = expr.op.upper()
        return f"({_render(expr.left, cols)} {op} {_render(expr.right, cols)})"
    if isinstance(expr, Like):
        keyword = "NOT LIKE" if expr.negated else "LIKE"
        return f"({_render(expr.value, cols)} {keyword} {_render(expr.pattern, cols)})"
    if isinstance(expr, InExpr):
        keyword = "NOT IN" if expr.negated else "IN"
        options = ", ".join(_render(option, cols) for option in expr.options)
        return f"({_render(expr.value, cols)} {keyword} ({options}))"
    if isinstance(expr, Between):
        keyword = "NOT BETWEEN" if expr.negated else "BETWEEN"
        return (
            f"({_render(expr.value, cols)} {keyword} "
            f"{_render(expr.lower, cols)} AND {_render(expr.upper, cols)})"
        )
    if isinstance(expr, IsNull):
        keyword = "IS NOT NULL" if expr.negated else "IS NULL"
        return f"({_render(expr.value, cols)} {keyword})"
    if isinstance(expr, Case):
        parts = ["CASE"]
        for when, then in expr.branches:
            parts.append(f"WHEN {_render(when, cols)} THEN {_render(then, cols)}")
        if expr.else_expr is not None:
            parts.append(f"ELSE {_render(expr.else_expr, cols)}")
        parts.append("END")
        return f"({' '.join(parts)})"
    if isinstance(expr, Cast):
        return f"CAST({_render(expr.operand, cols)} AS {expr.type_name})"
    if isinstance(expr, Call):
        if expr.star:
            return f"{expr.name.upper()}(*)"
        prefix = "DISTINCT " if expr.distinct else ""
        args = ", ".join(_render(arg, cols) for arg in expr.args)
        return f"{expr.name.upper()}({prefix}{args})"
    raise CodegenError("unknown expression node")  # pragma: no cover - defensive


def _render_window(call: WindowCall, cols: _Cols) -> str:
    args = "*" if call.star else ", ".join(_render(arg, cols) for arg in call.args)
    parts: list[str] = []
    if call.partition_by:
        parts.append("PARTITION BY " + ", ".join(_render(expr, cols) for expr in call.partition_by))
    if call.order_by:
        parts.append("ORDER BY " + ", ".join(_render_sort_key(key, cols) for key in call.order_by))
    return f"{call.func.upper()}({args}) OVER ({' '.join(parts)})"


def _render_sort_key(key: SortKey, cols: _Cols) -> str:
    return _render(key.expr, cols) + (" DESC" if key.descending else "")


# --------------------------------------------------------------------------- #
# __input__ substitution (strings, comments, and quoted identifiers aware)
# --------------------------------------------------------------------------- #


def _scan_quoted(text: str, start: int, quote: str) -> int:
    i = start + 1
    while i < len(text):
        char = text[i]
        if char == "\\" and i + 1 < len(text):
            i += 2
            continue
        if char == quote:
            if i + 1 < len(text) and text[i + 1] == quote:
                i += 2
                continue
            return i + 1
        i += 1
    return len(text)


def _is_word_char(char: "str | None") -> bool:
    return char is not None and (char.isalnum() or char == "_")


def _substitute_input(text: str, replacement: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(text):
        char = text[i]
        two = text[i:i + 2]
        if two == "--":
            newline = text.find("\n", i)
            end = len(text) if newline < 0 else newline
            out.append(text[i:end])
            i = end
            continue
        if two == "/*":
            close = text.find("*/", i + 2)
            end = len(text) if close < 0 else close + 2
            out.append(text[i:end])
            i = end
            continue
        if char == "'":
            end = _scan_quoted(text, i, "'")
            out.append(text[i:end])
            i = end
            continue
        if char == '"':
            end = _scan_quoted(text, i, '"')
            if text[i + 1:end - 1] == _INPUT_NAME:
                out.append(replacement)
            else:
                out.append(text[i:end])
            i = end
            continue
        if (
            text.startswith(_INPUT_NAME, i)
            and not _is_word_char(text[i - 1] if i > 0 else None)
            and not _is_word_char(text[i + _INPUT_NAME_LENGTH] if i + _INPUT_NAME_LENGTH < len(text) else None)
        ):
            out.append(replacement)
            i += _INPUT_NAME_LENGTH
            continue
        out.append(char)
        i += 1
    return "".join(out)


# --------------------------------------------------------------------------- #
# Stage lowering
# --------------------------------------------------------------------------- #


@dataclass
class _StageResult:
    sql: str
    cols: _Cols
    live: list[str]


def _label_of(source: SourceRef) -> str:
    return source.alias or source.name.split(".")[-1]


def _source_sql(source: SourceRef) -> str:
    name = _qualified(source.name)
    return name if source.alias is None else f"{name} AS {_ident(source.alias)}"


def _from_stage(stage: FromStage, schema: Schema) -> _StageResult:
    table = schema.tables.get(stage.source.name)
    if table is None:
        raise _stage_error(stage, f"unknown table '{stage.source.name}'")
    label = _label_of(stage.source)
    lookup: dict[str, str] = {}
    for column in table.columns:
        lookup[column] = _ident(column)
        lookup[f"{label}.{column}"] = _ident(column)
    return _StageResult(
        f"SELECT * FROM {_source_sql(stage.source)}",
        _Cols(lookup),
        list(table.columns),
    )


@dataclass
class _ResolvedRight:
    sql: str
    label: str
    columns: list[str]


def _right_for_table(stage: JoinStage, schema: Schema) -> _ResolvedRight:
    table = schema.tables.get(stage.source.name)
    if table is None:
        raise _stage_error(stage, f"unknown joined table '{stage.source.name}'")
    return _ResolvedRight(_source_sql(stage.source), _label_of(stage.source),
                          list(table.columns))


def _join_stage(
    stage: JoinStage,
    prev_cte: str,
    prev: _Cols,
    live: list[str],
    right: _ResolvedRight,
) -> _StageResult:
    label = right.label

    # ON runs in the join's own scope: bare names resolve to the previous CTE,
    # qualified names to whichever side owns them (left labels included).
    on_lookup: dict[str, str] = {}
    on_ambiguous = set(prev.ambiguous)
    for name in live:
        on_lookup[name] = f"{prev_cte}.{_ident(name)}"
        on_lookup[f"{prev_cte}.{name}"] = f"{prev_cte}.{_ident(name)}"
    for name, exposed in prev.lookup.items():
        if "." in name:
            on_lookup[name] = f"{prev_cte}.{exposed}"
    for column in right.columns:
        on_lookup[f"{label}.{column}"] = f"{_ident(label)}.{_ident(column)}"
        if any(name.lower() == column.lower() for name in live):
            on_ambiguous.add(column.lower())
    on_sql = (
        ""
        if stage.join_kind == "cross"
        else _render(stage.on, _Cols(on_lookup, False, on_ambiguous))
    )

    taken = {name.lower() for name in live}
    ambiguous = set(prev.ambiguous)
    projected = [f"{prev_cte}.*"]
    lookup = dict(prev.lookup)
    new_live = list(live)
    for column in right.columns:
        collision = column.lower() in taken
        if collision:
            ambiguous.add(column.lower())
        exposed = f"{label}_{column}" if collision else column
        if exposed.lower() in taken:
            raise _stage_error(
                stage,
                f"join exposes duplicate column '{exposed}'; alias one of the joins",
            )
        if exposed == column:
            projected.append(f"{_ident(label)}.{_ident(column)}")
        else:
            projected.append(f"{_ident(label)}.{_ident(column)} AS {_ident(exposed)}")
        taken.add(exposed.lower())
        new_live.append(exposed)
        if column not in lookup:
            lookup[column] = _ident(exposed)
        lookup[f"{label}.{column}"] = _ident(exposed)

    keyword = {
        "inner": "JOIN",
        "left": "LEFT JOIN",
        "right": "RIGHT JOIN",
        "full": "FULL OUTER JOIN",
        "cross": "CROSS JOIN",
    }[stage.join_kind]
    on_part = "" if stage.join_kind == "cross" else f" ON {on_sql}"
    sql = (
        f"SELECT {', '.join(projected)} FROM {prev_cte} {keyword} {right.sql}{on_part}"
    )
    return _StageResult(sql, _Cols(lookup, False, ambiguous), new_live)


def _from_resolved(sql: str, label: str, live: list[str], source: _Cols) -> _StageResult:
    """`SELECT * FROM <cte> AS <label>` with the label's columns addressable."""
    lookup: dict[str, str] = {}
    for name in live:
        lookup[name] = _ident(name)
        if label != "":
            lookup[f"{label}.{name}"] = _ident(name)
    aliased = sql if label == "" else f"{sql} AS {_ident(label)}"
    return _StageResult(
        f"SELECT * FROM {aliased}",
        _Cols(lookup, source.opaque, source.ambiguous),
        list(live),
    )


def _derive_stage(
    stage: DeriveStage, prev_cte: str, cols: _Cols, live: list[str]
) -> _StageResult:
    for item in stage.items:
        if item.name in live:
            raise _stage_error(
                stage,
                f"derive '{item.name}' conflicts with a live column; choose another name",
            )
    # Items resolve left-to-right. SQL aliases are not visible inside one
    # SELECT list, so an earlier item is inlined into later item expressions.
    scope = dict(cols.lookup)
    additions: list[str] = []
    for item in stage.items:
        rendered = _render(item.expr, _Cols(scope, cols.opaque, cols.ambiguous))
        additions.append(_aliased(rendered, item.name))
        scope[item.name] = rendered
    lookup = dict(cols.lookup)
    for item in stage.items:
        lookup[item.name] = _ident(item.name)
    return _StageResult(
        f"SELECT *, {', '.join(additions)} FROM {prev_cte}",
        _Cols(lookup, cols.opaque, cols.ambiguous),
        list(live) + [item.name for item in stage.items],
    )


def _group_unit_sql(
    group: GroupByStage,
    aggregate: AggregateStage,
    prev_cte: str,
    cols: _Cols,
) -> _StageResult:
    rendered_keys = [_render(key.expr, cols) for key in group.keys]
    keys = [_aliased(rendered, key.name) for rendered, key in zip(rendered_keys, group.keys)]
    group_by = ", ".join(rendered_keys)
    aggregates = ", ".join(
        _aliased(_render(item.call, cols), item.name) for item in aggregate.items
    )
    names = [key.name for key in group.keys] + [item.name for item in aggregate.items]
    lookup = _Cols.flat(names).lookup
    for index, key in enumerate(group.keys):
        rendered = rendered_keys[index]
        for input_name, sql_name in cols.lookup.items():
            if "." in input_name and sql_name == rendered:
                lookup[input_name] = _ident(key.name)
    return _StageResult(
        f"SELECT {', '.join([*keys, aggregates])} FROM {prev_cte} GROUP BY {group_by}",
        _Cols(lookup),
        names,
    )


def _single_stage_sql(
    stage: Stage,
    prev_cte: str,
    cols: _Cols,
    live: list[str],
    order_column: "str | None",
) -> _StageResult:
    if isinstance(stage, FilterStage) or isinstance(stage, HavingStage):
        return _StageResult(
            f"SELECT * FROM {prev_cte} WHERE {_render(stage.condition, cols)}", cols, live
        )
    if isinstance(stage, WindowStage):
        if stage.name in live:
            raise _stage_error(
                stage,
                f"window '{stage.name}' conflicts with a live column; choose another name",
            )
        lookup = dict(cols.lookup)
        lookup[stage.name] = _ident(stage.name)
        sql = (
            f"SELECT *, {_render_window(stage.call, cols)} AS {_ident(stage.name)} "
            f"FROM {prev_cte}"
        )
        return _StageResult(sql, _Cols(lookup, cols.opaque, cols.ambiguous),
                            [*live, stage.name])
    if isinstance(stage, SelectStage):
        if stage.star and not stage.items:
            return _StageResult(f"SELECT * FROM {prev_cte}", _Cols.flat(live), live)
        item_names = [item.alias or expression_name(item.expr) for item in stage.items]
        if stage.star:
            live_names = {name.lower() for name in live}
            duplicate = next(
                (name for name in item_names if name.lower() in live_names), None
            )
            if duplicate is not None:
                raise _stage_error(
                    stage,
                    f"select output '{duplicate}' duplicates a live column from '*'; "
                    "choose a different alias",
                )
        names = [*live, *item_names] if stage.star else list(item_names)
        rendered = [
            _aliased(_render(item.expr, cols), item_names[i])
            for i, item in enumerate(stage.items)
        ]
        if stage.star:
            rendered.insert(0, "*")
        if order_column is not None and not stage.star:
            rendered.append(
                f"{prev_cte}.{_ident(order_column)} AS {_ident(order_column)}"
            )
        return _StageResult(
            f"SELECT {', '.join(rendered)} FROM {prev_cte}", _Cols.flat(names), names
        )
    if isinstance(stage, SkipStage):
        order = "" if order_column is None else f" ORDER BY {_ident(order_column)}"
        return _StageResult(
            f"SELECT * FROM {prev_cte}{order} OFFSET {stage.count}", cols, live
        )
    if isinstance(stage, TakeStage):
        order = "" if order_column is None else f" ORDER BY {_ident(order_column)}"
        return _StageResult(
            f"SELECT * FROM {prev_cte}{order} LIMIT {stage.count}", cols, live
        )
    if isinstance(stage, DistinctStage):
        exclusion = "" if order_column is None else f" EXCLUDE ({_ident(order_column)})"
        return _StageResult(f"SELECT DISTINCT *{exclusion} FROM {prev_cte}", cols, live)
    if isinstance(stage, RawSqlStage):
        input_sql = (
            prev_cte
            if order_column is None
            else f"(SELECT * EXCLUDE ({_ident(order_column)}) FROM {prev_cte})"
        )
        text = _substitute_input(stage.text, input_sql)
        indented = text.replace("\n", "\n  ")
        return _StageResult(indented, _Cols({}, True), [])
    raise _stage_error(stage, f"cannot lower stage '{stage.keyword}'")


def _sort_stage_sql(
    stage: SortStage,
    prev_cte: str,
    cols: _Cols,
    order_column: "str | None",
    next_order_column: str,
) -> str:
    try:
        rendered = [_render_sort_key(key, cols) for key in stage.keys]
        if order_column is not None:
            rendered.append(f"{_ident(order_column)} ASC")
        keys = ", ".join(rendered)
    except CodegenError:
        raise _stage_error(
            stage,
            "sort key is no longer available after an earlier projection; "
            "sort again after select",
        ) from None
    input_sql = (
        "*" if order_column is None else f"* EXCLUDE ({_ident(order_column)})"
    )
    return (
        f"SELECT {input_sql}, ROW_NUMBER() OVER (ORDER BY {keys}) AS "
        f"{_ident(next_order_column)} FROM {prev_cte}"
    )


def _internal_order_names(program: Program, schema: Schema) -> set[str]:
    names: set[str] = set()
    for table in schema.tables.values():
        for column in table.columns:
            names.add(column.lower())

    def collect(stages: Program) -> None:
        for stage in stages:
            if isinstance(stage, (WithStage, SetStage)):
                collect(stage.program)
            elif isinstance(stage, (FromStage, JoinStage)):
                if stage.source.alias is not None:
                    names.add(stage.source.alias.lower())
                if stage.source.program is not None:
                    collect(stage.source.program)
            elif isinstance(stage, DeriveStage):
                for item in stage.items:
                    names.add(item.name.lower())
            elif isinstance(stage, GroupByStage):
                for item in stage.keys:
                    names.add(item.name.lower())
            elif isinstance(stage, AggregateStage):
                for item in stage.items:
                    names.add(item.name.lower())
            elif isinstance(stage, WindowStage):
                names.add(stage.name.lower())
            elif isinstance(stage, SelectStage):
                for item in stage.items:
                    names.add((item.alias or expression_name(item.expr)).lower())
            elif isinstance(stage, RawSqlStage):
                names.add(stage.text.lower())

    collect(program)
    return names


# --------------------------------------------------------------------------- #
# Program lowering
# --------------------------------------------------------------------------- #


def _truncate(program: Program, stop_at: "int | None") -> Program:
    if stop_at is None:
        return program
    if stop_at < 1:
        raise CodegenError("stopAt must be a positive grammar stage ordinal")
    limit = min(stop_at, len(program))
    stages = program[:limit]
    last = stages[-1] if stages else None
    nxt = program[limit] if limit < len(program) else None
    if isinstance(last, GroupByStage) and isinstance(nxt, AggregateStage):
        return [*stages, nxt]
    return stages


@dataclass
class _ChainState:
    last_cte: str
    cols: _Cols
    live: list[str]
    order_column: "str | None"


@dataclass
class _ChainContext:
    schema: Schema
    ctes: list[tuple[str, str]] = field(default_factory=list)
    next_order_name: Callable[[], str] = lambda: ""  # set by emit_sql


def _build_chain(
    stages: Program,
    context: _ChainContext,
    prefix: str,
    nested: bool,
    inherited_withs: dict[str, _ChainState],
) -> _ChainState:
    with_names = dict(inherited_withs)
    state: "_ChainState | None" = None
    order_column: "str | None" = None
    position = 0
    ordinal = 0

    while position < len(stages):
        stage = stages[position]
        if isinstance(stage, WithStage):
            inner = _build_chain(
                stage.program, context, f"{prefix}with_{stage.index}_", True, with_names
            )
            with_names[stage.name] = inner
            if state is None:
                state = inner
            position += 1
            continue

        ordinal += 1
        name = f"{prefix}stage_{ordinal}"
        prev_cte = f"{prefix}stage_0" if state is None else state.last_cte
        cols = state.cols if state is not None else _Cols()
        live = state.live if state is not None else []

        if isinstance(stage, FromStage):
            source = stage.source
            if source.program is not None:
                inner = _build_chain(
                    source.program, context, f"{prefix}sub_{stage.index}_", True, with_names
                )
                result = _from_resolved(
                    inner.last_cte, source.alias or "", inner.live, inner.cols
                )
            elif source.name in with_names:
                inner = with_names[source.name]
                result = _from_resolved(
                    inner.last_cte, source.alias or source.name, inner.live, inner.cols
                )
            else:
                result = _from_stage(stage, context.schema)
            order_column = None
        elif isinstance(stage, JoinStage):
            source = stage.source
            if source.program is not None:
                inner = _build_chain(
                    source.program, context, f"{prefix}join_{stage.index}_", True, with_names
                )
                label = source.alias or ""
                right = _ResolvedRight(
                    inner.last_cte if label == "" else f"{inner.last_cte} AS {_ident(label)}",
                    label,
                    inner.live,
                )
            elif source.name in with_names:
                inner = with_names[source.name]
                label = source.alias or source.name
                right = _ResolvedRight(f"{inner.last_cte} AS {_ident(label)}", label, inner.live)
            else:
                right = _right_for_table(stage, context.schema)
            result = _join_stage(stage, prev_cte, cols, live, right)
        elif isinstance(stage, SetStage):
            inner = _build_chain(
                stage.program, context, f"{prefix}set_{stage.index}_", True, with_names
            )
            keyword = stage.op.upper()
            left = (
                f"SELECT * FROM {prev_cte}"
                if order_column is None
                else f"SELECT * EXCLUDE ({_ident(order_column)}) FROM {prev_cte}"
            )
            result = _StageResult(
                f"{left} {keyword} SELECT * FROM {inner.last_cte}", cols, live
            )
            order_column = None
        elif isinstance(stage, GroupByStage):
            aggregate = stages[position + 1] if position + 1 < len(stages) else None
            if not isinstance(aggregate, AggregateStage):
                raise _stage_error(stage, "group by must be immediately followed by aggregate")
            result = _group_unit_sql(stage, aggregate, prev_cte, cols)
            order_column = None
            position += 1
        elif isinstance(stage, SortStage):
            next_order = context.next_order_name()
            result = _StageResult(
                _sort_stage_sql(stage, prev_cte, cols, order_column, next_order),
                cols,
                live,
            )
            order_column = next_order
        elif isinstance(stage, (DistinctStage, RawSqlStage)):
            result = _single_stage_sql(stage, prev_cte, cols, live, order_column)
            order_column = None
        elif isinstance(stage, DeriveStage):
            result = _derive_stage(stage, prev_cte, cols, live)
        else:
            result = _single_stage_sql(stage, prev_cte, cols, live, order_column)

        context.ctes.append((name, result.sql))
        state = _ChainState(name, result.cols, result.live, order_column)
        position += 1

    if state is None:
        raise CodegenError(
            "a program must define a relation with a from stage or a with definition"
        )
    if nested and state.order_column is not None:
        out = f"{prefix}out"
        context.ctes.append(
            (out, f"SELECT * EXCLUDE ({_ident(state.order_column)}) FROM {state.last_cte}")
        )
        return _ChainState(out, state.cols, state.live, None)
    return state


def _order_name_factory(program: Program, schema: Schema):
    reserved_names = _internal_order_names(program, schema)
    raw_sql_texts = [stage.text.lower() for stage in program
                     if isinstance(stage, RawSqlStage)]
    counter = {"ordinal": 0}

    def next_order_name() -> str:
        candidate = ""
        while True:
            counter["ordinal"] += 1
            candidate = f"__unsequel_internal_order_{counter['ordinal']}"
            lowered = candidate.lower()
            if lowered not in reserved_names and not any(
                lowered in text for text in raw_sql_texts
            ):
                break
        reserved_names.add(candidate.lower())
        return candidate

    return next_order_name


def _emit_statements(program: Program, schema: Schema) -> str:
    """Statement programs (`create`/`into`): one complete statement per write
    stage, each nested program embedded as an inline `WITH` block."""
    next_order_name = _order_name_factory(program, schema)
    statements: list[str] = []
    for stage in program:
        if not isinstance(stage, (CreateStage, IntoStage)):
            continue
        context = _ChainContext(schema, [], next_order_name)
        with_names: dict = {}
        for candidate in program:
            if not isinstance(candidate, WithStage):
                continue
            inner = _build_chain(candidate.program, context,
                                 f"with_{candidate.index}_", True, with_names)
            with_names[candidate.name] = inner
        kind = "create" if isinstance(stage, CreateStage) else "into"
        inner = _build_chain(stage.program, context, f"{kind}_{stage.index}_",
                             True, with_names)
        body = ",\n".join(f"{name} AS (\n  {sql}\n)" for name, sql in context.ctes)
        select = f"SELECT * FROM {inner.last_cte}"
        if isinstance(stage, CreateStage):
            statements.append(
                f"CREATE OR REPLACE {stage.object.upper()} {_ident(stage.name)} AS\n"
                f"WITH {body}\n{select};")
        else:
            statements.append(f"INSERT INTO {_ident(stage.name)}\nWITH {body}\n{select};")
    return "\n".join(statements)


def emit_sql(program: Program, schema: Schema, stop_at: "int | None" = None) -> str:
    """Lower a program to a DuckDB `WITH` chain (compiler/src/codegen.ts)."""
    if any(isinstance(stage, (CreateStage, IntoStage)) for stage in program):
        return _emit_statements(program, schema)
    stages = _truncate(program, stop_at)
    if not stages:
        raise CodegenError("a program cannot be empty")

    context = _ChainContext(schema, [], _order_name_factory(program, schema))
    state = _build_chain(stages, context, "", False, {})
    body = ",\n".join(f"{name} AS (\n  {sql}\n)" for name, sql in context.ctes)
    if state.order_column is None:
        final = f"SELECT * FROM {state.last_cte};"
    else:
        final = (
            f"SELECT * EXCLUDE ({_ident(state.order_column)}) FROM {state.last_cte} "
            f"ORDER BY {_ident(state.order_column)};"
        )
    return f"WITH {body}\n{final}"
