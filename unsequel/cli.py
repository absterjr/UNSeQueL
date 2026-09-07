from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from .errors import UnsequelError
from .engine import execute
from .io import load_table
from .parser import parse_query


def _data_spec(value: str) -> tuple[str, str]:
    if "=" in value:
        name, path = value.split("=", 1)
        if not name or not path:
            raise argparse.ArgumentTypeError("data must look like name=path")
        return name, path
    path = Path(value)
    return path.stem, value


def _table_output(rows: list[dict], columns: list[str]) -> str:
    if not columns:
        return "(0 columns)"
    widths = {column: max(len(column), *(len(str(row.get(column, ""))) for row in rows)) for column in columns}
    lines = ["  ".join(column.ljust(widths[column]) for column in columns)]
    lines.append("  ".join("-" * widths[column] for column in columns))
    lines.extend("  ".join(str(row.get(column, "")).ljust(widths[column]) for column in columns) for row in rows)
    return "\n".join(lines)


def _print_result(result, output_format: str) -> None:
    if output_format == "json":
        print(json.dumps(result.rows, indent=2, default=str))
    elif output_format == "csv":
        writer = csv.DictWriter(sys.stdout, fieldnames=result.columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(result.rows)
    else:
        print(_table_output(result.rows, result.columns))
        print(f"\n{len(result.rows)} row(s)")


def _read_query(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    return Path(path).read_text(encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="unsequel",
        description="Run queries written in logical execution order.",
    )
    parser.add_argument("--version", action="version", version="unsequel 0.1.0")
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="parse and execute a query")
    run.add_argument("query", help="query file, or '-' to read from stdin")
    run.add_argument("--data", action="append", required=True, type=_data_spec,
                     metavar="NAME=PATH", help="CSV/JSON table input; repeatable")
    run.add_argument("--format", choices=("table", "csv", "json"), default="table")

    check = commands.add_parser("check", help="parse a query without executing it")
    check.add_argument("query", help="query file, or '-' to read from stdin")
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        query_text = _read_query(args.query)
        query = parse_query(query_text)
        if args.command == "check":
            print("valid UNSeQueL query")
            return
        tables = {name: load_table(name, path) for name, path in args.data}
        _print_result(execute(query, tables), args.format)
    except (OSError, UnsequelError, ValueError) as exc:
        parser.exit(1, f"error: {exc}\n")
