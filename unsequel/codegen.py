"""DuckDB SQL code generation: one CTE per pipeline stage.

`emit_sql(stages, schema)` turns the stage IR into a `WITH` chain where every
pipeline stage becomes its own CTE (`stage_1`, `stage_2`, ...). This is verbose
on purpose: truncating the chain at any `stage_k` and selecting from it is
exactly the stage preview (`emit_sql(stages, schema, stop_at=k)`).

A schema is required — the join stages need to know each table's columns to
keep qualified references (`members.customer`) resolvable downstream.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .expressions import (Between, Binary, Call, Case, Cast, Expr, Identifier, InList,
                          Literal, Unary, Wildcard)
from .pipeline_ir import (Derive, Distinct, From, Group, Join, PipelineError, RawSql,
                          Select, Skip, Sort, Stage, Take, Where)
from .schema import Schema

_SAFE_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_INPUT_NAME = re.compile(r"\b__input__\b")
_TARGET = "duckdb"


class CodegenError(PipelineError):
    """The pipeline cannot be lowered to SQL."""


def _ident(name: str) -> str:
    return name if _SAFE_IDENT.match(name) else '"' + name.replace('"', '""') + '"'


def _literal(value: object) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return repr(value)
    return "'" + str(value).replace("'", "''") + "'"


@dataclass
class _Cols:
    """Resolves a query-level column name to a SQL column token for one CTE."""

    lookup: dict[str, str] = field(default_factory=dict)

    def resolve(self, name: str) -> str:
        if name in self.lookup:
            return self.lookup[name]
        bare = name.split(".")[-1]
        if bare in self.lookup:
            return self.lookup[bare]
        return ".".join(_ident(part) for part in name.split("."))

    def flat(self, names: list[str]) -> "_Cols":
        return _Cols({n: _ident(n) for n in names})


def _render(expr: Expr, cols: _Cols) -> str:
    if isinstance(expr, Literal):
        return _literal(expr.value)
    if isinstance(expr, Wildcard):
        return "*"
    if isinstance(expr, Identifier):
        return cols.resolve(expr.name)
    if isinstance(expr, Unary):
        op = expr.op.upper()
        if op == "NOT":
            return f"(NOT {_render(expr.operand, cols)})"
        return f"({op}{_render(expr.operand, cols)})"
    if isinstance(expr, Binary):
        op = expr.op.upper()
        left = _render(expr.left, cols)
        if op in {"IS", "IS NOT"} and isinstance(expr.right, Literal) and expr.right.value is None:
            return f"({left} {op} NULL)"
        return f"({left} {op} {_render(expr.right, cols)})"
    if isinstance(expr, Call):
        if len(expr.args) == 1 and isinstance(expr.args[0], Wildcard):
            return f"{expr.name.upper()}(*)"
        inner = ", ".join(_render(arg, cols) for arg in expr.args)
        prefix = "DISTINCT " if expr.distinct else ""
        return f"{expr.name.upper()}({prefix}{inner})"
    if isinstance(expr, InList):
        options = ", ".join(_render(option, cols) for option in expr.options)
        keyword = "NOT IN" if expr.negated else "IN"
        return f"({_render(expr.value, cols)} {keyword} ({options}))"
    if isinstance(expr, Between):
        keyword = "NOT BETWEEN" if expr.negated else "BETWEEN"
        return (f"({_render(expr.value, cols)} {keyword} "
                f"{_render(expr.lower, cols)} AND {_render(expr.upper, cols)})")
    if isinstance(expr, Case):
        parts = ["CASE"]
        for condition, result in expr.branches:
            parts.append(f"WHEN {_render(condition, cols)} THEN {_render(result, cols)}")
        parts.append(f"ELSE {_render(expr.else_expr, cols)} END")
        return "(" + " ".join(parts) + ")"
    if isinstance(expr, Cast):
        return f"CAST({_render(expr.operand, cols)} AS {expr.type_name})"
    raise CodegenError(f"cannot render expression {type(expr).__name__}")


def _source_sql(spec) -> str:
    if spec.subquery is not None:
        raise CodegenError("subquery sources are not supported by v0.2 codegen")
    if spec.alias:
        return f"{_ident(spec.name)} AS {_ident(spec.alias)}"
    return _ident(spec.name)


def _from_stage(stage: From, schema: Schema) -> tuple[str, _Cols, list[str]]:
    table = schema.get(stage.source.name)
    if table is None:
        raise CodegenError(f"unknown table '{stage.source.name}'", index=1,
                           keyword="from", line=stage.line)
    columns = list(table.columns)
    label = stage.source.alias or stage.source.name
    lookup = {}
    for column in columns:
        lookup[column] = _ident(column)
        lookup[f"{label}.{column}"] = _ident(column)
    return f"SELECT * FROM {_source_sql(stage.source)}", _Cols(lookup), columns


def _join_stage(stage: Join, schema: Schema, prev_cte: str, prev: _Cols,
                live: list[str], index: int) -> tuple[str, _Cols, list[str]]:
    table = schema.get(stage.source.name)
    if table is None:
        raise CodegenError(f"unknown joined table '{stage.source.name}'", index=index,
                           keyword=stage.keyword, line=stage.line)
    label = stage.source.alias or stage.source.name

    # ON runs in the join's own scope: bare names come from the previous CTE,
    # qualified names from whichever side owns them.
    on_lookup = {name: f"{prev_cte}.{_ident(name)}" for name in live}
    for name in live:
        on_lookup[f"{prev_cte}.{name}"] = f"{prev_cte}.{_ident(name)}"
    for column in table.columns:
        on_lookup[f"{label}.{column}"] = f"{_ident(label)}.{_ident(column)}"
    on_sql = _render(stage.on, _Cols(on_lookup))

    taken = set(live)
    projected = [f"{prev_cte}.*"]
    new_lookup = dict(prev.lookup)
    new_live = list(live)
    for column in table.columns:
        if column in taken:
            exposed = f"{label}_{column}"
            projected.append(f"{_ident(label)}.{_ident(column)} AS {_ident(exposed)}")
        else:
            exposed = column
            projected.append(f"{_ident(label)}.{_ident(column)}")
        taken.add(exposed)
        new_live.append(exposed)
        new_lookup.setdefault(column, _ident(exposed))
        new_lookup[f"{label}.{column}"] = _ident(exposed)

    keyword = "LEFT JOIN" if stage.kind == "left" else "JOIN"
    sql = (f"SELECT {', '.join(projected)} FROM {prev_cte} "
           f"{keyword} {_source_sql(stage.source)} ON {on_sql}")
    return sql, _Cols(new_lookup), new_live


def _stage_sql(stage: Stage, prev_cte: str, cols: _Cols, live: list[str],
               index: int) -> tuple[str, _Cols, list[str]]:
    if isinstance(stage, Where):
        return f"SELECT * FROM {prev_cte} WHERE {_render(stage.condition, cols)}", cols, live

    if isinstance(stage, Derive):
        additions = ", ".join(_aliased(_render(expr, cols), name) for name, expr in stage.items)
        new_live = live + [name for name, _ in stage.items]
        new_lookup = dict(cols.lookup)
        for name, _ in stage.items:
            new_lookup[name] = _ident(name)
        return f"SELECT *, {additions} FROM {prev_cte}", _Cols(new_lookup), new_live

    if isinstance(stage, Group):
        key_sql = [_aliased(_render(expr, cols), name) for name, expr in stage.keys]
        group_by = ", ".join(_render(expr, cols) for _, expr in stage.keys)
        agg_sql = [_aliased(_render(call, cols), name) for name, call in stage.aggregates]
        names = [name for name, _ in stage.keys] + [name for name, _ in stage.aggregates]
        sql = f"SELECT {', '.join(key_sql + agg_sql)} FROM {prev_cte} GROUP BY {group_by}"
        return sql, cols.flat(names), names

    if isinstance(stage, Select):
        if stage.star:
            return f"SELECT * FROM {prev_cte}", cols, live
        rendered = []
        names = []
        for item in stage.items:
            name = item.alias or _default_name(item.expression)
            rendered.append(_aliased(_render(item.expression, cols), name))
            names.append(name)
        return f"SELECT {', '.join(rendered)} FROM {prev_cte}", cols.flat(names), names

    if isinstance(stage, Sort):
        keys = ", ".join(_render(key.expression, cols) + (" DESC" if key.descending else "")
                         for key in stage.keys)
        return f"SELECT * FROM {prev_cte} ORDER BY {keys}", cols, live

    if isinstance(stage, Take):
        return f"SELECT * FROM {prev_cte} LIMIT {stage.count}", cols, live

    if isinstance(stage, Skip):
        return f"SELECT * FROM {prev_cte} OFFSET {stage.count}", cols, live

    if isinstance(stage, Distinct):
        return f"SELECT DISTINCT * FROM {prev_cte}", cols, live

    raise CodegenError(f"cannot lower stage '{stage.keyword}'", index=index,
                       keyword=stage.keyword, line=stage.line)


def _aliased(rendered: str, name: str) -> str:
    return rendered if rendered == _ident(name) else f"{rendered} AS {_ident(name)}"


def _default_name(expr: Expr) -> str:
    if isinstance(expr, Identifier):
        return expr.name.split(".")[-1]
    if isinstance(expr, Call):
        return expr.name.lower()
    return "expr"


def emit_sql(stages: list[Stage], schema: Schema, *, stop_at: int | None = None,
             target: str = _TARGET) -> str:
    """Emit a one-CTE-per-stage SQL statement for the given stages.

    `stop_at` truncates the pipeline after that many stages (1-based) — the
    basis for stage preview.
    """
    if target != _TARGET:
        raise CodegenError(f"only the '{_TARGET}' target is supported in v0.2")
    if not stages or not isinstance(stages[0], From):
        raise CodegenError("a pipeline must start with a from stage")
    limit = len(stages) if stop_at is None else max(1, min(stop_at, len(stages)))

    ctes: list[str] = []
    from_sql, cols, live = _from_stage(stages[0], schema)
    ctes.append(f"stage_1 AS (\n  {from_sql}\n)")

    for position in range(2, limit + 1):
        stage = stages[position - 1]
        prev_cte = f"stage_{position - 1}"
        if isinstance(stage, Join):
            sql, cols, live = _join_stage(stage, schema, prev_cte, cols, live, position)
        elif isinstance(stage, RawSql):
            sql, cols, live = _raw_sql_stage(stage, prev_cte, position)
        else:
            sql, cols, live = _stage_sql(stage, prev_cte, cols, live, position)
        ctes.append(f"stage_{position} AS (\n  {sql}\n)")

    body = ",\n".join(ctes)
    return f"WITH {body}\nSELECT * FROM stage_{limit};"


def _raw_sql_stage(stage: RawSql, prev_cte: str, position: int) -> tuple[str, _Cols, list[str]]:
    """A sql stage becomes an opaque CTE; __input__ names the previous stage."""
    text = _INPUT_NAME.sub(prev_cte, stage.text)
    indented = "\n".join(f"  {line}" for line in text.splitlines())
    # Columns after a raw stage are the user's responsibility: identifiers
    # render literally instead of being resolved from tracked lineage.
    return indented, _Cols({}), []
