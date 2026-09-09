"""Schema files for pre-execution validation.

A schema is a hand-written JSON file describing the tables a query may read:

    {
      "tables": {
        "orders": {
          "columns": {
            "order_id": "integer",
            "customer": "string",
            "unit_price": "number"
          }
        }
      }
    }

Column types are advisory strings. The analyzer understands the families
``integer`` / ``number`` / ``float`` / ``decimal`` (numeric), ``string`` /
``text`` / ``varchar`` (text), ``bool`` / ``boolean``, ``date`` / ``time`` /
``timestamp`` / ``datetime``. Anything else is treated as ``unknown`` and never
triggers a type error.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .errors import UnsequelError

_NUMERIC = {"integer", "int", "bigint", "number", "numeric", "float", "double", "real", "decimal"}
_TEXT = {"string", "text", "varchar", "char"}
_BOOL = {"bool", "boolean"}
_TEMPORAL = {"date", "time", "timestamp", "datetime"}


def type_family(name: str | None) -> str:
    if name is None:
        return "unknown"
    key = name.strip().lower()
    if key in _NUMERIC:
        return "numeric"
    if key in _TEXT:
        return "text"
    if key in _BOOL:
        return "boolean"
    if key in _TEMPORAL:
        return "temporal"
    return "unknown"


class SchemaError(UnsequelError):
    """The schema file itself is malformed."""


@dataclass(frozen=True)
class Table:
    name: str
    columns: dict[str, str]  # column -> declared type string

    def family(self, column: str) -> str:
        return type_family(self.columns.get(column))


@dataclass(frozen=True)
class Schema:
    tables: dict[str, Table]

    def get(self, name: str) -> Table | None:
        return self.tables.get(name)

    @staticmethod
    def from_dict(data: object, *, origin: str = "<dict>") -> "Schema":
        if not isinstance(data, dict) or "tables" not in data:
            raise SchemaError(f"{origin}: schema must be an object with a 'tables' key")
        raw_tables = data["tables"]
        if not isinstance(raw_tables, dict):
            raise SchemaError(f"{origin}: 'tables' must be an object")
        tables: dict[str, Table] = {}
        for table_name, body in raw_tables.items():
            if not isinstance(body, dict) or not isinstance(body.get("columns"), dict):
                raise SchemaError(f"{origin}: table '{table_name}' needs a 'columns' object")
            columns = {}
            for column_name, column_type in body["columns"].items():
                if not isinstance(column_type, str):
                    raise SchemaError(
                        f"{origin}: {table_name}.{column_name} type must be a string")
                columns[str(column_name)] = column_type
            tables[str(table_name)] = Table(str(table_name), columns)
        return Schema(tables)

    @staticmethod
    def load(path: str | Path) -> "Schema":
        path = Path(path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise SchemaError(f"schema file not found: {path}") from exc
        except json.JSONDecodeError as exc:
            raise SchemaError(f"{path}: invalid JSON ({exc})") from exc
        return Schema.from_dict(data, origin=str(path))
