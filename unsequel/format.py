"""Canonical, idempotent formatter for pipeline queries.

`format_pipeline` parses the stage IR and re-renders it: lowercase keywords,
one stage per line, spaces around binary operators, `-column` for descending
sort, `name = expression` only when the name differs from the rendered
expression, and a trailing newline. Comments are not preserved. Formatting an
already-canonical query is a no-op.
"""

from __future__ import annotations

from .expressions import Wildcard, format_expr
from .parser import FromSpec
from .pipeline_ir import (Derive, Distinct, From, Group, Join, RawSql, Select, Skip,
                          Sort, Stage, Take, Where, parse_pipeline_stages)


def _quote_sql(text: str) -> str:
    """Quote a sql-stage payload. Double quotes: single quotes inside are data."""
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _format_table(table: FromSpec) -> str:
    name = table.name or "subquery"
    if table.alias:
        return f"{name} as {table.alias}"
    return name


def _named(name: str, expr) -> str:
    rendered = format_expr(expr)
    return rendered if rendered == name else f"{name} = {rendered}"


def format_stage(stage: Stage) -> str:
    if isinstance(stage, From):
        return f"from {_format_table(stage.source)}"
    if isinstance(stage, Join):
        keyword = "left join" if stage.kind == "left" else "join"
        return f"{keyword} {_format_table(stage.source)} on {format_expr(stage.on)}"
    if isinstance(stage, Where):
        return f"where {format_expr(stage.condition)}"
    if isinstance(stage, Derive):
        items = ", ".join(_named(name, expr) for name, expr in stage.items)
        return f"derive {items}"
    if isinstance(stage, Group):
        keys = ", ".join(_named(name, expr) for name, expr in stage.keys)
        aggregates = ", ".join(f"{name} = {format_expr(expr)}" for name, expr in stage.aggregates)
        return f"group {keys} ({aggregates})"
    if isinstance(stage, Select):
        if stage.star:
            return "select *"
        parts = []
        for item in stage.items:
            if isinstance(item.expression, Wildcard):
                parts.append("*")
            elif item.alias:
                parts.append(_named(item.alias, item.expression))
            else:
                parts.append(format_expr(item.expression))
        return "select " + ", ".join(parts)
    if isinstance(stage, Sort):
        parts = []
        for key in stage.keys:
            rendered = format_expr(key.expression)
            parts.append(f"-{rendered}" if key.descending else rendered)
        return "sort " + ", ".join(parts)
    if isinstance(stage, Take):
        return f"take {stage.count}"
    if isinstance(stage, Skip):
        return f"skip {stage.count}"
    if isinstance(stage, Distinct):
        return "distinct"
    if isinstance(stage, RawSql):
        return f"sql {_quote_sql(stage.text)}"
    raise TypeError(f"unknown stage {type(stage).__name__}")


def format_pipeline(text: str) -> str:
    """Return the canonical form of a pipeline query, always ending in a newline."""
    stages = parse_pipeline_stages(text)
    return "\n".join(format_stage(stage) for stage in stages) + "\n"


def is_formatted(text: str) -> bool:
    return format_pipeline(text) == text
