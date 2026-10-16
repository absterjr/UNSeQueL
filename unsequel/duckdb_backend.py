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

import csv
import io as _iostd
import re
from pathlib import Path

from .codegen import _ident
from .errors import UnsequelError
from .io import _normalise_column_types
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


_HEADER_BYTES = 1 << 16

_DUCKDB_TYPE_ALIASES = {
    "text": "VARCHAR", "integer": "BIGINT", "number": "DOUBLE",
    "boolean": "BOOLEAN", "date": "DATE", "time": "TIME", "timestamp": "TIMESTAMP",
}

# The only types the CSV sniffer may pick; TypeScript's inference uses the same set.
_CSV_TYPE_CANDIDATES = (
    "auto_type_candidates=['BIGINT', 'DOUBLE', 'DATE', 'TIMESTAMP', 'VARCHAR']")


def _csv_header(path: str, delimiter: str) -> list[str]:
    """The CSV header, validated: present, with no empty or duplicate names."""
    with open(path, "rb") as handle:
        sample = handle.read(_HEADER_BYTES)
    reader = csv.reader(_iostd.StringIO(sample.decode("utf-8-sig"), newline=""),
                        delimiter=delimiter)
    header = next(reader, None)
    if not header:
        raise UnsequelError(f"CSV file has no header: {path}")
    seen: set[str] = set()
    for column in header:
        if column.strip() == "":
            raise UnsequelError(f"CSV file has an empty column name: {path}")
        lowered = column.casefold()
        if lowered in seen:
            raise UnsequelError(f"CSV file has duplicate column {column!r}: {path}")
        seen.add(lowered)
    return header


def _infer_csv_types(con, path: str, delimiter: str,
                     column_types: dict[str, str] | None) -> dict[str, str]:
    """Whole-file CSV typing by DuckDB's own sniffer (`sample_size=-1`), like
    TypeScript: a late zero-padded ZIP or a late 'N/A' decides the column's
    type. Dates must be ISO; booleans, times and offset timestamps stay text.
    One correction: DuckDB types integers too wide for BIGINT as DOUBLE, which
    drops digits, so those columns stay text. Nothing is read into Python."""
    header = _csv_header(path, delimiter)
    known = {column.casefold() for column in header}
    for name in column_types or {}:
        if name.casefold() not in known:
            raise UnsequelError(
                f"column type override refers to unknown column {name!r} in {path}")

    def read_csv(options: str) -> str:
        return (f"read_csv_auto({_sql_string(path)}, "
                f"delim={_sql_string(delimiter)}, header=true, {options})")

    options = ("sample_size=-1, " + _CSV_TYPE_CANDIDATES
               + ", dateformat='%Y-%m-%d'")
    types: dict[str, str] = {}
    try:
        described = con.execute(
            f"DESCRIBE SELECT * FROM {read_csv(options)}").fetchall()
        for row in described:
            types[str(row[0])] = str(row[1])
        doubles = [column for column, type_name in types.items() if type_name == "DOUBLE"]
        if doubles:
            checks = []
            for index, column in enumerate(doubles):
                unpad = f"ltrim(trim({_ident(column)}), '+-')"
                checks.append(
                    f"bool_or(CASE WHEN length({unpad}) >= 16 "
                    f"THEN regexp_full_match({unpad}, '\\d+') ELSE false END) AS w{index}")
            wide = con.execute(
                f"SELECT {', '.join(checks)} FROM {read_csv('all_varchar=true')}"
            ).fetchone()
            for index, column in enumerate(doubles):
                if wide is not None and wide[index] is True:
                    types[column] = "VARCHAR"
    except UnsequelError:
        raise
    except Exception as exc:
        raise UnsequelError(f"could not read {path}: {exc}") from exc
    overrides = _normalise_column_types(column_types or {})
    for column in list(types):
        override = overrides.get(column.casefold())
        if override is not None:
            types[column] = _DUCKDB_TYPE_ALIASES.get(override, override.upper())
    return types


def _reader(con, path: str, *, delimiter: str | None = None,
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

    types = _infer_csv_types(con, path, delimiter or ",", column_types)
    types_sql = ", ".join(f"{_sql_string(column)}: {_sql_string(type_name)}"
                           for column, type_name in types.items())
    delimiter_sql = _sql_string(delimiter or ",")
    return (f"read_csv_auto({_sql_string(path)}, delim={delimiter_sql}, header=true, "
            f"types={{{types_sql}}})")


def connect(data: dict[str, str], *, delimiter: str | None = None,
            column_types: dict[str, dict[str, str]] | None = None,
            database_path: str | None = None,
            attach: dict[str, str] | None = None,
            writable: bool = False, create_database: bool = False):
    if database_path is not None and not Path(database_path).exists():
        if not writable:
            raise UnsequelError(f"database not found: {database_path}")
        if not create_database:
            raise UnsequelError(f"database not found: {database_path}; "
                                "pass --create-db to create a new database file")
    duckdb = _import_duckdb()
    try:
        con = (duckdb.connect(database_path, read_only=not writable)
               if database_path is not None else duckdb.connect())
    except Exception as exc:
        raise UnsequelError(_open_error(database_path, exc)) from exc
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
                f"{_reader(con, str(path), delimiter=delimiter, column_types=table_types)}")
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
            f"{_reader(con, str(path), delimiter=delimiter, column_types=column_types)}")
        return [(str(row[0]), str(row[1])) for row in cursor.fetchall()]
    except Exception as exc:
        raise UnsequelError(f"could not inspect data file {path}: {exc}") from exc


def describe_database(con, *, persistent_only: bool = False) -> Schema:
    """A Schema for everything the connection sees: database tables, attached
    databases (`<name>.<table>`), and the temporary file sources. With
    ``persistent_only`` the temporary sources are dropped (schema snapshots
    must not leak this run's `# source` tables)."""
    try:
        rows = con.execute("SHOW ALL TABLES").fetchall()
        current = con.execute("SELECT current_database()").fetchone()[0]
    except Exception as exc:
        raise UnsequelError(f"DuckDB error: {exc}") from exc
    if persistent_only:
        rows = [row for row in rows if not row[5]]
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


def _open_error(path: str | None, exc: Exception) -> str:
    """A readable message for a database that would not open, naming the lock holder."""
    message = str(exc)
    if path is not None and "Could not set lock" in message:
        pid = re.search(r"\(PID (\d+)\)", message)
        holder = f" (PID {pid.group(1)})" if pid else ""
        return f"database {path} is in use by another process{holder}; try again when it finishes"
    return f"DuckDB error: {message}"


def _already_exists(exc: Exception) -> tuple[str, str] | None:
    """(kind, name) from DuckDB's "Table/View with name X already exists" error."""
    match = re.search(r'(Table|View) with name "?([^"\s!]+)"? already exists', str(exc), re.I)
    return (match.group(1).lower(), match.group(2)) if match else None


def import_table(con, name: str, path: str, *, delimiter: str | None = None,
                 column_types: dict[str, str] | None = None, replace: bool = False) -> None:
    """Materialize a file as a persistent table (the `import` command)."""
    if not Path(path).exists():
        raise UnsequelError(f"data file not found: {path}")
    verb = "CREATE OR REPLACE TABLE" if replace else "CREATE TABLE"
    try:
        con.execute(
            f"{verb} {_ident(name)} AS SELECT * FROM "
            f"{_reader(con, str(path), delimiter=delimiter, column_types=column_types)}")
    except Exception as exc:
        existing = _already_exists(exc)
        if existing:
            raise UnsequelError(
                f"table '{existing[1]}' already exists; pass --replace to overwrite it") from exc
        raise UnsequelError(f"could not import {path}: {exc}") from exc


def run_script(con, statements: "list[tuple[str, str]]") -> None:
    """Execute the labeled statements of a create/into program as one transaction.

    Statements run one at a time so a failure can name the stage that produced
    the SQL instead of pointing at an anonymous generated script.
    """
    try:
        # All statements commit together or not at all (DuckDB DDL is transactional).
        con.execute("BEGIN TRANSACTION")
        for label, statement in statements:
            try:
                con.execute(statement)
            except Exception as exc:
                existing = _already_exists(exc)
                if existing:
                    kind, name = existing
                    raise UnsequelError(
                        f"{kind} '{name}' already exists; use "
                        f"'create or replace {kind} {name} = (...)' to overwrite it") from exc
                raise UnsequelError(f"{label}: {exc}") from exc
        con.execute("COMMIT")
    except Exception:
        try:
            con.execute("ROLLBACK")
        except Exception:
            pass  # no transaction was open
        raise


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
