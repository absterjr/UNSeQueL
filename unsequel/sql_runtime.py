"""Execute a raw-SQL pipeline stage against the previous relation.

Uses the standard-library sqlite3 module so the escape hatch does not add a
runtime dependency. The previous pipeline result is exposed as __input__;
original named tables remain available under their own names. On the DuckDB
engine the raw stage runs inside the generated CTE chain instead
(see unsequel.codegen).
"""

from __future__ import annotations

import sqlite3

from .errors import ExecutionError
from .model import Table


def _quote_sqlite_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _sql_type(value: object) -> str:
    if isinstance(value, bool) or isinstance(value, int):
        return "INTEGER"
    if isinstance(value, float):
        return "REAL"
    return "TEXT"


def _column_type(table: Table, column: str) -> str:
    sample = next((row.get(column) for row in table.rows if row.get(column) is not None), None)
    return _sql_type(sample)


def _create_table(connection: sqlite3.Connection, table: Table) -> None:
    name = _quote_sqlite_identifier(table.name)
    if not table.columns:
        connection.execute(f"CREATE TABLE {name} (value INTEGER)")
        return
    columns = ", ".join(
        f"{_quote_sqlite_identifier(column)} {_column_type(table, column)}"
        for column in table.columns
    )
    connection.execute(f"CREATE TABLE {name} ({columns})")
    if table.rows:
        placeholders = ", ".join("?" for _ in table.columns)
        connection.executemany(
            f"INSERT INTO {name} VALUES ({placeholders})",
            [tuple(row.get(column) for column in table.columns) for row in table.rows],
        )


def run_sql_stage(sql: str, tables: dict[str, Table], current: Table) -> Table:
    """Run one sql "..." stage. The previous relation is table __input__."""
    connection = sqlite3.connect(":memory:")
    try:
        for table in tables.values():
            _create_table(connection, table)
        _create_table(connection, Table("__input__", list(current.columns), list(current.rows)))
        cursor = connection.execute(sql)
        columns = [description[0] for description in cursor.description or []]
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
        cursor.close()
        return Table("__input__", columns, rows)
    except sqlite3.Error as exc:
        raise ExecutionError(f"sql stage failed: {exc}") from exc
    finally:
        connection.close()
