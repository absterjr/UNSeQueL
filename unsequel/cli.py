from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

from . import __version__
from .analyzer import analyze as analyze_unsequel_program
from .codegen import emit_sql
from .duckdb_backend import copy_to_file, describe_source, run_sql
from .engine import execute
from .errors import ParseError, UnsequelError
from .format import format_pipeline, is_formatted
from .grammar import parse_program
from .io import load_sqlite_tables, load_table
from .memory_engine import execute_program
from .parser import parse_query
from .pipeline import execute_pipeline_stages, parse_pipeline_stages
from .schema import Schema
from .semantics import analyze
from .sqlgen import emit_sql as emit_unsequel_sql


def _data_spec(value: str) -> tuple[str, str]:
    if "=" in value:
        name, path = value.split("=", 1)
        if not name or not path:
            raise argparse.ArgumentTypeError("data must look like name=path")
        return name, path
    path = Path(value)
    return path.stem, value


def _column_type_spec(value: str) -> tuple[str, str, str]:
    try:
        target, type_name = value.rsplit("=", 1)
        table, column = target.split(".", 1)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "column type must look like table.column=type") from exc
    if not table or not column or not type_name:
        raise argparse.ArgumentTypeError("column type must look like table.column=type")
    if column.startswith('"') and column.endswith('"'):
        column = column[1:-1].replace('""', '"')
    return table, column, type_name


def _delimiter_arg(value: str) -> str:
    if len(value) != 1 or value in {'"', "\r", "\n"}:
        raise argparse.ArgumentTypeError("delimiter must be exactly one non-quote character")
    return value


def _column_types_for(args, table_name: str) -> dict[str, str]:
    return {column: type_name for table, column, type_name in args.column_type
            if table.casefold() == table_name.casefold()}


def _table_output(rows: list[dict], columns: list[str]) -> str:
    if not columns:
        return "(0 columns)"
    def display(value):
        return "NULL" if value is None else str(value)

    widths = {
        column: max([len(column), *(len(display(row.get(column, ""))) for row in rows)])
        for column in columns
    }
    lines = ["  ".join(column.ljust(widths[column]) for column in columns)]
    lines.append("  ".join("-" * widths[column] for column in columns))
    lines.extend(
        "  ".join(display(row.get(column, "")).ljust(widths[column]) for column in columns)
        for row in rows)
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


_UNSEQUEL_SOURCE_DIRECTIVE = re.compile(
    r"^#\s*source\s+([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.+)$")
_UNSEQUEL_SCHEMA_DIRECTIVE = re.compile(r"^#\s*schema\s*:\s*(.+)$")

_DUCKDB_TYPE_FAMILIES = {
    "BOOLEAN": "boolean",
    "TINYINT": "integer", "SMALLINT": "integer", "INTEGER": "integer",
    "BIGINT": "integer", "HUGEINT": "integer", "UTINYINT": "integer",
    "USMALLINT": "integer", "UINTEGER": "integer", "UBIGINT": "integer",
    "FLOAT": "number", "REAL": "number", "DOUBLE": "number", "DECIMAL": "number",
    "VARCHAR": "text", "CHAR": "text", "TEXT": "text",
    "DATE": "date", "TIME": "time", "TIMESTAMP": "timestamp",
}


def _resolve_relative(base: Path, raw: str) -> str:
    path = Path(raw)
    return str(path if path.is_absolute() else base / path)


def _unsequel_directives(text: str, query_path: str) -> tuple[dict[str, str], str | None]:
    """Leading `# source name: path` / `# schema: path` directives.

    Matches the compiler: directives must be the file's first lines, and an
    ordinary comment (or any other line) ends the directive block. Paths are
    resolved relative to the query file.
    """
    base = Path(query_path).resolve().parent if query_path != "-" else Path.cwd()
    sources: dict[str, str] = {}
    schema_path: str | None = None
    for line in text.replace("\r\n", "\n").split("\n"):
        trimmed = line.strip()
        if not trimmed.startswith("#"):
            break
        match = _UNSEQUEL_SOURCE_DIRECTIVE.match(trimmed)
        if match:
            sources[match.group(1)] = _resolve_relative(base, match.group(2).strip())
            continue
        match = _UNSEQUEL_SCHEMA_DIRECTIVE.match(trimmed)
        if match:
            schema_path = _resolve_relative(base, match.group(1).strip())
            continue
        break
    return sources, schema_path


def _family_from_duckdb_type(type_name: str) -> str:
    base = type_name.upper().split("(")[0].strip()
    return _DUCKDB_TYPE_FAMILIES.get(base, "unknown")


def _unsequel_schema(args, data: dict[str, str], schema_path: str | None) -> Schema:
    if schema_path:
        return Schema.load(schema_path)
    if not data:
        raise UnsequelError(
            ".unsequel files need a schema: pass --schema, add a '# schema: path' "
            "directive, or supply --data sources to introspect")
    delimiter = getattr(args, "delimiter", None)
    column_type_args = getattr(args, "column_type", [])
    tables = {}
    for name, path in data.items():
        overrides = {column: type_name for table, column, type_name in column_type_args
                     if table.casefold() == name.casefold()}
        described = describe_source(path, delimiter=delimiter, column_types=overrides)
        tables[name] = {"columns": {column: _family_from_duckdb_type(type_name)
                                    for column, type_name in described}}
    return Schema.from_dict({"tables": tables})


def _schema_from_tables(tables) -> Schema:
    """Introspect loaded tables into a schema for memory-engine runs."""
    declared = {}
    for name, table in tables.items():
        columns = {}
        for column in table.columns:
            sample = next(
                (row.get(column) for row in table.rows if row.get(column) is not None),
                None)
            if sample is None:
                columns[column] = "unknown"
            elif isinstance(sample, bool):
                columns[column] = "boolean"
            elif isinstance(sample, int):
                columns[column] = "integer"
            elif isinstance(sample, float):
                columns[column] = "number"
            elif isinstance(sample, datetime):
                columns[column] = "timestamp"
            elif isinstance(sample, date):
                columns[column] = "date"
            else:
                columns[column] = "text"
        declared[name] = {"columns": columns}
    return Schema.from_dict({"tables": declared})


def _wants_unsequel(args) -> bool:
    if getattr(args, "unsequel", False):
        return True
    return args.query != "-" and args.query.lower().endswith(".unsequel")


def _legacy_notice() -> None:
    print("note: .usql and .pusql are legacy surfaces being retired; "
          ".unsequel is the language (see README)", file=sys.stderr)


def _unsequel_inputs(args) -> tuple[str, dict[str, str], Schema]:
    text = _read_query(args.query)
    data, directive_schema = _unsequel_directives(text, args.query)
    for name, path in getattr(args, "data", []):
        data[name] = path
    schema = _unsequel_schema(args, data, args.schema or directive_schema)
    return text, data, schema


def _load_tables(args):
    if bool(args.data) == bool(args.sqlite):
        raise UnsequelError("this command needs exactly one of --data or --sqlite")
    if args.sqlite:
        if args.delimiter is not None or args.column_type:
            raise UnsequelError("--delimiter and --column-type apply only to --data CSV/JSON inputs")
        return load_sqlite_tables(args.sqlite)
    supplied_names = {name.casefold() for name, _path in args.data}
    unknown = sorted({table for table, _column, _type in args.column_type
                      if table.casefold() not in supplied_names})
    if unknown:
        raise UnsequelError(f"--column-type refers to table {unknown[0]!r}, not supplied by --data")
    return {
        name: load_table(name, path, delimiter=args.delimiter,
                         column_types=_column_types_for(args, name))
        for name, path in args.data
    }


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
        sub.add_argument("--unsequel", action="store_true",
                         help="treat the input (including stdin '-') as canonical "
                              ".unsequel syntax")
        sub.add_argument("--schema", metavar="PATH",
                         help="JSON schema file; validate the pipeline against it")

    run = commands.add_parser("run", help="parse and execute a query")
    add_pipeline_flags(run)
    run.add_argument("--engine", choices=("memory", "duckdb"), default=None,
                     help="memory: built-in engine (default; .unsequel runs without "
                          "DuckDB); duckdb: execute the generated SQL")
    run.add_argument("--data", action="append", default=[], type=_data_spec,
                     metavar="NAME=PATH", help="CSV/JSON table input; repeatable")
    run.add_argument("--delimiter", type=_delimiter_arg,
                     help="one-character CSV delimiter (default: comma)")
    run.add_argument("--column-type", action="append", default=[], type=_column_type_spec,
                     metavar="TABLE.COLUMN=TYPE",
                     help="override inferred type: text, integer, number, or boolean; repeatable")
    run.add_argument("--sqlite", metavar="PATH",
                     help="SQLite database input; all tables and views become sources")
    run.add_argument("--out", metavar="PATH",
                     help="write results to a .csv, .parquet, or .json file instead "
                          "of printing them")
    run.add_argument("--format", choices=("table", "csv", "json"), default="table")

    check = commands.add_parser("check", help="parse (and optionally schema-check) a query")
    add_pipeline_flags(check)

    compile_ = commands.add_parser("compile", help="print the SQL a query compiles to")
    compile_.add_argument("query", help="query file, or '-' for stdin")
    compile_.add_argument("--unsequel", action="store_true",
                          help="treat the input (including stdin '-') as canonical "
                               ".unsequel syntax")
    compile_.add_argument("--schema", metavar="PATH",
                          help="JSON schema file (required for .pusql; .unsequel files "
                               "may use a '# schema' directive or introspect --data)")
    compile_.add_argument("--stage", type=int, metavar="N",
                          help="truncate the query after stage N")

    preview = commands.add_parser("preview", help="run a query truncated at a stage")
    preview.add_argument("query", help="query file, or '-' for stdin")
    preview.add_argument("--unsequel", action="store_true",
                         help="treat the input (including stdin '-') as canonical "
                              ".unsequel syntax")
    preview.add_argument("--schema", metavar="PATH",
                         help="JSON schema file (required for .pusql)")
    preview.add_argument("--stage", type=int, metavar="N", required=True,
                         help="stage to stop at (1-based)")
    preview.add_argument("--limit", type=int, default=10, metavar="K",
                         help="sample rows to show (default 10)")
    preview.add_argument("--data", action="append", default=[], type=_data_spec,
                         metavar="NAME=PATH", help="CSV/JSON table input; repeatable")
    preview.add_argument("--delimiter", type=_delimiter_arg,
                         help="one-character CSV delimiter (default: comma)")
    preview.add_argument("--column-type", action="append", default=[], type=_column_type_spec,
                         metavar="TABLE.COLUMN=TYPE",
                         help="override inferred type: text, integer, number, or boolean; repeatable")
    preview.add_argument("--format", choices=("table", "csv", "json"), default="table")

    fmt = commands.add_parser("fmt", help="print a pipeline query in canonical form")
    fmt.add_argument("query", help="pipeline query file, or '-' to read from stdin")
    fmt.add_argument("--write", action="store_true", help="rewrite the file in place")
    fmt.add_argument("--check", action="store_true",
                     help="exit 1 if the file is not already in canonical form")
    return parser


def _pipeline_stages(text: str, schema_path: str | None):
    stages = parse_pipeline_stages(text)
    if schema_path:
        analyze(stages, Schema.load(schema_path))
    return stages


def _wants_pipeline(args) -> bool:
    """--pipeline, or a .pusql file (the compile/preview commands are pipeline-only)."""
    if args.pipeline:
        return True
    return args.query != "-" and args.query.lower().endswith(".pusql")


def _parse_ordered_with_hint(text: str):
    try:
        return parse_query(text)
    except ParseError as exc:
        pipeline_stages = {"derive", "group", "aggregate", "filter", "sort",
                           "take", "skip", "distinct", "window"}
        looks_like_pipeline = any(
            (match := re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\b", part))
            and match.group(1).lower() in pipeline_stages
            for line in text.splitlines()
            for part in line.split("|")
            if part.strip() and not part.lstrip().startswith(("#", "--"))
        )
        if not looks_like_pipeline:
            raise
        raise ParseError(
            f"{exc}\n  hint: pipeline queries need --pipeline (or use a .pusql file)"
        ) from None


def _cmd_check(args) -> None:
    text = _read_query(args.query)
    if _wants_unsequel(args):
        _, data, schema = _unsequel_inputs(args)
        program = parse_program(text)
        analyze_unsequel_program(program, schema)
        print("valid UNSeQueL program (schema OK)")
    elif _wants_pipeline(args):
        _legacy_notice()
        _pipeline_stages(text, args.schema)
        suffix = " (schema OK)" if args.schema else ""
        print(f"valid UNSeQueL query{suffix}")
    else:
        _legacy_notice()
        if args.schema:
            raise UnsequelError("--schema is only supported with --pipeline")
        _parse_ordered_with_hint(text)
        print("valid UNSeQueL query")


def _cmd_compile(args) -> None:
    if _wants_unsequel(args):
        text, _, schema = _unsequel_inputs(args)
        program = parse_program(text)
        print(emit_unsequel_sql(program, schema, stop_at=args.stage))
        return
    _legacy_notice()
    if not args.schema:
        raise UnsequelError("--schema is required to compile a .pusql query")
    stages = _pipeline_stages(_read_query(args.query), args.schema)
    print(emit_sql(stages, Schema.load(args.schema), stop_at=args.stage))


def _cmd_preview(args) -> None:
    if _wants_unsequel(args):
        text, data, schema = _unsequel_inputs(args)
        program = parse_program(text)
        total = len(program)
        if not 1 <= args.stage <= total:
            raise UnsequelError(f"--stage must be between 1 and {total} for this query")
        inner = emit_unsequel_sql(program, schema, stop_at=args.stage).rstrip().rstrip(";")
        sql = f"SELECT * FROM (\n{inner}\n) AS _preview LIMIT {args.limit}"
        column_types = {name: _column_types_for(args, name) for name in data}
        result = run_sql(sql, data, delimiter=args.delimiter, column_types=column_types)
        print(f"stage {args.stage}/{total}  ({program[args.stage - 1].keyword})")
        _print_result(result, args.format)
        return
    _legacy_notice()
    if not args.schema:
        raise UnsequelError("--schema is required to preview a .pusql query")
    stages = _pipeline_stages(_read_query(args.query), args.schema)
    total = len(stages)
    if not 1 <= args.stage <= total:
        raise UnsequelError(f"--stage must be between 1 and {total} for this query")
    inner = emit_sql(stages, Schema.load(args.schema), stop_at=args.stage).rstrip().rstrip(";")
    sql = f"SELECT * FROM (\n{inner}\n) AS _preview LIMIT {args.limit}"
    data = {name: path for name, path in args.data}
    column_types = {name: _column_types_for(args, name) for name in data}
    result = run_sql(sql, data, delimiter=args.delimiter, column_types=column_types)
    print(f"stage {args.stage}/{total}  ({stages[args.stage - 1].keyword})")
    _print_result(result, args.format)


def _cmd_fmt(args) -> None:
    text = _read_query(args.query)
    formatted = format_pipeline(text)
    if args.check:
        if is_formatted(text):
            return
        raise SystemExit(1)
    if args.write:
        if args.query == "-":
            raise UnsequelError("fmt --write cannot rewrite stdin")
        Path(args.query).write_text(formatted, encoding="utf-8")
        return
    print(formatted, end="")


def _emit_result(args, result, out_path, *, sql, data) -> bool:
    """Handle --out; returns True when the results were written to a file."""
    if out_path is None:
        return False
    target = Path(out_path)
    suffix = target.suffix.lower()
    if suffix in {".parquet", ".pq"}:
        if sql is None:
            raise UnsequelError(
                "writing .parquet needs the DuckDB engine; add --engine duckdb "
                "or write .csv/.json")
        copy_to_file(sql, out_path, data, delimiter=args.delimiter,
                     column_types={name: _column_types_for(args, name) for name in data})
        print(f"wrote {out_path}")
        return True
    if suffix not in {".csv", ".json"}:
        raise UnsequelError("--out must end in .csv, .parquet, or .json")
    with target.open("w", encoding="utf-8", newline="") as handle:
        if suffix == ".csv":
            writer = csv.DictWriter(handle, fieldnames=result.columns,
                                    lineterminator="\n")
            writer.writeheader()
            writer.writerows(result.rows)
        else:
            for row in result.rows:
                handle.write(json.dumps(row, default=str) + "\n")
    print(f"wrote {out_path}")
    return True


def _cmd_run(args) -> None:
    if _wants_unsequel(args):
        if args.sqlite:
            raise UnsequelError("--sqlite is not supported for .unsequel files")
        text = _read_query(args.query)
        data, directive_schema = _unsequel_directives(text, args.query)
        for name, path in args.data:
            data[name] = path
        program = parse_program(text)
        sql = None
        if args.engine == "duckdb":
            schema = _unsequel_schema(args, data, args.schema or directive_schema)
            analyze_unsequel_program(program, schema)
            sql = emit_unsequel_sql(program, schema)
            column_types = {name: _column_types_for(args, name) for name in data}
            result = run_sql(sql, data, delimiter=args.delimiter,
                             column_types=column_types)
        else:
            tables = {
                name: load_table(name, path, delimiter=args.delimiter,
                                 column_types=_column_types_for(args, name))
                for name, path in data.items()
            }
            schema_path = args.schema or directive_schema
            if schema_path:
                schema = Schema.load(schema_path)
            elif tables:
                schema = _schema_from_tables(tables)
            else:
                raise UnsequelError(
                    ".unsequel files need a schema: pass --schema, add a '# schema: path' "
                    "directive, or supply --data sources to introspect")
            analyze_unsequel_program(program, schema)
            result = execute_program(program, tables, schema=schema)
            if args.out and Path(args.out).suffix.lower() in {".parquet", ".pq"}:
                sql = emit_unsequel_sql(program, schema)
        if not _emit_result(args, result, args.out, sql=sql, data=data):
            _print_result(result, args.format)
        return
    _legacy_notice()
    text = _read_query(args.query)
    sql = None
    data = {name: path for name, path in args.data}
    if args.engine == "duckdb":
        if not _wants_pipeline(args):
            raise UnsequelError("--engine duckdb requires --pipeline (or a .pusql file)")
        if not args.schema:
            raise UnsequelError("--engine duckdb requires --schema")
        if args.sqlite:
            raise UnsequelError("--engine duckdb reads --data sources, not --sqlite")
        stages = _pipeline_stages(text, args.schema)
        sql = emit_sql(stages, Schema.load(args.schema))
        column_types = {name: _column_types_for(args, name) for name in data}
        result = run_sql(sql, data, delimiter=args.delimiter, column_types=column_types)
    else:
        if _wants_pipeline(args):
            stages = _pipeline_stages(text, args.schema)
            result = execute_pipeline_stages(stages, _load_tables(args))
        else:
            if args.schema:
                raise UnsequelError("--schema is only supported with --pipeline")
            result = execute(_parse_ordered_with_hint(text), _load_tables(args))
    if not _emit_result(args, result, args.out, sql=sql, data=data):
        _print_result(result, args.format)


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    handlers = {
        "run": _cmd_run,
        "check": _cmd_check,
        "compile": _cmd_compile,
        "preview": _cmd_preview,
        "fmt": _cmd_fmt,
    }
    try:
        handlers[args.command](args)
    except (OSError, UnsequelError, ValueError) as exc:
        parser.exit(1, f"error: {exc}\n")
