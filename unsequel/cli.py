from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from . import __version__
from .errors import UnsequelError
from .engine import execute
from .io import load_sqlite_tables, load_table
from .parser import parse_query
from .pipeline import lower_to_query, parse_pipeline, parse_pipeline_stages
from .schema import Schema
from .semantics import analyze


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
    parser.add_argument("--version", action="version", version=f"unsequel {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="parse and execute a query")
    run.add_argument("query", help="query file, or '-' to read from stdin")
    run.add_argument("--pipeline", action="store_true",
                     help="treat the query as pipeline syntax instead of ordered syntax")
    run.add_argument("--schema", metavar="PATH",
                     help="JSON schema file; validate the pipeline against it before running")
    run.add_argument("--data", action="append", default=[], type=_data_spec,
                     metavar="NAME=PATH", help="CSV/JSON table input; repeatable")
    run.add_argument("--sqlite", metavar="PATH",
                     help="SQLite database input; all tables and views become sources")
    run.add_argument("--format", choices=("table", "csv", "json"), default="table")

    check = commands.add_parser("check", help="parse (and optionally schema-check) a query")
    check.add_argument("query", help="query file, or '-' to read from stdin")
    check.add_argument("--pipeline", action="store_true",
                       help="treat the query as pipeline syntax instead of ordered syntax")
    check.add_argument("--schema", metavar="PATH",
                       help="JSON schema file; validate the pipeline against it")
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        query_text = _read_query(args.query)
        schema_path = getattr(args, "schema", None)
        if args.pipeline:
            stages = parse_pipeline_stages(query_text)
            if schema_path:
                analyze(stages, Schema.load(schema_path))
            query = lower_to_query(stages)
        else:
            if schema_path:
                parser.error("--schema is only supported with --pipeline")
            query = parse_query(query_text)
        if args.command == "check":
            checked = " (schema OK)" if args.pipeline and schema_path else ""
            print(f"valid UNSeQueL query{checked}")
            return
        if bool(args.data) == bool(args.sqlite):
            parser.error("run requires exactly one of --data or --sqlite")
        tables = (load_sqlite_tables(args.sqlite) if args.sqlite
                  else {name: load_table(name, path) for name, path in args.data})
        _print_result(execute(query, tables), args.format)
    except (OSError, UnsequelError, ValueError) as exc:
        parser.exit(1, f"error: {exc}\n")
