from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from . import __version__
from .codegen import emit_sql
from .duckdb_backend import run_sql
from .engine import execute
from .errors import UnsequelError
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


def _load_tables(args):
    if bool(args.data) == bool(args.sqlite):
        raise UnsequelError("this command needs exactly one of --data or --sqlite")
    if args.sqlite:
        return load_sqlite_tables(args.sqlite)
    return {name: load_table(name, path) for name, path in args.data}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="unsequel",
        description="Run queries written in logical execution order.",
    )
    parser.add_argument("--version", action="version", version=f"unsequel {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    def add_pipeline_flags(sub):
        sub.add_argument("query", help="query file, or '-' to read from stdin")
        sub.add_argument("--pipeline", action="store_true",
                         help="treat the query as pipeline syntax instead of ordered syntax")
        sub.add_argument("--schema", metavar="PATH",
                         help="JSON schema file; validate the pipeline against it")

    run = commands.add_parser("run", help="parse and execute a query")
    add_pipeline_flags(run)
    run.add_argument("--engine", choices=("memory", "duckdb"), default="memory",
                     help="memory: built-in engine (default); duckdb: run generated SQL")
    run.add_argument("--data", action="append", default=[], type=_data_spec,
                     metavar="NAME=PATH", help="CSV/JSON table input; repeatable")
    run.add_argument("--sqlite", metavar="PATH",
                     help="SQLite database input; all tables and views become sources")
    run.add_argument("--format", choices=("table", "csv", "json"), default="table")

    check = commands.add_parser("check", help="parse (and optionally schema-check) a query")
    add_pipeline_flags(check)

    compile_ = commands.add_parser("compile", help="print the SQL a pipeline query compiles to")
    compile_.add_argument("query", help="pipeline query file, or '-' for stdin")
    compile_.add_argument("--schema", metavar="PATH", required=True,
                          help="JSON schema file (required for codegen)")
    compile_.add_argument("--stage", type=int, metavar="N",
                          help="truncate the pipeline after stage N")

    preview = commands.add_parser("preview", help="run a pipeline truncated at a stage")
    preview.add_argument("query", help="pipeline query file, or '-' for stdin")
    preview.add_argument("--schema", metavar="PATH", required=True)
    preview.add_argument("--stage", type=int, metavar="N", required=True,
                         help="stage to stop at (1-based)")
    preview.add_argument("--limit", type=int, default=10, metavar="K",
                         help="sample rows to show (default 10)")
    preview.add_argument("--data", action="append", default=[], type=_data_spec,
                         metavar="NAME=PATH", help="CSV/JSON table input; repeatable")
    preview.add_argument("--format", choices=("table", "csv", "json"), default="table")
    return parser


def _pipeline_stages(text: str, schema_path: str | None):
    stages = parse_pipeline_stages(text)
    if schema_path:
        analyze(stages, Schema.load(schema_path))
    return stages


def _cmd_check(args) -> None:
    text = _read_query(args.query)
    if args.pipeline:
        _pipeline_stages(text, args.schema)
        suffix = " (schema OK)" if args.schema else ""
    else:
        if args.schema:
            raise UnsequelError("--schema is only supported with --pipeline")
        parse_query(text)
        suffix = ""
    print(f"valid UNSeQueL query{suffix}")


def _cmd_compile(args) -> None:
    stages = _pipeline_stages(_read_query(args.query), args.schema)
    print(emit_sql(stages, Schema.load(args.schema), stop_at=args.stage))


def _cmd_preview(args) -> None:
    stages = _pipeline_stages(_read_query(args.query), args.schema)
    total = len(stages)
    if not 1 <= args.stage <= total:
        raise UnsequelError(f"--stage must be between 1 and {total} for this query")
    inner = emit_sql(stages, Schema.load(args.schema), stop_at=args.stage).rstrip().rstrip(";")
    sql = f"SELECT * FROM (\n{inner}\n) AS _preview LIMIT {args.limit}"
    data = {name: path for name, path in args.data}
    result = run_sql(sql, data)
    print(f"stage {args.stage}/{total}  ({stages[args.stage - 1].keyword})")
    _print_result(result, args.format)


def _cmd_run(args) -> None:
    text = _read_query(args.query)
    if args.engine == "duckdb":
        if not args.pipeline:
            raise UnsequelError("--engine duckdb requires --pipeline")
        if not args.schema:
            raise UnsequelError("--engine duckdb requires --schema")
        if args.sqlite:
            raise UnsequelError("--engine duckdb reads --data sources, not --sqlite")
        stages = _pipeline_stages(text, args.schema)
        sql = emit_sql(stages, Schema.load(args.schema))
        result = run_sql(sql, {name: path for name, path in args.data})
    else:
        if args.pipeline:
            stages = _pipeline_stages(text, args.schema)
            query = lower_to_query(stages)
        else:
            if args.schema:
                raise UnsequelError("--schema is only supported with --pipeline")
            query = parse_query(text)
        result = execute(query, _load_tables(args))
    _print_result(result, args.format)


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    handlers = {
        "run": _cmd_run,
        "check": _cmd_check,
        "compile": _cmd_compile,
        "preview": _cmd_preview,
    }
    try:
        handlers[args.command](args)
    except (OSError, UnsequelError, ValueError) as exc:
        parser.exit(1, f"error: {exc}\n")
