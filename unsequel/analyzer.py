"""Schema-aware validation of a `.unsequel` program.

Port of ``compiler/src/semantics.ts``. ``analyze`` walks a parsed program
stage by stage against a :class:`~unsequel.schema.Schema`, tracking which
columns are alive at each point, and raises a stage-located
:class:`SemanticError` for:

- an unknown source or joined table,
- a reference to a column that is not alive at that stage,
- ``SUM`` / ``AVG`` over a non-numeric column,
- a post-``group by`` reference to something that is neither a group key nor
  an aggregate,
- a ``derive`` or ``window`` that redefines a live column.

A ``sql`` stage is an integrity boundary: later stages are reported as
``<opaque>`` without further checks. ``select *`` passes the scope through
unchanged. ``analyze`` returns the per-stage column lineage.
"""

from __future__ import annotations

from dataclasses import dataclass

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
    RawSqlStage,
    SelectItem,
    SelectStage,
    SetStage,
    SkipStage,
    SortStage,
    SourceRef,
    Stage,
    TakeStage,
    Unary,
    WindowCall,
    WindowStage,
    WithStage,
    expression_name,
)
from .schema import Schema, type_family, with_created_table

__all__ = ["SemanticError", "StageLineage", "analyze"]


class SemanticError(LocatedError):
    """A `.unsequel` program that parses but is wrong against the schema."""


@dataclass(frozen=True)
class StageLineage:
    index: int
    keyword: str
    columns: tuple[str, ...]


class _Scope:
    __slots__ = ("families", "ambiguous")

    def __init__(self) -> None:
        self.families: dict[str, str] = {}
        self.ambiguous: set[str] = set()

    def known(self, name: str) -> bool:
        return name in self.families

    def family(self, name: str) -> str:
        return self.families.get(name, "unknown")

    def set(self, name: str, family: str) -> None:
        self.families[name] = family

    def mark_ambiguous(self, name: str) -> None:
        self.ambiguous.add(name.lower())

    def is_ambiguous(self, name: str) -> bool:
        return name.lower() in self.ambiguous

    def entries(self) -> list[tuple[str, str]]:
        return list(self.families.items())

    def snapshot(self) -> dict[str, str]:
        return dict(self.families)

    def bare_names(self) -> list[str]:
        return [name for name in self.families if "." not in name]


def _semantic_error(stage: Stage, message: str) -> SemanticError:
    return SemanticError(
        message,
        stage.span.start.line,
        stage.span.start.column,
        stage_index=stage.index,
        keyword=stage.keyword,
    )


def _identifiers(expr: Expr, out: "set[str] | None" = None) -> set[str]:
    if out is None:
        out = set()
    if isinstance(expr, Name):
        out.add(expr.name)
    elif isinstance(expr, (Literal,)):
        pass
    elif isinstance(expr, Binary):
        _identifiers(expr.left, out)
        _identifiers(expr.right, out)
    elif isinstance(expr, Unary):
        _identifiers(expr.operand, out)
    elif isinstance(expr, Like):
        _identifiers(expr.value, out)
        _identifiers(expr.pattern, out)
    elif isinstance(expr, InExpr):
        _identifiers(expr.value, out)
        for option in expr.options:
            _identifiers(option, out)
    elif isinstance(expr, Between):
        _identifiers(expr.value, out)
        _identifiers(expr.lower, out)
        _identifiers(expr.upper, out)
    elif isinstance(expr, IsNull):
        _identifiers(expr.value, out)
    elif isinstance(expr, Case):
        for when, then in expr.branches:
            _identifiers(when, out)
            _identifiers(then, out)
        if expr.else_expr is not None:
            _identifiers(expr.else_expr, out)
    elif isinstance(expr, Cast):
        _identifiers(expr.operand, out)
    elif isinstance(expr, Call):
        for arg in expr.args:
            _identifiers(arg, out)
    return out


def _check_refs(expr: Expr, scope: _Scope, stage: Stage, role: str) -> None:
    for name in _identifiers(expr):
        if "." not in name and scope.is_ambiguous(name):
            raise _semantic_error(
                stage,
                f"{role} references ambiguous column '{name}'; qualify it with a source label",
            )
        if not scope.known(name):
            raise _semantic_error(stage, f"{role} references unknown column '{name}'")


def _select_name(item: SelectItem) -> str:
    return item.alias or expression_name(item.expr)


def _infer(expr: Expr, scope: _Scope) -> str:
    if isinstance(expr, Literal):
        value = expr.value
        if isinstance(value, bool):
            return "boolean"
        if isinstance(value, (int, float)):
            return "numeric"
        if isinstance(value, str):
            return "text"
        return "unknown"
    if isinstance(expr, Name):
        return scope.family(expr.name)
    if isinstance(expr, Unary):
        return "boolean" if expr.op == "not" else "numeric"
    if isinstance(expr, Binary):
        if expr.op in {"+", "-", "*", "/", "%"}:
            return "numeric"
        if expr.op == "||":
            return "text"
        return "boolean"
    if isinstance(expr, (Like, InExpr, Between, IsNull)):
        return "boolean"
    if isinstance(expr, Call):
        name = expr.name.upper()
        if name in {"SUM", "AVG", "COUNT", "ABS", "ROUND", "LENGTH"}:
            return "numeric"
        if name == "STRFTIME":
            return "text"
        if name == "DATE_TRUNC":
            return "temporal"
        if name in {"MIN", "MAX", "COALESCE"} and expr.args:
            return _infer(expr.args[0], scope)
        if name in {"LOWER", "UPPER", "TRIM", "CONCAT"}:
            return "text"
        return "unknown"
    if isinstance(expr, Case):
        families = {_infer(then, scope) for _, then in expr.branches}
        if expr.else_expr is not None:
            families.add(_infer(expr.else_expr, scope))
        families.discard("unknown")
        if len(families) != 1:
            return "unknown"
        return next(iter(families))
    if isinstance(expr, Cast):
        return type_family(expr.type_name)
    return "unknown"


def _window_family(call: WindowCall, scope: _Scope) -> str:
    name = call.func.upper()
    if name in {"SUM", "AVG", "COUNT", "ROW_NUMBER", "RANK", "DENSE_RANK"}:
        return "numeric"
    if name in {"MIN", "MAX", "LAG", "LEAD"} and call.args:
        return _infer(call.args[0], scope)
    return "unknown"


@dataclass
class _Analysis:
    lineage: list[StageLineage]
    columns: list[str]
    families: dict[str, str]
    opaque: bool


@dataclass(frozen=True)
class _ResolvedSource:
    label: str
    families: dict[str, str]


def _table_families(columns: dict[str, str]) -> dict[str, str]:
    return {column: type_family(declared) for column, declared in columns.items()}


def _resolve_source(
    source: SourceRef,
    schema: Schema,
    ctes: dict[str, _Analysis],
    stage: Stage,
    role: str,
) -> _ResolvedSource:
    if source.program is not None:
        inner = _analyze_program(source.program, schema, ctes)
        return _ResolvedSource(source.alias or "", inner.families)
    cte = ctes.get(source.name)
    if cte is not None:
        return _ResolvedSource(source.alias or source.name, cte.families)
    table = schema.tables.get(source.name)
    if table is None:
        if role == "from":
            raise _semantic_error(stage, f"unknown table '{source.name}'")
        raise _semantic_error(stage, f"unknown joined table '{source.name}'")
    label = source.alias or source.name.split(".")[-1]
    return _ResolvedSource(label, _table_families(table.columns))


def _add_source_columns(scope: _Scope, source: _ResolvedSource) -> None:
    for column, family in source.families.items():
        if "." in column:
            continue
        scope.set(f"{source.label}.{column}", family)
        scope.set(column, family)


def _analyze_program(
    stages: list[Stage], schema: Schema, ctes: dict[str, _Analysis]
) -> _Analysis:
    scope = _Scope()
    opaque = False
    group_row_scope: _Scope | None = None
    lineage: list[StageLineage] = []

    for position, stage in enumerate(stages):
        if opaque:
            lineage.append(StageLineage(stage.index, stage.keyword, ("<opaque>",)))
            continue

        if isinstance(stage, WithStage):
            inner = _analyze_program(stage.program, schema, ctes)
            ctes[stage.name] = inner
            lineage.append(StageLineage(stage.index, stage.keyword, tuple(inner.columns)))
            continue
        if isinstance(stage, FromStage):
            _add_source_columns(scope, _resolve_source(stage.source, schema, ctes, stage, "from"))
        elif isinstance(stage, JoinStage):
            resolved = _resolve_source(stage.source, schema, ctes, stage, "join")
            label = resolved.label
            taken = set(scope.bare_names())
            for column, family in resolved.families.items():
                if "." in column:
                    continue
                scope.set(f"{label}.{column}", family)
                if scope.known(column):
                    scope.mark_ambiguous(column)
                exposed = f"{label}_{column}" if column in taken else column
                if exposed in taken:
                    raise _semantic_error(
                        stage,
                        f"join exposes duplicate column '{exposed}'; alias one of the joins",
                    )
                scope.set(exposed, family)
                taken.add(exposed)
            _check_refs(stage.on, scope, stage, "join condition")
        elif isinstance(stage, FilterStage):
            _check_refs(stage.condition, scope, stage, "filter condition")
        elif isinstance(stage, DeriveStage):
            for item in stage.items:
                _check_refs(item.expr, scope, stage, f"derive '{item.name}'")
                if scope.known(item.name):
                    raise _semantic_error(
                        stage,
                        f"derive '{item.name}' conflicts with a live column; "
                        "choose another name",
                    )
                scope.set(item.name, _infer(item.expr, scope))
        elif isinstance(stage, GroupByStage):
            group_scope = _Scope()
            for key in stage.keys:
                _check_refs(key.expr, scope, stage, f"group key '{key.name}'")
                family = _infer(key.expr, scope)
                group_scope.set(key.name, family)
                if isinstance(key.expr, Name):
                    if "." in key.expr.name:
                        group_scope.set(key.expr.name, family)
                    else:
                        for name, source_family in scope.entries():
                            if "." in name and name.split(".")[-1].lower() == key.expr.name.lower():
                                group_scope.set(name, source_family)
            group_row_scope = scope
            scope = group_scope
        elif isinstance(stage, AggregateStage):
            row_scope = group_row_scope if group_row_scope is not None else scope
            for item in stage.items:
                _check_refs(item.call, row_scope, stage, f"aggregate '{item.name}'")
                if scope.known(item.name):
                    raise _semantic_error(
                        stage,
                        f"aggregate '{item.name}' conflicts with the group key of the same name",
                    )
                fname = item.call.name.upper()
                if fname in {"SUM", "AVG"} and item.call.args:
                    arg_family = _infer(item.call.args[0], row_scope)
                    if arg_family not in {"numeric", "unknown"}:
                        raise _semantic_error(
                            stage,
                            f"{fname} over '{item.name}' needs a numeric column, "
                            f"got {arg_family}",
                        )
                scope.set(item.name, _infer(item.call, row_scope))
            group_row_scope = None
        elif isinstance(stage, HavingStage):
            later_aliases: set[str] = set()
            for later in stages[position + 1:]:
                if isinstance(later, SelectStage) and not later.star:
                    for item in later.items:
                        later_aliases.add(_select_name(item))
            for name in _identifiers(stage.condition):
                if not scope.known(name) and name in later_aliases:
                    raise _semantic_error(
                        stage,
                        f"having runs before select, so SELECT alias '{name}' is not "
                        "available; repeat the aggregate expression or define the name "
                        "before having",
                    )
            _check_refs(stage.condition, scope, stage, "having condition")
        elif isinstance(stage, WindowStage):
            for arg in stage.call.args:
                _check_refs(arg, scope, stage, f"window '{stage.name}'")
            for expr in stage.call.partition_by:
                _check_refs(expr, scope, stage, f"window '{stage.name}'")
            for key in stage.call.order_by:
                _check_refs(key.expr, scope, stage, f"window '{stage.name}'")
            if scope.known(stage.name):
                raise _semantic_error(
                    stage,
                    f"window '{stage.name}' conflicts with a live column; choose another name",
                )
            scope.set(stage.name, _window_family(stage.call, scope))
        elif isinstance(stage, SelectStage):
            fresh = _Scope()
            if stage.star:
                for name in scope.bare_names():
                    fresh.set(name, scope.family(name))
                for item in stage.items:
                    _check_refs(item.expr, scope, stage, "select")
                    output = _select_name(item)
                    if fresh.known(output):
                        raise _semantic_error(
                            stage,
                            f"select output '{output}' duplicates a live column from '*'; "
                            "choose a different alias",
                        )
                    fresh.set(output, _infer(item.expr, scope))
            else:
                for item in stage.items:
                    _check_refs(item.expr, scope, stage, "select")
                    fresh.set(_select_name(item), _infer(item.expr, scope))
            scope = fresh
        elif isinstance(stage, SortStage):
            for key in stage.keys:
                _check_refs(key.expr, scope, stage, "sort key")
        elif isinstance(stage, (SkipStage, TakeStage, DistinctStage)):
            pass
        elif isinstance(stage, SetStage):
            inner = _analyze_program(stage.program, schema, ctes)
            if not inner.opaque:
                left = len(scope.bare_names())
                if left != len(inner.columns):
                    raise _semantic_error(
                        stage,
                        f"'{stage.op}' requires the same number of columns on both sides "
                        f"(left {left}, right {len(inner.columns)})",
                    )
        elif isinstance(stage, CreateStage):
            inner = _analyze_program(stage.program, schema, dict(ctes))
            lineage.append(StageLineage(stage.index, stage.keyword, tuple(inner.columns)))
            # Later statements see this table: `create table a = (...)` then `from a`.
            if not inner.opaque:
                schema = with_created_table(schema, stage.name, [
                    (column, inner.families.get(column, "unknown")) for column in inner.columns])
            continue
        elif isinstance(stage, IntoStage):
            inner = _analyze_program(stage.program, schema, dict(ctes))
            table = schema.tables.get(stage.name)
            if table is None:
                raise _semantic_error(
                    stage,
                    f"into '{stage.name}' needs an existing table; create it first "
                    "(create table ...)")
            if not inner.opaque:
                _check_into_columns(stage, stage.name, table.columns,
                                    inner.columns, inner.families)
            lineage.append(StageLineage(stage.index, stage.keyword, tuple(inner.columns)))
            continue
        elif isinstance(stage, RawSqlStage):
            opaque = True
            scope = _Scope()
            lineage.append(StageLineage(stage.index, stage.keyword, ("<raw sql>",)))
            continue

        lineage.append(StageLineage(stage.index, stage.keyword, tuple(scope.bare_names())))

    return _Analysis(lineage, scope.bare_names(), scope.snapshot(), opaque)


def analyze(stages: list[Stage], schema: Schema) -> list[StageLineage]:
    """Validate a program against a schema and return per-stage lineage."""
    return _analyze_program(stages, schema, {}).lineage


def _check_into_columns(stage, target: str, target_columns: dict[str, str],
                        columns: list[str], families: dict[str, str]) -> None:
    """`into` appends BY NAME: same column set (any order, case-insensitive), same families."""
    wanted = {name.casefold(): (name, declared) for name, declared in target_columns.items()}
    got = {name.casefold(): name for name in columns}
    extra = [name for key, name in got.items() if key not in wanted]
    missing = [name for key, (name, _) in wanted.items() if key not in got]
    if extra or missing:
        parts = []
        if extra:
            parts.append(f"not in '{target}': {', '.join(extra)}")
        if missing:
            parts.append(f"missing: {', '.join(missing)}")
        raise _semantic_error(
            stage, f"into '{target}' matches columns by name; {'; '.join(parts)}")
    for key, name in got.items():
        target_name, declared = wanted[key]
        expected, actual = type_family(declared), families.get(name, "unknown")
        if "unknown" not in (expected, actual) and expected != actual:
            raise _semantic_error(
                stage,
                f"into '{target}': '{name}' is {actual} but {target}.{target_name} "
                f"is {declared}; CAST it explicitly")


def referenced_tables(program) -> set[str]:
    """Base tables a program reads (from/join, recursively), minus its own `with` names."""
    tables: set[str] = set()

    def visit(stages, shadow: set[str]) -> None:
        names = shadow | {stage.name for stage in stages if isinstance(stage, WithStage)}
        for stage in stages:
            if isinstance(stage, (FromStage, JoinStage)):
                if stage.source.program is not None:
                    visit(stage.source.program, names)
                elif stage.source.name and stage.source.name not in names:
                    tables.add(stage.source.name)
            elif isinstance(stage, (WithStage, SetStage, CreateStage, IntoStage)):
                visit(stage.program, names)

    visit(program, set())
    return tables

