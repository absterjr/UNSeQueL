from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .errors import ExecutionError
from .expressions import (Binary, Expr, Identifier, Literal, Wildcard, contains_aggregate,
                          evaluate)
from .model import Table
from .parser import JoinSpec, Query, SelectItem


def _lookup_table(tables: dict[str, Table], name: str) -> Table:
    for key, table in tables.items():
        if key.lower() == name.lower():
            return table
    available = ", ".join(sorted(tables)) or "none"
    raise ExecutionError(f"Unknown table {name!r}. Available tables: {available}")


def _qualified_rows(table: Table, name: str, alias: str | None) -> list[dict]:
    rows: list[dict] = []
    for row in table.rows:
        result = dict(row)
        for column in table.columns:
            result[f"{name}.{column}"] = row.get(column)
            if alias:
                result[f"{alias}.{column}"] = row.get(column)
        rows.append(result)
    return rows


def _merge_rows(left: dict, right: dict, right_columns: Iterable[str], null_right: bool = False) -> dict:
    result = dict(left)
    for key, value in right.items():
        if "." in key or key not in result:
            result[key] = value
    if null_right:
        for column in right_columns:
            if column not in result:
                result[column] = None
    return result


def _equi_join_keys(expr: Expr) -> tuple[str, str] | None:
    if isinstance(expr, Binary) and expr.op in {"=", "=="}:
        if isinstance(expr.left, Identifier) and isinstance(expr.right, Identifier):
            return expr.left.name, expr.right.name
    return None


def _join(left: list[dict], right: list[dict], spec: JoinSpec, right_columns: list[str]) -> list[dict]:
    """Execute one join, using a hash lookup for simple equality joins."""
    keys = _equi_join_keys(spec.condition)
    output: list[dict] = []
    if keys:
        left_key, right_key = keys
        left_sample = left[0] if left else {}
        right_sample = right[0] if right else {}
        if left_key not in left_sample and right_key in left_sample:
            left_key, right_key = right_key, left_key
        index: dict[object, list[dict]] = {}
        for row in right:
            index.setdefault(row.get(right_key), []).append(row)
        for lrow in left:
            matches = index.get(lrow.get(left_key), [])
            if matches:
                output.extend(_merge_rows(lrow, rrow, right_columns) for rrow in matches)
            elif spec.kind == "left":
                # Use the right row shape so qualified names such as
                # c.segment also exist and resolve to NULL.
                null_row = {key: None for key in (right[0] if right else right_columns)}
                output.append(_merge_rows(lrow, null_row, right_columns, null_right=True))
        return output

    # General ON expressions are supported as a correctness-first fallback.
    for lrow in left:
        matches = []
        for rrow in right:
            merged = _merge_rows(lrow, rrow, right_columns)
            if evaluate(spec.condition, merged) is True:
                matches.append(merged)
        if matches:
            output.extend(matches)
        elif spec.kind == "left":
            null_row = {key: None for key in (right[0] if right else right_columns)}
            output.append(_merge_rows(lrow, null_row, right_columns, null_right=True))
    return output


def _visible(row: dict) -> dict:
    return {key: value for key, value in row.items() if "." not in key}


def _has_aggregate(query: Query) -> bool:
    return any(contains_aggregate(item.expression) for item in query.select) \
        or contains_aggregate(query.having) \
        or any(contains_aggregate(item.expression) for item in query.order_by)


def _select_row(item: SelectItem, context: dict, group: list[dict], aliases: dict) -> tuple[str, object]:
    if isinstance(item.expression, Wildcard):
        raise ExecutionError("SELECT * cannot be combined with other SELECT expressions")
    key = item.output_name
    value = evaluate(item.expression, context, group, aliases)
    return key, value


def execute(query: Query, tables: dict[str, Table]) -> Table:
    """Execute a parsed query against named in-memory tables."""
    source_table = _lookup_table(tables, query.source.name)
    rows = _qualified_rows(source_table, query.source.name, query.source.alias)
    visible_columns = list(source_table.columns)

    for join in query.joins:
        right_table = _lookup_table(tables, join.name)
        right_rows = _qualified_rows(right_table, join.name, join.alias)
        rows = _join(rows, right_rows, join, right_table.columns)
        visible_columns.extend(column for column in right_table.columns if column not in visible_columns)

    if query.where is not None:
        rows = [row for row in rows if evaluate(query.where, row) is True]

    grouped = bool(query.group_by) or _has_aggregate(query)
    groups: list[list[dict]]
    if grouped:
        if query.group_by:
            buckets: dict[tuple, list[dict]] = {}
            for row in rows:
                key = tuple(evaluate(expr, row) for expr in query.group_by)
                buckets.setdefault(key, []).append(row)
            groups = list(buckets.values())
        else:
            groups = [rows]
    else:
        groups = [[row] for row in rows]

    entries: list[tuple[dict, dict, list[dict]]] = []
    for group in groups:
        context = group[0] if group else {}
        if query.having is not None and evaluate(query.having, context, group) is not True:
            continue
        output: dict = {}
        aliases: dict = {}
        for item in query.select:
            if isinstance(item.expression, Wildcard):
                if len(query.select) != 1:
                    raise ExecutionError("SELECT * cannot be combined with other expressions")
                output.update(_visible(context))
                aliases.update(output)
                continue
            key, value = _select_row(item, context, group, aliases)
            output[key] = value
            aliases[key] = value
        entries.append((output, context, group))

    for order in reversed(query.order_by):
        def value_for(entry):
            output, context, group = entry
            scope = dict(context)
            scope.update(output)  # SELECT aliases win for ORDER BY
            return evaluate(order.expression, scope, group, output)

        non_null = [entry for entry in entries if value_for(entry) is not None]
        nulls = [entry for entry in entries if value_for(entry) is None]
        non_null.sort(key=value_for, reverse=order.descending)
        entries = non_null + nulls

    if query.limit is not None:
        entries = entries[:query.limit]
    if query.select and isinstance(query.select[0].expression, Wildcard):
        columns = visible_columns
    else:
        columns = [item.output_name for item in query.select]
    return Table("result", columns, [entry[0] for entry in entries])
