from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Iterable

from .errors import ExecutionError
from .expressions import (Between, Binary, Call, Case, Cast, Expr, Identifier, InList,
                          Literal, Unary, Wildcard, contains_aggregate, evaluate)
from .model import Table
from .parser import FromSpec, JoinSpec, Query, SelectItem


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


def _qualified_shape(table: Table, name: str, alias: str | None) -> dict:
    row = {column: None for column in table.columns}
    for column in table.columns:
        row[f"{name}.{column}"] = None
        if alias:
            row[f"{alias}.{column}"] = None
    return row


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


def _passes_predicate(expr: Expr, row: dict, rows: list[dict] | None = None,
                      role: str = "condition") -> bool:
    value = evaluate(expr, row, rows)
    if value is None:
        return False
    if type(value) is not bool:
        raise ExecutionError(f"{role} must evaluate to boolean, got {type(value).__name__}")
    return value


def _join(left: list[dict], right: list[dict], spec: JoinSpec, right_columns: list[str],
          right_shape: dict, left_shape: dict) -> list[dict]:
    """Execute one join, using a hash lookup for simple equality joins."""
    keys = _equi_join_keys(spec.condition)
    output: list[dict] = []
    if spec.kind == "cross":
        return [_merge_rows(lrow, rrow, right_columns) for lrow in left for rrow in right]
    if keys:
        left_key, right_key = keys
        left_sample = left[0] if left else {}
        right_sample = right[0] if right else {}
        if left_key not in left_sample and right_key in left_sample:
            left_key, right_key = right_key, left_key
        index: dict[object, list[dict]] = {}
        for row in right:
            index.setdefault(row.get(right_key), []).append(row)
        matched_right: set[int] = set()
        for lrow in left:
            matches = index.get(lrow.get(left_key), [])
            if matches:
                matched_right.update(id(row) for row in matches)
                output.extend(_merge_rows(lrow, rrow, right_columns) for rrow in matches)
            elif spec.kind in {"left", "full"}:
                # Use the right row shape so qualified names such as
                # c.segment also exist and resolve to NULL.
                null_row = {key: None for key in (right[0] if right else right_shape)}
                output.append(_merge_rows(lrow, null_row, right_columns, null_right=True))
        if spec.kind in {"right", "full"}:
            null_left = {key: None for key in left_shape}
            output.extend(_merge_rows(null_left, rrow, right_columns)
                          for rrow in right if id(rrow) not in matched_right)
        return output

    # General ON expressions are supported as a correctness-first fallback.
    matched_right: set[int] = set()
    for lrow in left:
        matches: list[tuple[dict, dict]] = []
        for rrow in right:
            merged = _merge_rows(lrow, rrow, right_columns)
            if _passes_predicate(spec.condition, merged, role="JOIN condition"):
                matches.append((merged, rrow))
        if matches:
            matched_right.update(id(rrow) for _, rrow in matches)
            output.extend(merged for merged, _ in matches)
        elif spec.kind in {"left", "full"}:
            null_row = {key: None for key in (right[0] if right else right_shape)}
            output.append(_merge_rows(lrow, null_row, right_columns, null_right=True))
    if spec.kind in {"right", "full"}:
        null_left = {key: None for key in left_shape}
        output.extend(_merge_rows(null_left, rrow, right_columns)
                      for rrow in right if id(rrow) not in matched_right)
    return output


def _visible(row: dict) -> dict:
    return {key: value for key, value in row.items() if "." not in key}


def _has_aggregate(query: Query) -> bool:
    return any(contains_aggregate(item.expression) for item in query.select) \
        or contains_aggregate(query.having) \
        or any(contains_aggregate(item.expression) for item in query.order_by)


_AGGREGATES = {"COUNT", "SUM", "AVG", "MIN", "MAX"}


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
        return set().union(*(_identifiers(arg) for arg in expr.args)) if expr.args else set()
    if isinstance(expr, InList):
        return _identifiers(expr.value) | set().union(
            *(_identifiers(option) for option in expr.options))
    if isinstance(expr, Between):
        return _identifiers(expr.value) | _identifiers(expr.lower) | _identifiers(expr.upper)
    if isinstance(expr, Case):
        result = _identifiers(expr.else_expr)
        for condition, value in expr.branches:
            result |= _identifiers(condition) | _identifiers(value)
        return result
    if isinstance(expr, Cast):
        return _identifiers(expr.operand)
    return set()


def _check_unambiguous(expr: Expr, source_columns: list[list[str]], role: str,
                       aliases: set[str] | None = None) -> None:
    aliases = aliases or set()
    for name in _identifiers(expr):
        if "." in name or name.casefold() in aliases:
            continue
        matches = [column for columns in source_columns for column in columns
                   if column.casefold() == name.casefold()]
        if len(matches) > 1:
            raise ExecutionError(
                f"{role} references ambiguous column '{name}'; qualify it with a table alias")


def _ungrouped_identifiers(expr: Expr, group_expressions: list[Expr],
                           group_names: set[str], allowed_names: set[str]) -> set[str]:
    if any(expr == grouped for grouped in group_expressions):
        return set()
    if isinstance(expr, Call) and expr.name.upper() in _AGGREGATES:
        return set()
    if isinstance(expr, Identifier):
        lowered = expr.name.casefold()
        bare = expr.name.rsplit(".", 1)[-1].casefold()
        if bare in group_names or ("." not in expr.name and lowered in allowed_names):
            return set()
        return {expr.name}
    if isinstance(expr, (Literal, Wildcard)):
        return set()
    if isinstance(expr, Unary):
        return _ungrouped_identifiers(expr.operand, group_expressions, group_names, allowed_names)
    if isinstance(expr, Binary):
        return (_ungrouped_identifiers(expr.left, group_expressions, group_names, allowed_names)
                | _ungrouped_identifiers(expr.right, group_expressions, group_names, allowed_names))
    if isinstance(expr, Call):
        return set().union(*(_ungrouped_identifiers(arg, group_expressions, group_names,
                                                     allowed_names)
                             for arg in expr.args)) if expr.args else set()
    if isinstance(expr, InList):
        return (_ungrouped_identifiers(expr.value, group_expressions, group_names, allowed_names)
                | set().union(*(_ungrouped_identifiers(option, group_expressions, group_names,
                                                       allowed_names)
                                for option in expr.options)))
    if isinstance(expr, Between):
        return (_ungrouped_identifiers(expr.value, group_expressions, group_names, allowed_names)
                | _ungrouped_identifiers(expr.lower, group_expressions, group_names, allowed_names)
                | _ungrouped_identifiers(expr.upper, group_expressions, group_names, allowed_names))
    if isinstance(expr, Case):
        result = _ungrouped_identifiers(expr.else_expr, group_expressions, group_names, allowed_names)
        for condition, value in expr.branches:
            result |= _ungrouped_identifiers(condition, group_expressions, group_names, allowed_names)
            result |= _ungrouped_identifiers(value, group_expressions, group_names, allowed_names)
        return result
    if isinstance(expr, Cast):
        return _ungrouped_identifiers(expr.operand, group_expressions, group_names, allowed_names)
    return set()


def _validate_grouped_query(query: Query) -> None:
    grouped = bool(query.group_by) or _has_aggregate(query)
    if not grouped:
        return
    if any(isinstance(item.expression, Wildcard) for item in query.select):
        raise ExecutionError(
            "SELECT * cannot be used with GROUP BY or aggregates; select group keys and aggregates explicitly")
    simple_group_names = {
        expr.name.casefold() for expr in query.group_by if isinstance(expr, Identifier)
    }
    select_names = {item.output_name.casefold() for item in query.select
                    if not isinstance(item.expression, Wildcard)}

    if query.having is not None:
        aliases_used = {name.casefold() for name in _identifiers(query.having)} & select_names
        unavailable = aliases_used - simple_group_names
        if unavailable:
            alias = sorted(unavailable)[0]
            raise ExecutionError(
                f"HAVING runs before SELECT, so SELECT alias '{alias}' is not available; "
                "repeat the aggregate expression or use a group/aggregate column")

    checks: list[tuple[str, Expr, set[str]]] = []
    checks.extend(("SELECT", item.expression, set()) for item in query.select)
    if query.having is not None:
        checks.append(("HAVING", query.having, set()))
    for order in query.order_by:
        is_ordinal = isinstance(order.expression, Literal) and type(order.expression.value) is int
        aliases = select_names if not is_ordinal else set()
        checks.append(("ORDER BY", order.expression, aliases))

    for role, expression, allowed_names in checks:
        invalid = _ungrouped_identifiers(
            expression, query.group_by, simple_group_names, allowed_names)
        if invalid:
            name = sorted(invalid)[0]
            raise ExecutionError(
                f"{role} column '{name}' must appear in GROUP BY or be used in an aggregate")


def _select_row(item: SelectItem, context: dict, group: list[dict], aliases: dict) -> tuple[str, object]:
    if isinstance(item.expression, Wildcard):
        raise ExecutionError("SELECT * cannot be combined with other SELECT expressions")
    key = item.output_name
    value = evaluate(item.expression, context, group, aliases)
    return key, value


def _lookup_cte(ctes: dict[str, Query], name: str) -> Query | None:
    lowered = name.lower()
    for key, query in ctes.items():
        if key.lower() == lowered:
            return query
    return None


def _load_source(source: FromSpec, tables: dict[str, Table], ctes: dict[str, Query],
                 active_ctes: set[str]) -> tuple[Table, str, str | None]:
    if source.subquery is not None:
        return _execute_query(source.subquery, tables, ctes, active_ctes), source.display_name, source.alias
    if source.name is None:
        raise ExecutionError("FROM source is missing a table name")
    cte = _lookup_cte(ctes, source.name)
    if cte is not None:
        key = source.name.lower()
        if key in active_ctes:
            raise ExecutionError(f"Recursive CTE {source.name!r} is not supported")
        return _execute_query(cte, tables, ctes, active_ctes | {key}), source.name, source.alias
    return _lookup_table(tables, source.name), source.name, source.alias


def _row_key(row: dict, columns: list[str]) -> tuple:
    values = []
    for column in columns:
        value = row.get(column)
        try:
            hash(value)
        except TypeError:
            value = repr(value)
        values.append(value)
    return tuple(values)


def _deduplicate(entries: list[tuple[dict, dict, list[dict]]], columns: list[str]) -> list[tuple[dict, dict, list[dict]]]:
    seen: set[tuple] = set()
    result = []
    for entry in entries:
        key = _row_key(entry[0], columns)
        if key not in seen:
            seen.add(key)
            result.append(entry)
    return result


def _combine_set_results(left: Table, right: Table, kind: str) -> Table:
    if len(left.columns) != len(right.columns):
        raise ExecutionError("UNION queries must return the same number of columns")
    right_rows = [{column: right_row.get(right_column)
                   for column, right_column in zip(left.columns, right.columns)}
                  for right_row in right.rows]
    left_rows = list(left.rows)
    if kind in {"UNION", "UNION DISTINCT"}:
        rows = left_rows + right_rows
        rows = [entry[0] for entry in _deduplicate([(row, {}, []) for row in rows], left.columns)]
    elif kind == "UNION ALL":
        rows = left_rows + right_rows
    elif kind in {"INTERSECT", "INTERSECT ALL"}:
        right_keys = [_row_key(row, left.columns) for row in right_rows]
        rows = []
        for row in left_rows:
            key = _row_key(row, left.columns)
            if key in right_keys:
                rows.append(row)
                if kind == "INTERSECT ALL":
                    right_keys.remove(key)
        rows = [entry[0] for entry in _deduplicate([(row, {}, []) for row in rows], left.columns)] \
            if kind == "INTERSECT" else rows
    elif kind in {"EXCEPT", "EXCEPT ALL"}:
        right_keys = [_row_key(row, left.columns) for row in right_rows]
        rows = []
        for row in left_rows:
            key = _row_key(row, left.columns)
            if key not in right_keys:
                rows.append(row)
            elif kind == "EXCEPT ALL":
                right_keys.remove(key)
        rows = [entry[0] for entry in _deduplicate([(row, {}, []) for row in rows], left.columns)] \
            if kind == "EXCEPT" else rows
    else:
        raise ExecutionError(f"Unsupported set operation {kind!r}")
    return Table("result", list(left.columns), rows)


def _execute_query(query: Query, tables: dict[str, Table], inherited_ctes: dict[str, Query],
                   active_ctes: set[str]) -> Table:
    ctes = dict(inherited_ctes)
    ctes.update(query.ctes)
    source_table, source_name, source_alias = _load_source(query.source, tables, ctes, active_ctes)
    rows = _qualified_rows(source_table, source_name, source_alias)
    left_shape = _qualified_shape(source_table, source_name, source_alias)
    visible_columns = list(source_table.columns)
    source_columns = [list(source_table.columns)]

    for join in query.joins:
        right_table, right_name, right_alias = _load_source(
            FromSpec(join.name, join.alias, join.subquery), tables, ctes, active_ctes
        )
        _check_unambiguous(join.condition, [*source_columns, list(right_table.columns)],
                           "JOIN condition")
        right_rows = _qualified_rows(right_table, right_name, right_alias)
        right_shape = _qualified_shape(right_table, right_name, right_alias)
        rows = _join(rows, right_rows, join, right_table.columns, right_shape, left_shape)
        left_shape = _merge_rows(left_shape, right_shape, right_table.columns)
        source_columns.append(list(right_table.columns))
        visible_columns.extend(column for column in right_table.columns if column not in visible_columns)

    select_names = {item.output_name.casefold() for item in query.select
                    if not isinstance(item.expression, Wildcard)}
    expressions: list[tuple[Expr, str]] = []
    if query.where is not None:
        expressions.append((query.where, "WHERE condition"))
    expressions.extend((expr, "GROUP BY expression") for expr in query.group_by)
    if query.having is not None:
        expressions.append((query.having, "HAVING condition"))
    expressions.extend((item.expression, "SELECT expression") for item in query.select)
    expressions.extend((order.expression, "ORDER BY expression") for order in query.order_by)
    for expression, role in expressions:
        allowed_aliases = select_names if role == "ORDER BY expression" else set()
        _check_unambiguous(expression, source_columns, role, allowed_aliases)

    _validate_grouped_query(query)
    flattened = [column.casefold() for columns in source_columns for column in columns]
    duplicate_source_columns = {
        name for name, count in Counter(flattened).items() if count > 1
    }
    if any(isinstance(item.expression, Wildcard) for item in query.select) \
            and duplicate_source_columns:
        name = sorted(duplicate_source_columns)[0]
        raise ExecutionError(
            f"SELECT * would drop duplicate join column '{name}'; select and alias columns explicitly")

    if query.where is not None:
        rows = [row for row in rows
                if _passes_predicate(query.where, row, role="WHERE condition")]

    grouped = bool(query.group_by) or _has_aggregate(query)
    groups: list[list[dict]]
    if grouped:
        if query.group_by:
            buckets: dict[tuple, list[dict]] = {}
            for row in rows:
                key = tuple(evaluate(expr, row) for expr in query.group_by)
                try:
                    hash(key)
                except TypeError as exc:
                    raise ExecutionError("GROUP BY expressions must produce scalar values") from exc
                buckets.setdefault(key, []).append(row)
            groups = list(buckets.values())
        else:
            groups = [rows]
    else:
        groups = [[row] for row in rows]

    entries: list[tuple[dict, dict, list[dict]]] = []
    for group in groups:
        context = group[0] if group else {}
        if query.having is not None and not _passes_predicate(
                query.having, context, group, "HAVING condition"):
            continue
        output: dict = {}
        aliases: dict = {}
        for item in query.select:
            if isinstance(item.expression, Wildcard):
                output.update(_visible(context))
                aliases.update(output)
                continue
            key, value = _select_row(item, context, group, aliases)
            output[key] = value
            aliases[key] = value
        entries.append((output, context, group))

    select_columns: list[str] = []
    for item in query.select:
        names = visible_columns if isinstance(item.expression, Wildcard) else [item.output_name]
        select_columns.extend(names)
    seen_outputs: set[str] = set()
    for name in select_columns:
        if name.casefold() in seen_outputs:
            raise ExecutionError(
                f"SELECT projects '{name}' more than once; add an alias to one of them")
        seen_outputs.add(name.casefold())
    columns = select_columns
    if query.distinct:
        entries = _deduplicate(entries, columns)

    resolved_order: list[tuple[object, bool]] = []
    for order in query.order_by:
        expr = order.expression
        if isinstance(expr, Literal) and type(expr.value) is int:
            ordinal = expr.value
            if not 1 <= ordinal <= len(columns):
                raise ExecutionError(
                    f"ORDER BY ordinal {ordinal} is out of range for {len(columns)} output columns")
            resolved_order.append((columns[ordinal - 1], order.descending))
        else:
            resolved_order.append((expr, order.descending))

    for order_expression, descending in reversed(resolved_order):
        def value_for(entry):
            output, context, group = entry
            scope = dict(context)
            scope.update(output)  # SELECT aliases win for ORDER BY
            if isinstance(order_expression, str):
                return output.get(order_expression)
            return evaluate(order_expression, scope, group, output)

        non_null = [entry for entry in entries if value_for(entry) is not None]
        nulls = [entry for entry in entries if value_for(entry) is None]
        try:
            non_null.sort(key=value_for, reverse=descending)
        except TypeError as exc:
            raise ExecutionError("ORDER BY values have incompatible types") from exc
        entries = non_null + nulls

    if query.offset:
        entries = entries[query.offset:]
    if query.limit is not None:
        entries = entries[:query.limit]
    result = Table("result", columns, [entry[0] for entry in entries])
    for operation in query.set_operations:
        result = _combine_set_results(result,
                                      _execute_query(operation.query, tables, ctes, active_ctes),
                                      operation.kind)
    return result


def execute(query: Query, tables: dict[str, Table]) -> Table:
    """Execute a parsed query against named in-memory tables."""
    return _execute_query(query, tables, {}, set())
