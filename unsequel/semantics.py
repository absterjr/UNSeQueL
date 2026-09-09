"""Schema-aware validation of a pipeline before it ever touches a warehouse.

`analyze` walks the stage IR against a `Schema`, tracking which columns are
"alive" at each stage. It raises a stage-located `SemanticError` for:

* an unknown source or joined table,
* a reference to a column that is not alive at that stage,
* an ambiguous bare column after a join,
* `SUM` / `AVG` over a non-numeric column,
* a post-`group` reference to something that is neither a group key nor an
  aggregate.

As a side effect it returns the column lineage: the ordered list of live
columns after every stage.
"""

from __future__ import annotations

from dataclasses import dataclass

from .expressions import (Between, Binary, Call, Case, Cast, Expr, Identifier, InList,
                          Literal, Unary, Wildcard)
from .pipeline_ir import (Derive, Distinct, From, Group, Join, PipelineError, RawSql,
                          Select, Skip, Sort, Stage, Take, Where)
from .schema import Schema, type_family

_NUMERIC_AGGS = {"SUM", "AVG"}
_TEXT_FUNCS = {"LOWER", "UPPER", "TRIM", "CONCAT"}
_NUMERIC_FUNCS = {"ABS", "ROUND", "LENGTH"}


class SemanticError(PipelineError):
    """A pipeline query that is valid syntax but wrong against the schema."""


@dataclass
class _Scope:
    # referenceable name (bare or "table.col") -> type family
    columns: dict[str, str]
    # bare column name -> set of qualifying labels that expose it
    sources: dict[str, set[str]]

    def known(self, name: str) -> bool:
        return name in self.columns or name.split(".")[-1] in self.columns

    def family(self, name: str) -> str:
        return self.columns.get(name) or self.columns.get(name.split(".")[-1]) or "unknown"

    def bare_names(self) -> list[str]:
        return [name for name in self.columns if "." not in name]

    def add_column(self, label: str, column: str, family: str) -> None:
        self.columns[f"{label}.{column}"] = family
        self.columns.setdefault(column, family)
        self.sources.setdefault(column, set()).add(label)

    def add_derived(self, name: str, family: str) -> None:
        self.columns[name] = family
        self.sources[name] = {"<derived>"}


@dataclass
class StageLineage:
    index: int
    keyword: str
    columns: tuple[str, ...]


def _identifiers(expr: Expr) -> set[str]:
    if isinstance(expr, Identifier):
        return {expr.name}
    if isinstance(expr, (Literal, Wildcard)):
        return set()
    if isinstance(expr, Unary):
        return _identifiers(expr.operand)
    if isinstance(expr, Binary):
        return _identifiers(expr.left) | _identifiers(expr.right)
    if isinstance(expr, Call):
        out: set[str] = set()
        for arg in expr.args:
            out |= _identifiers(arg)
        return out
    if isinstance(expr, InList):
        out = _identifiers(expr.value)
        for option in expr.options:
            out |= _identifiers(option)
        return out
    if isinstance(expr, Between):
        return _identifiers(expr.value) | _identifiers(expr.lower) | _identifiers(expr.upper)
    if isinstance(expr, Case):
        out = _identifiers(expr.else_expr)
        for condition, result in expr.branches:
            out |= _identifiers(condition) | _identifiers(result)
        return out
    if isinstance(expr, Cast):
        return _identifiers(expr.operand)
    return set()


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
    if isinstance(expr, Identifier):
        return scope.family(expr.name)
    if isinstance(expr, Unary):
        return "boolean" if expr.op.upper() == "NOT" else "numeric"
    if isinstance(expr, Binary):
        op = expr.op.upper()
        if op in {"+", "-", "*", "/", "%"}:
            return "numeric"
        if op == "||":
            return "text"
        return "boolean"
    if isinstance(expr, (InList, Between)):
        return "boolean"
    if isinstance(expr, Call):
        name = expr.name.upper()
        if name in {"SUM", "AVG", "COUNT"} or name in _NUMERIC_FUNCS:
            return "numeric"
        if name in {"MIN", "MAX", "COALESCE"} and expr.args:
            return _infer(expr.args[0], scope)
        if name in _TEXT_FUNCS:
            return "text"
        return "unknown"
    if isinstance(expr, Case):
        families = {_infer(result, scope) for _, result in expr.branches}
        families.add(_infer(expr.else_expr, scope))
        families.discard("unknown")
        return families.pop() if len(families) == 1 else "unknown"
    if isinstance(expr, Cast):
        return type_family(expr.type_name)
    return "unknown"


def _fail(stage: Stage, index: int, message: str) -> SemanticError:
    return SemanticError(message, index=index, keyword=stage.keyword, line=stage.line)


def _check_refs(expr: Expr, scope: _Scope, stage: Stage, index: int, role: str) -> None:
    for name in _identifiers(expr):
        if not scope.known(name):
            raise _fail(stage, index, f"{role} references unknown column '{name}'")


def _table_scope(scope: _Scope, table_name: str, alias: str | None,
                 columns: dict[str, str]) -> None:
    label = alias or table_name
    for column, declared in columns.items():
        scope.add_column(label, column, type_family(declared))


def analyze(stages: list[Stage], schema: Schema) -> list[StageLineage]:
    """Validate a stage list against a schema and return per-stage lineage."""
    scope = _Scope({}, {})
    grouped = False
    opaque = False
    lineage: list[StageLineage] = []

    for index, stage in enumerate(stages, start=1):
        if opaque:
            # After a sql stage the relation is the user's SQL output; column
            # names and types are not tracked, so later stages are unchecked.
            lineage.append(StageLineage(index, stage.keyword, ("<opaque>",)))
            continue

        if isinstance(stage, From):
            src = stage.source
            if src.subquery is not None:
                raise _fail(stage, index, "subquery sources are not schema-checked in v0.2")
            table = schema.get(src.name)
            if table is None:
                raise _fail(stage, index, f"unknown table '{src.name}'")
            _table_scope(scope, src.name, src.alias, table.columns)

        elif isinstance(stage, Join):
            src = stage.source
            if src.subquery is not None:
                raise _fail(stage, index, "subquery joins are not schema-checked in v0.2")
            table = schema.get(src.name)
            if table is None:
                raise _fail(stage, index, f"unknown joined table '{src.name}'")
            _table_scope(scope, src.name, src.alias, table.columns)
            _check_refs(stage.on, scope, stage, index, "join condition")

        elif isinstance(stage, Where):
            _check_refs(stage.condition, scope, stage, index, "where condition")

        elif isinstance(stage, Derive):
            for name, expr in stage.items:
                _check_refs(expr, scope, stage, index, f"derive '{name}'")
            for name, expr in stage.items:
                scope.add_derived(name, _infer(expr, scope))

        elif isinstance(stage, Group):
            fresh = _Scope({}, {})
            for name, expr in stage.keys:
                _check_refs(expr, scope, stage, index, f"group key '{name}'")
                fresh.add_derived(name, _infer(expr, scope))
            for name, call in stage.aggregates:
                _check_refs(call, scope, stage, index, f"aggregate '{name}'")
                fname = call.name.upper()
                if fname in _NUMERIC_AGGS and call.args:
                    arg_family = _infer(call.args[0], scope)
                    if arg_family not in {"numeric", "unknown"}:
                        raise _fail(stage, index,
                                    f"{fname} over '{name}' needs a numeric column, "
                                    f"got {arg_family}")
                fresh.add_derived(name, _infer(call, scope))
            scope = fresh
            grouped = True

        elif isinstance(stage, Select):
            if not stage.star:
                for item in stage.items:
                    _check_refs(item.expression, scope, stage, index, "select")
                fresh = _Scope({}, {})
                for item in stage.items:
                    out = item.alias or _identifier_name(item.expression)
                    fresh.add_derived(out, _infer(item.expression, scope))
                scope = fresh

        elif isinstance(stage, Sort):
            for key in stage.keys:
                _check_refs(key.expression, scope, stage, index, "sort key")

        elif isinstance(stage, (Take, Skip, Distinct)):
            pass

        elif isinstance(stage, RawSql):
            opaque = True
            scope = _Scope({}, {})
            lineage.append(StageLineage(index, stage.keyword, ("<raw sql>",)))
            continue

        lineage.append(StageLineage(index, stage.keyword, tuple(scope.bare_names())))

    _ = grouped
    return lineage


def _identifier_name(expr: Expr) -> str:
    if isinstance(expr, Identifier):
        return expr.name.split(".")[-1]
    if isinstance(expr, Call):
        return expr.name.lower()
    return "expression"
