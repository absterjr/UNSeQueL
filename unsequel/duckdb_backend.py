"""Run generated SQL against DuckDB.

This is optional: `duckdb` is an extra (`pip install unsequel[duckdb]`). The
core language and the in-memory engine never import it.

Databases open **read-only** until the language can create tables
(`create table = (...)` / `into`), so a second session (or an open DBeaver
window) is never blocked and a `--db` typo reports "not found" instead of
silently creating an empty database. File sources load as **temporary
tables**, which never persist over database contents.
"""

from __future__ import annotations

from pathlib import Path

from .codegen import _ident
from .errors import UnsequelError
from .io import infer_csv_types
from .model import Table
from .schema import Schema


class DuckDBUnavailable(UnsequelError):
    """The duckdb package is not installed."""


def _sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _import_duckdb():
    try:
        import duckdb
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise DuckDBUnavailable(
            "duckdb is not installed; run `pip install \"unsequel[duckdb]\"` "
            "(the standalone binaries bundle it)"
        ) from exc
    return duckdb


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


def connect(data: dict[str, str], *, delimiter: str | None = None,
            column_types: dict[str, dict[str, str]] | None = None,
            database_path: str | None = None,
            attach: dict[str, str] | None = None):
    duckdb = _import_duckdb()
    if database_path is not None:
        if not Path(database_path).exists():
            raise UnsequelError(
                f"database not found: {database_path} "
                "(databases are read-only until `create table = (...)` ships)")
        con = duckdb.connect(database_path, read_only=True)
    else:
        con = duckdb.connect()
    for name, path in (attach or {}).items():
        if not Path(path).exists():
            raise UnsequelError(f"database to attach not found: {path}")
        try:
            con.execute(f"ATTACH {_sql_string(path)} AS {_ident(name)} (READ_ONLY)")
        except Exception as exc:
            raise UnsequelError(
                f"could not attach database {path}: {exc}") from exc
    for name, path in data.items():
        if not Path(path).exists():
            raise UnsequelError(f"data file not found: {path}")
        table_types = (column_types or {}).get(name, {})
        try:
            con.execute(
                f"CREATE OR REPLACE TEMP TABLE {_ident(name)} AS SELECT * FROM "
                f"{_reader(str(path), delimiter=delimiter, column_types=table_types)}")
        except Exception as exc:
            raise UnsequelError(
                f"could not load data file {path}: {exc}") from exc
    return con


def describe_source(path: str, *, delimiter: str | None = None,
                    column_types: dict[str, str] | None = None) -> list[tuple[str, str]]:
    """Column names and DuckDB type names for a data file (for introspection)."""
    if not Path(path).exists():
        raise UnsequelError(f"data file not found: {path}")
    duckdb = _import_duckdb()
    con = duckdb.connect()
    try:
        cursor = con.execute(
            "DESCRIBE SELECT * FROM "
            f"{_reader(str(path), delimiter=delimiter, column_types=column_types)}")
        return [(str(row[0]), str(row[1])) for row in cursor.fetchall()]
    except Exception as exc:
        raise UnsequelError(f"could not inspect data file {path}: {exc}") from exc


def describe_database(con) -> Schema:
    """A Schema for everything the connection sees: database tables, attached
    databases (`<name>.<table>`), and the temporary file sources."""
    try:
        rows = con.execute("SHOW ALL TABLES").fetchall()
        current = con.execute("SELECT current_database()").fetchone()[0]
    except Exception as exc:
        raise UnsequelError(f"DuckDB error: {exc}") from exc
    temporary_names = {row[2] for row in rows if row[5]}
    tables: dict[str, dict] = {}
    for database, schema, name, columns, column_types, temporary in rows:
        if not temporary and database == current and schema == "main" \
                and name in temporary_names:
            continue  # the temporary source shadows the persistent table
        if temporary:
            key = str(name)
        elif database == current:
            key = str(name) if schema == "main" else f"{schema}.{name}"
        else:
            key = f"{database}.{name}" if schema == "main" \
                else f"{database}.{schema}.{name}"
        tables[key] = {"columns": dict(zip(columns, column_types))}
    return Schema.from_dict({"tables": tables})


def fetch(con, sql: str) -> Table:
    """Execute one statement on an existing connection."""
    try:
        cursor = con.execute(sql)
        columns = [description[0] for description in cursor.description]
        rows = [dict(zip(columns, record)) for record in cursor.fetchall()]
        return Table("result", columns, rows)
    except Exception as exc:
        raise UnsequelError(f"DuckDB error: {exc}") from exc


def run_sql(sql: str, data: dict[str, str], *, delimiter: str | None = None,
            column_types: dict[str, dict[str, str]] | None = None,
            database_path: str | None = None,
            attach: dict[str, str] | None = None) -> Table:
    """Execute one SQL statement and return the result as an in-memory Table."""
    con = connect(data, delimiter=delimiter, column_types=column_types,
                  database_path=database_path, attach=attach)
    return fetch(con, sql)


def copy_to_file(sql: str, path: str, data: dict[str, str], *,
                 delimiter: str | None = None,
                 column_types: dict[str, dict[str, str]] | None = None,
                 database_path: str | None = None,
                 attach: dict[str, str] | None = None) -> None:
    """Write a query's results to a file via DuckDB COPY (the --out sink)."""
    con = connect(data, delimiter=delimiter, column_types=column_types,
                  database_path=database_path, attach=attach)
    inner = sql.strip().rstrip(";")
    lowered = path.lower()
    if lowered.endswith((".parquet", ".pq")):
        options = "FORMAT PARQUET"
    elif lowered.endswith(".json"):
        options = "FORMAT JSON"
    elif lowered.endswith(".csv"):
        options = "FORMAT CSV, HEADER"
    else:
        raise UnsequelError("--out must end in .csv, .parquet, or .json")
    escaped = path.replace("'", "''")
    try:
        con.execute(f"COPY (\n{inner}\n) TO '{escaped}' ({options})")
    except Exception as exc:
        raise UnsequelError(f"could not write {path}: {exc}") from exc
