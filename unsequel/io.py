from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path
from typing import Any

from .errors import ExecutionError
from .model import Table


def _coerce(value: str) -> Any:
    """Infer simple CSV scalar types without third-party dependencies."""
    value = value.strip()
    if value == "" or value.lower() in {"null", "none", "nan"}:
        return None
    try:
        return int(value)
    except ValueError:
        pass
    try:
        number = float(value)
        return int(number) if number.is_integer() else number
    except ValueError:
        return value


def load_table(name: str, path: str | Path) -> Table:
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
        rows = [{column: row.get(column) for column in columns} for row in payload]
        return Table(name, columns, rows)
    if suffix != ".csv":
        raise ExecutionError(f"Unsupported data format {suffix!r}; use .csv or .json")
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        columns = list(reader.fieldnames or [])
        if not columns:
            raise ExecutionError(f"CSV file has no header: {path}")
        rows = [{key: _coerce(value) for key, value in row.items()} for row in reader]
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
