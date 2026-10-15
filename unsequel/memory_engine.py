"""In-memory execution of canonical `.unsequel` programs.

Walks the same stage list and state model as `unsequel.sqlgen` (one relation
per lowering unit, the same column-lookup and join-naming rules, the same
hidden sort ordinal), but evaluates directly in Python, so `.unsequel`
programs run without DuckDB. The `sql` hatch is refused here rather than
executed on SQLite, because the hatch is DuckDB SQL and running it on another
engine silently changes answers (integer division, LIKE case sensitivity,
QUALIFY, date functions). `tests/test_memory_parity.py` pins every reference
query against DuckDB execution; the behaviors probed against DuckDB are
encoded here: float division and division-by-zero infinity,
half-away-from-zero casts and rounding, three-valued logic, NULLS LAST
ordering, RANGE-peer window aggregates, and implicit text/number coercion in
comparisons.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Callable

from .errors import ExecutionError
from .grammar import (
    AggregateStage,
    Between,
    Binary,
    Call,
    Case,
    Cast,
    DeriveStage,
    DistinctStage,
    Expr,
    FilterStage,
    FromStage,
    GroupByStage,
    HavingStage,
    InExpr,
    IsNull,
    JoinStage,
    Like,
    Literal,
    Name,
    Program,
    RawSqlStage,
    SelectStage,
    SetStage,
    SkipStage,
    SortKey,
    SortStage,
    Stage,
    TakeStage,
    Unary,
    WindowCall,
    WindowStage,
    WithStage,
    expression_name,
)
from .model import Table
from .schema import Schema, type_family
from .sqlgen import _internal_order_names, _number_text

__all__ = ["StageExecutionError", "execute_program"]


class StageExecutionError(ExecutionError):
    """A stage failed at execution time; the message carries its location."""

    def __init__(self, stage: Stage, message: str) -> None:
        super().__init__(
            f"stage {stage.index} ({stage.keyword}): {message} "
            f"at line {stage.span.start.line}, column {stage.span.start.column}"
        )


# --------------------------------------------------------------------------- #
# Scalar semantics (probed against DuckDB)
# --------------------------------------------------------------------------- #


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _type_name(value: object) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "boolean"
    if _is_number(value):
        return "number"
    if isinstance(value, str):
        return "text"
    if isinstance(value, (date, datetime)):
        return "date"
    return type(value).__name__


def _to_varchar(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return _number_text(value)
    return str(value)


def _truth(value: object) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if _is_number(value):
        return value != 0
    raise ExecutionError(f"condition must be a boolean, got {_type_name(value)}")


def _three_and(left: bool | None, right: bool | None) -> bool | None:
    if left is False or right is False:
        return False
    if left is None or right is None:
        return None
    return True


def _three_or(left: bool | None, right: bool | None) -> bool | None:
    if left is True or right is True:
        return True
    if left is None or right is None:
        return None
    return False


def _parse_number(text: str) -> int | float:
    stripped = text.strip()
    try:
        return int(stripped)
    except ValueError:
        pass
    try:
        return float(stripped)
    except ValueError:
        raise ExecutionError(f"cannot compare text {text!r} with a number") from None


def _as_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.strip())
        except ValueError:
            raise ExecutionError(f"invalid date value {value!r}") from None
    raise ExecutionError(f"expected a date value, got {_type_name(value)}")


def _is_dateish(value: object) -> bool:
    return isinstance(value, (date, datetime))


def _order_cmp(left: object, right: object) -> int:
    return (left > right) - (left < right)


def _compare_values(left: object, right: object) -> int:
    if _is_number(left) and _is_number(right):
        return _order_cmp(left, right)
    if isinstance(left, bool) and isinstance(right, bool):
        return _order_cmp(left, right)
    if isinstance(left, str) and isinstance(right, str):
        return _order_cmp(left, right)
    if _is_number(left) and isinstance(right, str):
        return _compare_values(left, _parse_number(right))
    if isinstance(left, str) and _is_number(right):
        return _compare_values(_parse_number(left), right)
    if _is_dateish(left) and (_is_dateish(right) or isinstance(right, str)):
        return _order_cmp(_as_datetime(left), _as_datetime(right))
    if _is_dateish(right) and isinstance(left, str):
        return _order_cmp(_as_datetime(left), _as_datetime(right))
    raise ExecutionError(f"cannot compare {_type_name(left)} with {_type_name(right)}")


def _comparison(op: str, left: object, right: object) -> bool | None:
    if left is None or right is None:
        return None
    order = _compare_values(left, right)
    if op in {"=", "=="}:
        return order == 0
    if op in {"!=", "<>"}:
        return order != 0
    if op == "<":
        return order < 0
    if op == "<=":
        return order <= 0
    if op == ">":
        return order > 0
    if op == ">=":
        return order >= 0
    raise ExecutionError(f"unknown comparison operator {op!r}")


def _arith(op: str, left: object, right: object) -> object:
    if left is None or right is None:
        return None
    if op == "||":
        return _to_varchar(left) + _to_varchar(right)
    if not (_is_number(left) and _is_number(right)):
        raise ExecutionError(f"'{op}' requires numeric operands")
    if op == "+":
        return left + right
    if op == "-":
        return left - right
    if op == "*":
        return left * right
    if op == "/":
        if right == 0:
            if left == 0:
                return float("nan")
            return math.copysign(math.inf, left) if right == 0 else left / right
        return left / right
    if op == "%":
        if right == 0:
            return None
        if isinstance(left, float) or isinstance(right, float):
            return math.fmod(left, right)
        return int(math.fmod(left, right))
    raise ExecutionError(f"unknown operator {op!r}")


def _like(negated: bool, value: object, pattern: object) -> bool | None:
    if value is None or pattern is None:
        return None
    if not isinstance(pattern, str):
        raise ExecutionError("LIKE requires a text pattern")
    regex = "".join(
        ".*" if ch == "%" else "." if ch == "_" else re.escape(ch) for ch in pattern
    )
    matched = re.fullmatch(regex, _to_varchar(value)) is not None
    return (not matched) if negated else matched


def _cast_float_to_int(value: float) -> int:
    # DuckDB casts DOUBLE to integers with round-half-to-even.
    return int(round(value))


_INT_TYPES = {"INT", "INTEGER", "BIGINT", "SMALLINT", "TINYINT", "HUGEINT",
              "UTINYINT", "USMALLINT", "UINTEGER", "UBIGINT"}


def _cast(value: object, type_name: str) -> object:
    if value is None:
        return None
    target = type_name.strip().upper()
    family = type_family(target)
    if family == "numeric":
        if target in _INT_TYPES:
            if isinstance(value, bool):
                return int(value)
            if _is_number(value):
                return _cast_float_to_int(value) if isinstance(value, float) else value
            if isinstance(value, str):
                try:
                    return int(value.strip())
                except ValueError:
                    raise ExecutionError(
                        f"cannot cast {value!r} to {type_name}") from None
            raise ExecutionError(f"cannot cast {_type_name(value)} to {type_name}")
        if isinstance(value, bool):
            return float(value)
        if _is_number(value):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value.strip())
            except ValueError:
                raise ExecutionError(f"cannot cast {value!r} to {type_name}") from None
        raise ExecutionError(f"cannot cast {_type_name(value)} to {type_name}")
    if family == "text":
        return _to_varchar(value)
    if family == "boolean":
        if isinstance(value, bool):
            return value
        if _is_number(value):
            return value != 0
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"true", "t", "1"}:
                return True
            if lowered in {"false", "f", "0"}:
                return False
            raise ExecutionError(f"cannot cast {value!r} to {type_name}")
        raise ExecutionError(f"cannot cast {_type_name(value)} to {type_name}")
    if target == "DATE":
        return _as_datetime(value).date()
    if family == "temporal":
        return _as_datetime(value)
    raise ExecutionError(f"unsupported cast type '{type_name}'")


def _round_value(value: object, digits: int) -> float:
    if not _is_number(value):
        raise ExecutionError("ROUND requires a numeric argument")
    quantum = Decimal(1).scaleb(-digits)
    return float(Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP))


def _truncate_datetime(part: str, value: object) -> datetime:
    moment = _as_datetime(value)
    key = part.strip().lower()
    if key == "year":
        return datetime(moment.year, 1, 1)
    if key == "quarter":
        month = 3 * ((moment.month - 1) // 3) + 1
        return datetime(moment.year, month, 1)
    if key == "month":
        return datetime(moment.year, moment.month, 1)
    if key == "week":
        midnight = datetime(moment.year, moment.month, moment.day)
        return midnight - timedelta(days=midnight.weekday())
    if key == "day":
        return datetime(moment.year, moment.month, moment.day)
    if key == "hour":
        return moment.replace(minute=0, second=0, microsecond=0)
    if key == "minute":
        return moment.replace(second=0, microsecond=0)
    if key == "second":
        return moment.replace(microsecond=0)
    raise ExecutionError(
        "date_trunc supports year, quarter, month, week, day, hour, minute, second")


def _call(expr: Call, row: dict, lookup: "_Lookup") -> object:
    name = expr.name.upper()
    if name == "COALESCE":
        for arg in expr.args:
            value = _eval(arg, row, lookup)
            if value is not None:
                return value
        return None
    if name == "NULLIF":
        left = _eval(expr.args[0], row, lookup)
        right = _eval(expr.args[1], row, lookup)
        if left is None:
            return None
        if right is not None and _comparison("=", left, right) is True:
            return None
        return left
    if name in {"LOWER", "UPPER", "TRIM"}:
        value = _eval(expr.args[0], row, lookup)
        if value is None:
            return None
        if not isinstance(value, str):
            raise ExecutionError(f"{name} requires text")
        if name == "LOWER":
            return value.lower()
        if name == "UPPER":
            return value.upper()
        return value.strip()
    if name == "LENGTH":
        value = _eval(expr.args[0], row, lookup)
        if value is None:
            return None
        if not isinstance(value, str):
            raise ExecutionError("LENGTH requires text")
        return len(value)
    if name == "ABS":
        value = _eval(expr.args[0], row, lookup)
        if value is None:
            return None
        if not _is_number(value):
            raise ExecutionError("ABS requires a numeric argument")
        return abs(value)
    if name == "ROUND":
        value = _eval(expr.args[0], row, lookup)
        if value is None:
            return None
        digits = int(_eval(expr.args[1], row, lookup)) if len(expr.args) > 1 else 0
        return _round_value(value, digits)
    if name == "CONCAT":
        parts = [_eval(arg, row, lookup) for arg in expr.args]
        return "".join(_to_varchar(part) for part in parts if part is not None)
    if name == "STRFTIME":
        value = _eval(expr.args[0], row, lookup)
        if value is None:
            return None
        fmt = _eval(expr.args[1], row, lookup)
        if not isinstance(fmt, str):
            raise ExecutionError("strftime requires a text format")
        return _as_datetime(value).strftime(fmt)
    if name == "DATE_TRUNC":
        part = _eval(expr.args[0], row, lookup)
        if not isinstance(part, str):
            raise ExecutionError("date_trunc requires a text part name")
        value = _eval(expr.args[1], row, lookup)
        if value is None:
            return None
        return _truncate_datetime(part, value)
    raise ExecutionError(f"unknown function '{expr.name}'")


# --------------------------------------------------------------------------- #
# Expression evaluation
# --------------------------------------------------------------------------- #


class _Lookup:
    __slots__ = ("map", "opaque", "ambiguous")

    def __init__(
        self,
        mapping: "dict[str, str] | None" = None,
        opaque: bool = False,
        ambiguous: "set[str] | None" = None,
    ) -> None:
        self.map: dict[str, str] = dict(mapping or {})
        self.opaque = opaque
        self.ambiguous: set[str] = set(ambiguous or ())

    def resolve(self, name: str) -> str:
        if "." not in name and name.lower() in self.ambiguous:
            raise ExecutionError(
                f"column '{name}' is ambiguous after a join; qualify it with a source label")
        if name in self.map:
            return self.map[name]
        if self.opaque:
            return name
        raise ExecutionError(f"cannot resolve column '{name}' in this stage")

    @staticmethod
    def flat(names: "list[str] | tuple[str, ...]") -> "_Lookup":
        return _Lookup({name: name for name in names})


def _eval(expr: Expr, row: dict, lookup: _Lookup) -> object:
    if isinstance(expr, Literal):
        return expr.value
    if isinstance(expr, Name):
        return row.get(lookup.resolve(expr.name))
    if isinstance(expr, Unary):
        if expr.op == "not":
            value = _eval(expr.operand, row, lookup)
            condition = _truth(value)
            return None if condition is None else not condition
        value = _eval(expr.operand, row, lookup)
        if value is None:
            return None
        if not _is_number(value):
            raise ExecutionError(f"unary '{expr.op}' requires a numeric operand")
        return -value if expr.op == "-" else value
    if isinstance(expr, Binary):
        if expr.op in {"and", "or"}:
            left = _truth(_eval(expr.left, row, lookup))
            right = _truth(_eval(expr.right, row, lookup))
            return _three_and(left, right) if expr.op == "and" else _three_or(left, right)
        left = _eval(expr.left, row, lookup)
        right = _eval(expr.right, row, lookup)
        if expr.op in {"+", "-", "*", "/", "%", "||"}:
            return _arith(expr.op, left, right)
        return _comparison(expr.op, left, right)
    if isinstance(expr, Like):
        return _like(expr.negated, _eval(expr.value, row, lookup),
                     _eval(expr.pattern, row, lookup))
    if isinstance(expr, InExpr):
        value = _eval(expr.value, row, lookup)
        if value is None:
            return None
        options = [_eval(option, row, lookup) for option in expr.options]
        if any(_comparison("=", value, option) is True for option in options):
            result: bool | None = True
        elif any(option is None for option in options):
            result = None
        else:
            result = False
        if expr.negated and result is not None:
            return not result
        return result
    if isinstance(expr, Between):
        value = _eval(expr.value, row, lookup)
        result = _three_and(
            _comparison(">=", value, _eval(expr.lower, row, lookup)),
            _comparison("<=", value, _eval(expr.upper, row, lookup)),
        )
        if expr.negated and result is not None:
            return not result
        return result
    if isinstance(expr, IsNull):
        value = _eval(expr.value, row, lookup)
        result = value is None
        return (not result) if expr.negated else result
    if isinstance(expr, Case):
        for when, then in expr.branches:
            if _truth(_eval(when, row, lookup)) is True:
                return _eval(then, row, lookup)
        if expr.else_expr is not None:
            return _eval(expr.else_expr, row, lookup)
        return None
    if isinstance(expr, Cast):
        return _cast(_eval(expr.operand, row, lookup), expr.type_name)
    if isinstance(expr, Call):
        return _call(expr, row, lookup)
    raise ExecutionError("unknown expression node")  # pragma: no cover - defensive


# --------------------------------------------------------------------------- #
# Aggregates and windows
# --------------------------------------------------------------------------- #


def _aggregate_values(name: str, values: list) -> object:
    if name == "COUNT":
        return len(values)
    if not values:
        return None
    if name in {"SUM", "AVG"}:
        if not all(_is_number(value) for value in values):
            raise ExecutionError(f"{name} requires numeric operands")
        total = sum(values)
        return total if name == "SUM" else total / len(values)
    try:
        return min(values) if name == "MIN" else max(values)
    except TypeError as exc:
        raise ExecutionError(f"{name} values have incompatible types") from exc


def _distinct_values(values: list) -> list:
    seen: set = set()
    unique: list = []
    for value in values:
        if value not in seen:
            seen.add(value)
            unique.append(value)
    return unique


def _aggregate_item(item, members: list[dict], lookup: _Lookup) -> object:
    call = item.call
    if call.star:
        return len(members)
    values = [_eval(call.args[0], row, lookup) for row in members]
    values = [value for value in values if value is not None]
    if call.distinct:
        values = _distinct_values(values)
    return _aggregate_values(call.name.upper(), values)


@dataclass
class _WindowOrder:
    descending: bool
    get: Callable[[dict], object]


def _window_order(keys: "list[SortKey]", lookup: _Lookup) -> list[_WindowOrder]:
    return [_WindowOrder(key.descending, lambda row, e=key.expr: _eval(e, row, lookup))
            for key in keys]


def _sorted_indices(rows: list[dict], order: list[_WindowOrder],
                    indices: list[int]) -> list[int]:
    current = list(indices)
    for key in reversed(order):
        decorated = [(key.get(rows[i]), i) for i in current]
        non_null = [(value, i) for value, i in decorated if value is not None]
        nulls = [i for value, i in decorated if value is None]
        try:
            non_null.sort(key=lambda pair: pair[0], reverse=key.descending)
        except TypeError as exc:
            raise ExecutionError("ORDER BY values have incompatible types") from exc
        current = [i for _, i in non_null] + nulls
    return current


def _window_values(stage: WindowStage, rows: list[dict], lookup: _Lookup) -> list:
    call = stage.call
    func = call.func.upper()
    order = _window_order(call.order_by, lookup)
    partitions: dict[tuple, list[int]] = {}
    if call.partition_by:
        for index, row in enumerate(rows):
            key = tuple(_eval(expr, row, lookup) for expr in call.partition_by)
            partitions.setdefault(key, []).append(index)
    else:
        partitions[()] = list(range(len(rows)))

    results: list = [None] * len(rows)
    for indices in partitions.values():
        ordered = _sorted_indices(rows, order, indices)
        if func == "ROW_NUMBER":
            for position, index in enumerate(ordered, start=1):
                results[index] = position
            continue
        if func in {"RANK", "DENSE_RANK"}:
            key_values = [tuple(key.get(rows[i]) for key in order) for i in ordered]
            rank = 0
            dense = 0
            for position, index in enumerate(ordered):
                if position == 0 or key_values[position] != key_values[position - 1]:
                    rank = position + 1
                    dense += 1
                results[index] = rank if func == "RANK" else dense
            continue
        if func in {"LAG", "LEAD"}:
            offset = int(_eval(call.args[1], rows[ordered[0]], lookup)) if len(call.args) > 1 else 1
            default = _eval(call.args[2], rows[ordered[0]], lookup) if len(call.args) > 2 else None
            values = [_eval(call.args[0], rows[index], lookup) for index in ordered]
            for position, index in enumerate(ordered):
                source = position - offset if func == "LAG" else position + offset
                results[index] = values[source] if 0 <= source < len(values) else default
            continue
        # Aggregate window: whole partition, or RANGE UNBOUNDED PRECEDING TO
        # CURRENT ROW (peers included) when the spec has an ORDER BY.
        if not call.order_by:
            results_for_partition = _window_aggregate(func, call, ordered, rows, lookup)
            for index in ordered:
                results[index] = results_for_partition
            continue
        key_values = [tuple(key.get(rows[i]) for key in order) for i in ordered]
        accumulated: list = []
        count = 0
        position = 0
        while position < len(ordered):
            end = position
            while end + 1 < len(ordered) and key_values[end + 1] == key_values[position]:
                end += 1
            for index in ordered[position:end + 1]:
                count += 1
                if not call.star:
                    accumulated.append(_eval(call.args[0], rows[index], lookup))
            if call.star:
                value: object = count
            else:
                value = _aggregate_values(
                    func, [v for v in accumulated if v is not None])
            for index in ordered[position:end + 1]:
                results[index] = value
            position = end + 1
    return results


def _window_aggregate(func: str, call: WindowCall, indices: list[int],
                      rows: list[dict], lookup: _Lookup) -> object:
    if call.star:
        return len(indices)
    values = [_eval(call.args[0], rows[index], lookup) for index in indices]
    return _aggregate_values(func, [value for value in values if value is not None])


# --------------------------------------------------------------------------- #
# Chain execution (mirrors sqlgen._build_chain)
# --------------------------------------------------------------------------- #


@dataclass
class _State:
    rows: list[dict]
    lookup: _Lookup
    live: list[str]
    order: str | None


@dataclass
class _Context:
    tables: dict[str, Table]
    next_order_name: Callable[[], str]


def _copy_rows(table: Table) -> list[dict]:
    return [dict(row) for row in table.rows]


def _order_rows(rows: list[dict], order: str | None) -> list[dict]:
    """Apply the hidden sort ordinal (NULL ordinals last, like DuckDB)."""
    if order is None:
        return rows
    non_null = [row for row in rows if row.get(order) is not None]
    nulls = [row for row in rows if row.get(order) is None]
    non_null.sort(key=lambda row: row[order])
    return non_null + nulls


def _execute_chain(
    stages: Program,
    context: _Context,
    nested: bool,
    inherited_withs: dict[str, _State],
) -> _State:
    with_names = dict(inherited_withs)
    state: _State | None = None
    order: str | None = None
    position = 0

    while position < len(stages):
        stage = stages[position]
        if isinstance(stage, WithStage):
            inner = _execute_chain(stage.program, context, True, with_names)
            with_names[stage.name] = inner
            if state is None:
                state = inner
            position += 1
            continue

        rows = state.rows if state is not None else []
        lookup = state.lookup if state is not None else _Lookup()
        live = state.live if state is not None else []

        try:
            if isinstance(stage, FromStage):
                source = stage.source
                if source.program is not None:
                    inner = _execute_chain(source.program, context, True, with_names)
                    state = _from_state(inner, source.alias or "")
                elif source.name in with_names:
                    inner = with_names[source.name]
                    state = _from_state(inner, source.alias or source.name)
                else:
                    table = context.tables.get(source.name)
                    if table is None:
                        raise ExecutionError(f"unknown table '{source.name}'")
                    label = source.alias or source.name.split(".")[-1]
                    mapping: dict[str, str] = {}
                    for column in table.columns:
                        mapping[column] = column
                        mapping[f"{label}.{column}"] = column
                    state = _State(_copy_rows(table), _Lookup(mapping),
                                   list(table.columns), None)
                order = None
            elif isinstance(stage, JoinStage):
                source = stage.source
                if source.program is not None:
                    inner = _execute_chain(source.program, context, True, with_names)
                    label = source.alias or ""
                    right_rows, right_columns = _copy_rows_from_state(inner), list(inner.live)
                elif source.name in with_names:
                    inner = with_names[source.name]
                    label = source.alias or source.name
                    right_rows, right_columns = _copy_rows_from_state(inner), list(inner.live)
                else:
                    table = context.tables.get(source.name)
                    if table is None:
                        raise ExecutionError(f"unknown joined table '{source.name}'")
                    label = source.alias or source.name.split(".")[-1]
                    right_rows, right_columns = _copy_rows(table), list(table.columns)
                state = _join_stage(stage, rows, lookup, live, right_rows,
                                    right_columns, label, order)
            elif isinstance(stage, FilterStage) or isinstance(stage, HavingStage):
                state = _State(
                    [row for row in rows
                     if _truth(_eval(stage.condition, row, lookup)) is True],
                    lookup, live, order)
            elif isinstance(stage, DeriveStage):
                for item in stage.items:
                    if item.name in live:
                        raise ExecutionError(
                            f"derive '{item.name}' conflicts with a live column; "
                            "choose another name")
                    for row in rows:
                        row[item.name] = _eval(item.expr, row, lookup)
                    lookup = _Lookup({**lookup.map, item.name: item.name},
                                     lookup.opaque, lookup.ambiguous)
                    live = [*live, item.name]
                state = _State(rows, lookup, live, order)
            elif isinstance(stage, GroupByStage):
                aggregate = stages[position + 1] if position + 1 < len(stages) else None
                if not isinstance(aggregate, AggregateStage):
                    raise ExecutionError("group by must be immediately followed by aggregate")
                state = _group_unit(stage, aggregate, rows, lookup)
                position += 1
                order = None
            elif isinstance(stage, WindowStage):
                if stage.name in live:
                    raise ExecutionError(
                        f"window '{stage.name}' conflicts with a live column; "
                        "choose another name")
                values = _window_values(stage, rows, lookup)
                for row, value in zip(rows, values):
                    row[stage.name] = value
                lookup = _Lookup({**lookup.map, stage.name: stage.name},
                                 lookup.opaque, lookup.ambiguous)
                live = [*live, stage.name]
                state = _State(rows, lookup, live, order)
            elif isinstance(stage, SelectStage):
                state = _select_stage(stage, rows, lookup, live, order)
            elif isinstance(stage, SortStage):
                if order is not None:
                    for row in rows:
                        row.pop(order, None)
                ordered_indices = _sorted_indices(rows, _window_order(stage.keys, lookup),
                                                  list(range(len(rows))))
                next_order = context.next_order_name()
                for number, index in enumerate(ordered_indices, start=1):
                    rows[index][next_order] = number
                state = _State(rows, lookup, live, next_order)
                order = next_order
            elif isinstance(stage, (SkipStage, TakeStage)):
                ordered_rows = _order_rows(rows, order)
                if isinstance(stage, SkipStage):
                    rows = ordered_rows[stage.count:]
                else:
                    rows = ordered_rows[:stage.count]
                state = _State(rows, lookup, live, order)
            elif isinstance(stage, DistinctStage):
                seen: set = set()
                unique: list[dict] = []
                for row in rows:
                    key = tuple(row.get(column) for column in live)
                    if key in seen:
                        continue
                    seen.add(key)
                    if order is not None:
                        row.pop(order, None)
                    unique.append(row)
                state = _State(unique, lookup, live, None)
                order = None
            elif isinstance(stage, SetStage):
                inner = _execute_chain(stage.program, context, True, with_names)
                state = _set_stage(stage, rows, lookup, live, inner)
                order = None
            elif isinstance(stage, RawSqlStage):
                raise ExecutionError(
                    "the sql hatch runs DuckDB SQL and needs the DuckDB engine; "
                    "run with --engine duckdb (or install the duckdb extra, which "
                    "also makes it the default)")
            else:  # pragma: no cover - defensive
                raise ExecutionError(f"cannot execute stage '{stage.keyword}'")
        except StageExecutionError:
            raise
        except ExecutionError as exc:
            raise StageExecutionError(stage, str(exc)) from exc

        position += 1

    if state is None:
        raise ExecutionError(
            "a program must define a relation with a from stage or a with definition")
    if nested and state.order is not None:
        for row in state.rows:
            row.pop(state.order, None)
        state = _State(state.rows, state.lookup, state.live, None)
    return state


def _copy_rows_from_state(state: _State) -> list[dict]:
    return [dict(row) for row in state.rows]


def _from_state(inner: _State, label: str) -> _State:
    mapping: dict[str, str] = {}
    for name in inner.live:
        mapping[name] = name
        if label:
            mapping[f"{label}.{name}"] = name
    return _State(_copy_rows_from_state(inner),
                  _Lookup(mapping, inner.lookup.opaque, inner.lookup.ambiguous),
                  list(inner.live), inner.order)


def _join_stage(
    stage: JoinStage,
    left_rows: list[dict],
    left_lookup: _Lookup,
    left_live: list[str],
    right_rows: list[dict],
    right_columns: list[str],
    label: str,
    order: str | None,
) -> _State:
    on_map: dict[str, str] = {}
    on_ambiguous = set(left_lookup.ambiguous)
    for name in left_live:
        on_map[name] = name
    for name, physical in left_lookup.map.items():
        if "." in name:
            on_map[name] = physical
    for column in right_columns:
        on_map[f"{label}.{column}"] = f"{label}.{column}"
        if any(name.lower() == column.lower() for name in left_live):
            on_ambiguous.add(column.lower())
    on_lookup = _Lookup(on_map, False, on_ambiguous)

    def pair_ctx(left_row: dict, right_row: dict | None) -> dict:
        context = dict(left_row)
        for name, physical in left_lookup.map.items():
            if "." in name:
                context[name] = left_row.get(physical)
        for column in right_columns:
            context[f"{label}.{column}"] = None if right_row is None else right_row.get(column)
        return context

    is_cross = stage.join_kind == "cross"
    exposed_map: dict[str, str] = {}
    taken = {name.lower() for name in left_live}
    ambiguous = set(left_lookup.ambiguous)
    new_live = list(left_live)
    for column in right_columns:
        collision = column.lower() in taken
        if collision:
            ambiguous.add(column.lower())
        exposed = f"{label}_{column}" if collision else column
        if exposed.lower() in taken:
            raise ExecutionError(
                f"join exposes duplicate column '{exposed}'; alias one of the joins")
        taken.add(exposed.lower())
        new_live.append(exposed)
        exposed_map[column] = exposed

    def merged(left_row: dict, right_row: dict | None) -> dict:
        combined = dict(left_row)
        for column in right_columns:
            combined[exposed_map[column]] = (
                None if right_row is None else right_row.get(column))
        return combined

    def matched(right_row: dict, left_row: dict) -> bool:
        if is_cross:
            return True
        return _truth(_eval(stage.on, pair_ctx(left_row, right_row), on_lookup)) is True

    output: list[dict] = []
    kind = stage.join_kind
    matched_right_ids: set[int] = set()
    for left_row in left_rows:
        found = False
        for right_index, right_row in enumerate(right_rows):
            if matched(right_row, left_row):
                found = True
                matched_right_ids.add(right_index)
                output.append(merged(left_row, right_row))
        if not found and kind in {"left", "full"}:
            output.append(merged(left_row, None))
    if kind in {"right", "full"}:
        for right_index, right_row in enumerate(right_rows):
            if right_index in matched_right_ids:
                continue
            combined = {name: None for name in left_rows[0]} if left_rows else {}
            for name, physical in left_lookup.map.items():
                if "." in name:
                    combined[name] = None
            for column in right_columns:
                combined[exposed_map[column]] = right_row.get(column)
            output.append(combined)

    lookup = dict(left_lookup.map)
    for column in right_columns:
        exposed = exposed_map[column]
        if column not in lookup:
            lookup[column] = exposed
        lookup[f"{label}.{column}"] = exposed
    return _State(output, _Lookup(lookup, False, ambiguous), new_live, order)


def _group_unit(
    group: GroupByStage, aggregate: AggregateStage, rows: list[dict], lookup: _Lookup
) -> _State:
    grouped: dict[tuple, list[dict]] = {}
    ordered_keys: list[tuple] = []
    for row in rows:
        key = tuple(_eval(item.expr, row, lookup) for item in group.keys)
        if key not in grouped:
            grouped[key] = []
            ordered_keys.append(key)
        grouped[key].append(row)

    new_rows: list[dict] = []
    for key in ordered_keys:
        members = grouped[key]
        output = {item.name: _eval(item.expr, members[0], lookup) for item in group.keys}
        for item in aggregate.items:
            output[item.name] = _aggregate_item(item, members, lookup)
        new_rows.append(output)

    names = [item.name for item in group.keys] + [item.name for item in aggregate.items]
    new_lookup = _Lookup.flat(names)
    for item in group.keys:
        if isinstance(item.expr, Name):
            physical = lookup.map.get(item.expr.name)
            for name, column in lookup.map.items():
                if "." in name and column == physical:
                    new_lookup.map[name] = item.name
    return _State(new_rows, new_lookup, names, None)


def _select_stage(
    stage: SelectStage,
    rows: list[dict],
    lookup: _Lookup,
    live: list[str],
    order: str | None,
) -> _State:
    if stage.star and not stage.items:
        return _State(rows, _Lookup.flat(live), live, order)
    item_names = [item.alias or expression_name(item.expr) for item in stage.items]
    if stage.star:
        live_names = {name.lower() for name in live}
        duplicate = next((name for name in item_names if name.lower() in live_names), None)
        if duplicate is not None:
            raise ExecutionError(
                f"select output '{duplicate}' duplicates a live column from '*'; "
                "choose a different alias")
    names = [*live, *item_names] if stage.star else list(item_names)
    output_rows: list[dict] = []
    for row in rows:
        if stage.star:
            projected = dict(row)
        else:
            projected = {}
            if order is not None:
                projected[order] = row.get(order)
        for item, name in zip(stage.items, item_names):
            projected[name] = _eval(item.expr, row, lookup)
        output_rows.append(projected)
    return _State(output_rows, _Lookup.flat(names), names, order)


def _set_stage(
    stage: SetStage, rows: list[dict], lookup: _Lookup, live: list[str], inner: _State
) -> _State:
    left_rows = [{column: row.get(column) for column in live} for row in rows]
    right_rows = [{column: row.get(column) for column in inner.live}
                  for row in inner.rows]
    if stage.op == "union all":
        combined = left_rows + right_rows
    else:
        left_keys = [tuple(row.get(column) for column in live) for row in left_rows]
        right_keys = {tuple(row.get(column) for column in inner.live)
                      for row in right_rows}
        if stage.op == "union":
            seen: set = set()
            combined = []
            for row, key in zip(left_rows + right_rows, left_keys + [
                    tuple(row.get(column) for column in inner.live) for row in right_rows]):
                if key not in seen:
                    seen.add(key)
                    combined.append(row)
        elif stage.op == "intersect":
            seen = set()
            combined = []
            for row, key in zip(left_rows, left_keys):
                if key in right_keys and key not in seen:
                    seen.add(key)
                    combined.append(row)
        elif stage.op == "except":
            seen = set()
            combined = []
            for row, key in zip(left_rows, left_keys):
                if key not in right_keys and key not in seen:
                    seen.add(key)
                    combined.append(row)
        else:  # pragma: no cover - defensive
            raise ExecutionError(f"unknown set operator '{stage.op}'")
    return _State(combined, lookup, live, None)


def execute_program(program: Program, tables: dict[str, Table],
                    *, schema: Schema | None = None) -> Table:
    """Execute a parsed `.unsequel` program on the in-memory engine."""
    if not program:
        raise ExecutionError("a program cannot be empty")
    reserved = _internal_order_names(program, schema or Schema.from_dict({"tables": {}}))
    raw_sql_texts = [stage.text.lower() for stage in program
                     if isinstance(stage, RawSqlStage)]
    counter = {"ordinal": 0}

    def next_order_name() -> str:
        candidate = ""
        while True:
            counter["ordinal"] += 1
            candidate = f"__unsequel_internal_order_{counter['ordinal']}"
            lowered = candidate.lower()
            if lowered not in reserved and not any(
                lowered in text for text in raw_sql_texts
            ):
                break
        reserved.add(candidate.lower())
        return candidate

    context = _Context(dict(tables), next_order_name)
    state = _execute_chain(program, context, False, {})
    ordered_rows = _order_rows(state.rows, state.order)
    result_rows = [{column: row.get(column) for column in state.live}
                   for row in ordered_rows]
    return Table("result", list(state.live), result_rows)
