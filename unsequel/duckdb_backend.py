"""Run generated SQL against a throwaway in-memory DuckDB.

This is optional: `duckdb` is an extra (`pip install unsequel[duckdb]`). The
core language and the in-memory engine never import it.
"""

from __future__ import annotations

from pathlib import Path

from .codegen import _ident
from .errors import UnsequelError
from .io import infer_csv_types
from .model import Table


class DuckDBUnavailable(UnsequelError):
    """The duckdb package is not installed."""


def _sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _reader(path: str, *, delimiter: str | None = None,
            column_types: dict[str, str] | None = None) -> str:
    lowered = path.lower()
    if lowered.endswith(".json"):
        type_map = {
            "text": "VARCHAR", "string": "VARCHAR", "varchar": "VARCHAR",
            "int": "BIGINT", "integer": "BIGINT", "number": "DOUBLE",
            "numeric": "DOUBLE", "float": "DOUBLE", "real": "DOUBLE",
            "double": "DOUBLE", "decimal": "DOUBLE", "bool": "BOOLEAN",
            "boolean": "BOOLEAN",
        }
        overrides = column_types or {}
        columns = ", ".join(
            f"{_sql_string(name)}: {_sql_string(type_map.get(kind.strip().lower(), ''))}"
            for name, kind in overrides.items())
        if overrides and any(not type_map.get(kind.strip().lower()) for kind in overrides.values()):
            bad = next(kind for kind in overrides.values() if not type_map.get(kind.strip().lower()))
            raise UnsequelError(f"unsupported column type {bad!r}")
        options = f", columns={{{columns}}}" if columns else ""
        return f"read_json_auto({_sql_string(path)}{options})"
    if lowered.endswith((".parquet", ".pq")):
        if column_types:
            raise UnsequelError("--column-type is only supported for CSV/JSON inputs")
        return f"read_parquet({_sql_string(path)})"

    types = infer_csv_types(path, delimiter, column_types)
    types_sql = ", ".join(f"{_sql_string(column)}: {_sql_string(type_name)}"
                           for column, type_name in types.items())
    delimiter_sql = _sql_string(delimiter or ",")
    return (f"read_csv_auto({_sql_string(path)}, delim={delimiter_sql}, "
            f"types={{{types_sql}}})")


def _import_duckdb():
    try:
        import duckdb
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise DuckDBUnavailable(
            "duckdb is not installed; run `pip install \"unsequel[duckdb]\"`"
        ) from exc
    return duckdb


def connect(data: dict[str, str], *, delimiter: str | None = None,
            column_types: dict[str, dict[str, str]] | None = None):
    duckdb = _import_duckdb()
    con = duckdb.connect()
    for name, path in data.items():
        if not Path(path).exists():
            raise UnsequelError(f"data file not found: {path}")
        table_types = (column_types or {}).get(name, {})
        con.execute(
            f"CREATE TABLE {_ident(name)} AS SELECT * FROM "
            f"{_reader(str(path), delimiter=delimiter, column_types=table_types)}")
    return con


def describe_source(path: str, *, delimiter: str | None = None,
                    column_types: dict[str, str] | None = None) -> list[tuple[str, str]]:
    """Column names and DuckDB type names for a data file (for introspection)."""
    if not Path(path).exists():
        raise UnsequelError(f"data file not found: {path}")
    duckdb = _import_duckdb()
    con = duckdb.connect()
    cursor = con.execute(
        "DESCRIBE SELECT * FROM "
        f"{_reader(str(path), delimiter=delimiter, column_types=column_types)}")
    return [(str(row[0]), str(row[1])) for row in cursor.fetchall()]


def run_sql(sql: str, data: dict[str, str], *, delimiter: str | None = None,
            column_types: dict[str, dict[str, str]] | None = None) -> Table:
    """Execute one SQL statement and return the result as an in-memory Table."""
    cursor = connect(data, delimiter=delimiter, column_types=column_types).execute(sql)
    columns = [description[0] for description in cursor.description]
    rows = [dict(zip(columns, record)) for record in cursor.fetchall()]
    return Table("result", columns, rows)
