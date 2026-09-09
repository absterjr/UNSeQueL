"""Run generated SQL against a throwaway in-memory DuckDB.

This is optional: `duckdb` is an extra (`pip install unsequel[duckdb]`). The
core language and the in-memory engine never import it.
"""

from __future__ import annotations

from pathlib import Path

from .codegen import _ident
from .errors import UnsequelError
from .model import Table


class DuckDBUnavailable(UnsequelError):
    """The duckdb package is not installed."""


def _reader(path: str) -> str:
    lowered = path.lower()
    if lowered.endswith(".json"):
        return f"read_json_auto('{path}')"
    if lowered.endswith((".parquet", ".pq")):
        return f"read_parquet('{path}')"
    return f"read_csv_auto('{path}')"


def connect(data: dict[str, str]):
    try:
        import duckdb
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise DuckDBUnavailable(
            "duckdb is not installed; run `pip install \"unsequel[duckdb]\"`"
        ) from exc
    con = duckdb.connect()
    for name, path in data.items():
        if not Path(path).exists():
            raise UnsequelError(f"data file not found: {path}")
        con.execute(f"CREATE TABLE {_ident(name)} AS SELECT * FROM {_reader(str(path))}")
    return con


def run_sql(sql: str, data: dict[str, str]) -> Table:
    """Execute one SQL statement and return the result as an in-memory Table."""
    cursor = connect(data).execute(sql)
    columns = [description[0] for description in cursor.description]
    rows = [dict(zip(columns, record)) for record in cursor.fetchall()]
    return Table("result", columns, rows)
