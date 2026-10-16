from __future__ import annotations

import csv
import json
import math
import re
import sqlite3
from pathlib import Path
from typing import Any

from .errors import ExecutionError
from .model import Table


# A zero followed by a digit ("02134", "012.5") marks an identifier, as in DuckDB's sniffer.
_LEADING_ZERO = re.compile(r"[+-]?0\d")
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
# No Z/offset suffix: those stay text rather than being silently converted to UTC.
_ISO_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?")
_INT64_MIN, _INT64_MAX = -(2 ** 63), 2 ** 63 - 1
_TYPE_ALIASES = {
    "text": "text", "string": "text", "varchar": "text", "char": "text",
    "int": "integer", "integer": "integer",
    "number": "number", "numeric": "number", "float": "number",
    "real": "number", "double": "number", "decimal": "number",
    "bool": "boolean", "boolean": "boolean",
}


def _delimiter(value: str | None) -> str:
    delimiter = "," if value is None else value
    if len(delimiter) != 1 or delimiter in {'"', "\r", "\n"}:
        raise ExecutionError("CSV delimiter must be exactly one non-quote character")
    return delimiter


def _normalise_column_types(column_types: dict[str, str] | None) -> dict[str, str]:
    result: dict[str, str] = {}
    for column, raw_type in (column_types or {}).items():
        type_name = _TYPE_ALIASES.get(raw_type.strip().lower())
        if type_name is None:
            supported = ", ".join(sorted(_TYPE_ALIASES))
            raise ExecutionError(
                f"unsupported type {raw_type!r} for column {column!r}; choose {supported}")
        result[column.casefold()] = type_name
    return result


def _blank_to_null(value: str) -> str | None:
    # CSV has no universal NULL spelling. Preserve literal "null", "none", and
    # "nan"; only an empty/whitespace-only cell is treated as missing.
    return None if value.strip() == "" else value


def _numeric_value(value: str) -> int | float | None:
    stripped = value.strip()
    if _LEADING_ZERO.match(stripped):
        return None
    try:
        return int(stripped)
    except ValueError:
        try:
            number = float(stripped)
        except ValueError:
            return None
        return number if math.isfinite(number) else None


def _infer_column(values: list[str | None]) -> list[Any]:
    """Infer one type for a whole CSV column, preserving identifiers as text."""
    non_null = [value for value in values if value is not None]
    if not non_null:
        return [None] * len(values)

    parsed = [_numeric_value(value) for value in non_null]
    if all(value is not None for value in parsed):
        integer_column = all(type(value) is int for value in parsed)
        # Same rules as the DuckDB sniffer path: integers too wide for BIGINT, or
        # 16+ digit integers in a DOUBLE column, stay text so no digit is lost.
        if integer_column and any(not _INT64_MIN <= value <= _INT64_MAX for value in parsed):
            return values
        if not integer_column and any(
                type(value) is int and abs(value) >= 10 ** 15 for value in parsed):
            return values
        iterator = iter(parsed)
        output: list[Any] = []
        for value in values:
            if value is None:
                output.append(None)
            else:
                number = next(iterator)
                output.append(int(number) if integer_column else number)
        return output

    # Mixed or dirty columns are consistently text, never int/str mixtures.
    return values


def _convert_override(value: str | None, type_name: str, column: str,
                      row_number: int) -> Any:
    if value is None:
        return None
    stripped = value.strip()
    try:
        if type_name == "text":
            return value
        if type_name == "integer":
            return int(stripped)
        if type_name == "number":
            number = float(stripped)
            if not math.isfinite(number):
                raise ValueError("not finite")
            return int(number) if number.is_integer() else number
        lowered = stripped.lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
        raise ValueError("expected true/false, 1/0, yes/no, or on/off")
    except ValueError as exc:
        raise ExecutionError(
            f"cannot convert {value!r} in column {column!r} at CSV row {row_number} "
            f"to {type_name}") from exc


def _read_csv(path: Path, delimiter: str) -> tuple[list[str], list[list[str]]]:
    try:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.reader(handle, delimiter=delimiter)
            columns = next(reader, [])
            if not columns:
                raise ExecutionError(f"CSV file has no header: {path}")
            if any(column.strip() == "" for column in columns):
                raise ExecutionError(f"CSV file has an empty column name: {path}")
            normalized = [column.casefold() for column in columns]
            duplicates = sorted({column for column in normalized if normalized.count(column) > 1})
            if duplicates:
                original = next(column for column in columns if column.casefold() == duplicates[0])
                raise ExecutionError(
                    f"CSV file has duplicate column name {original!r}: {path}")
            rows: list[list[str]] = []
            for row_number, values in enumerate(reader, start=2):
                if len(values) != len(columns):
                    raise ExecutionError(
                        f"CSV row {row_number} has {len(values)} fields; "
                        f"expected {len(columns)} in {path}")
                rows.append(values)
            return columns, rows
    except csv.Error as exc:
        raise ExecutionError(f"Invalid CSV in {path}: {exc}") from exc


def infer_csv_types(path: str | Path, delimiter: str | None = None,
                    column_types: dict[str, str] | None = None) -> dict[str, str]:
    """Infer DuckDB type names with the same whole-column rules as `load_table`."""
    path = Path(path)
    columns, rows = _read_csv(path, _delimiter(delimiter))
    overrides = _normalise_column_types(column_types)
    unknown = set(overrides) - {column.casefold() for column in columns}
    if unknown:
        raise ExecutionError(
            f"column type override refers to unknown column {sorted(unknown)[0]!r}")
    types: dict[str, str] = {}
    for index, column in enumerate(columns):
        override = overrides.get(column.casefold())
        if override is not None:
            types[column] = {
                "text": "VARCHAR", "integer": "BIGINT", "number": "DOUBLE",
                "boolean": "BOOLEAN",
            }[override]
            continue
        raw = [str(value).strip() for value in (_blank_to_null(row[index]) for row in rows)
               if value is not None]
        if raw and all(_ISO_DATE.fullmatch(value) for value in raw):
            types[column] = "DATE"
            continue
        if raw and all(_ISO_TIMESTAMP.fullmatch(value) for value in raw):
            types[column] = "TIMESTAMP"
            continue
        inferred = _infer_column([_blank_to_null(row[index]) for row in rows])
        non_null = [value for value in inferred if value is not None]
        if non_null and all(type(value) is int for value in non_null):
            types[column] = "BIGINT"
        elif non_null and all(type(value) in {int, float} for value in non_null):
            types[column] = "DOUBLE"
        else:
            types[column] = "VARCHAR"
    return types


def load_table(name: str, path: str | Path, *, delimiter: str | None = None,
               column_types: dict[str, str] | None = None) -> Table:
    """Load one CSV or JSON array into a Table."""
    path = Path(path)
    if not path.exists():
        raise ExecutionError(f"Data file does not exist: {path}")
    suffix = path.suffix.lower()
    if suffix == ".json":
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ExecutionError(f"Invalid JSON in {path}: {exc}") from exc
        if not isinstance(payload, list) or any(not isinstance(row, dict) for row in payload):
            raise ExecutionError("JSON input must be an array of objects")
        columns = list(dict.fromkeys(key for row in payload for key in row))
        overrides = _normalise_column_types(column_types)
        unknown = set(overrides) - {column.casefold() for column in columns}
        if unknown:
            raise ExecutionError(
                f"column type override refers to unknown column {sorted(unknown)[0]!r} "
                f"in table {name!r}")
        rows = []
        for row_number, row in enumerate(payload, start=1):
            converted = {}
            for column in columns:
                value = row.get(column)
                override = overrides.get(column.casefold())
                if override is not None and value is not None:
                    value = _convert_override(str(value), override, column, row_number)
                converted[column] = value
            rows.append(converted)
        return Table(name, columns, rows)
    if suffix != ".csv":
        raise ExecutionError(f"Unsupported data format {suffix!r}; use .csv or .json")
    csv_delimiter = _delimiter(delimiter)
    columns, raw_rows = _read_csv(path, csv_delimiter)
    overrides = _normalise_column_types(column_types)
    column_names = {column.casefold() for column in columns}
    unknown = set(overrides) - column_names
    if unknown:
        raise ExecutionError(
            f"column type override refers to unknown column {sorted(unknown)[0]!r} "
            f"in table {name!r}")

    converted_columns: list[list[Any]] = []
    for index, column in enumerate(columns):
        values = [_blank_to_null(row[index]) for row in raw_rows]
        override = overrides.get(column.casefold())
        if override is None:
            converted = _infer_column(values)
        else:
            converted = [
                _convert_override(value, override, column, row_number)
                for row_number, value in enumerate(values, start=2)
            ]
        converted_columns.append(converted)
    rows = [
        {column: converted_columns[index][row_index]
         for index, column in enumerate(columns)}
        for row_index in range(len(raw_rows))
    ]
    return Table(name, columns, rows)


def _quote_sqlite_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def load_sqlite_tables(path: str | Path) -> dict[str, Table]:
    """Load SQLite tables and views through the standard-library adapter."""
    path = Path(path)
    if not path.exists():
        raise ExecutionError(f"SQLite database does not exist: {path}")
    connection = None
    try:
        connection = sqlite3.connect(str(path))
        with connection:
            names = connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%' "
                "ORDER BY name"
            ).fetchall()
            tables: dict[str, Table] = {}
            for (name,) in names:
                cursor = connection.execute(f"SELECT * FROM {_quote_sqlite_identifier(name)}")
                columns = [description[0] for description in cursor.description or []]
                rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
                cursor.close()
                tables[name] = Table(name, columns, rows)
            return tables
    except sqlite3.Error as exc:
        raise ExecutionError(f"Could not read SQLite database {path}: {exc}") from exc
    finally:
        if connection is not None:
            connection.close()
