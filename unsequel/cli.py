from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from . import __version__
from .analyzer import analyze as analyze_unsequel_program
from .codegen import emit_sql
from .duckdb_backend import (connect, copy_to_file, describe_database, fetch,
                             import_table, run_sql, run_script)
from .engine import execute
from .errors import ParseError, UnsequelError
from .format import format_pipeline, is_formatted
from .grammar import (AggregateStage, CreateStage, GroupByStage, IntoStage,
                      WithStage, parse_program)
from .io import load_sqlite_tables, load_table
from .memory_engine import execute_program
from .parser import parse_query
from .pipeline import execute_pipeline_stages, parse_pipeline_stages
from .schema import Schema
from .semantics import analyze
from .sqlgen import emit_sql as emit_unsequel_sql, emit_statement_list


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
    # compile doesn't define --column-type, so default to no overrides.
    return {column: type_name for table, column, type_name in getattr(args, "column_type", [])
            if table.casefold() == table_name.casefold()}


_MAX_SAFE_INTEGER = 2 ** 53 - 1


def _number_text(value: float) -> str:
    """How the TypeScript track renders a number (JS `String(n)`).

    JS prints plain decimal digits for 1e-6 <= |n| < 1e21 and exponent form
    outside that window; Python's `repr` switches much earlier, so both ends
    are normalized here.
    """
    magnitude = abs(value)
    if magnitude != 0 and (magnitude < 1e-6 or magnitude >= 1e21):
        text = repr(value)
        head, _, tail = text.partition("e")
        sign = ""
        if tail[:1] in {"+", "-"}:
            sign, tail = tail[0], tail[1:]
        return f"{head}e{sign}{tail.lstrip('0') or '0'}"
    if value.is_integer():
        return str(int(value))
    return format(Decimal(repr(value)), "f")


def _text(value) -> str:
    """Cell text shared with the TypeScript CLI: JS number spelling, NaN/Infinity."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        if not math.isfinite(value):
            return "NaN" if math.isnan(value) else ("Infinity" if value > 0 else "-Infinity")
        return _number_text(value)
    return str(value)


def _decimal_round_trips(text: str) -> bool:
    """Whether a DECIMAL text survives a round trip through a JS number."""
    normalized = text.rstrip("0").rstrip(".") if "." in text else text
    try:
        number = float(text)
    except (OverflowError, ValueError):
        return False
    return bool(normalized) and normalized != "-" and math.isfinite(number) \
        and _number_text(number) == normalized


def _decimal_json(value: Decimal) -> str:
    """DECIMAL is a JSON number at DuckDB's scale — unless the digits would not
    survive the round trip through a JS number, in which case it stays exact text."""
    text = str(value)
    if _decimal_round_trips(text):
        return _number_text(float(text))
    return json.dumps(text, ensure_ascii=False)


def _json_text(value, indent: int) -> str:
    """One JSON value; numbers use JS spellings so both tracks byte-match."""
    pad = " " * indent
    inner = " " * (indent + 2)
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, int):
        if abs(value) <= _MAX_SAFE_INTEGER:
            return str(value)
        return json.dumps(str(value), ensure_ascii=False)
    if isinstance(value, float):
        return "null" if not math.isfinite(value) else _number_text(value)
    if isinstance(value, Decimal):
        return _decimal_json(value)
    if isinstance(value, dict):
        if not value:
            return "{}"
        entries = ",\n".join(
            f"{inner}{json.dumps(str(key), ensure_ascii=False)}: {_json_text(item, indent + 2)}"
            for key, item in value.items())
        return f"{{\n{entries}\n{pad}}}"
    if isinstance(value, (list, tuple)):
        if not value:
            return "[]"
        items = ",\n".join(f"{inner}{_json_text(item, indent + 2)}" for item in value)
        return f"[\n{items}\n{pad}]"
    return json.dumps(str(value), ensure_ascii=False)


def _json_columns_to_stringify(rows: list[dict], columns: list[str]) -> set[str]:
    """Columns where at least one value cannot survive as a JSON number."""
    stringified: set[str] = set()
    for column in columns:
        for row in rows:
            value = row.get(column)
            if isinstance(value, bool) or value is None:
                continue
            if isinstance(value, int):
                if abs(value) > _MAX_SAFE_INTEGER:
                    stringified.add(column)
                    break
            elif isinstance(value, Decimal):
                if not _decimal_round_trips(str(value)):
                    stringified.add(column)
                    break
    return stringified


def _coerce_json_columns(rows: list[dict], columns: list[str]) -> list[dict]:
    """One JSON type per column: when any value in a column needs exact text
    (HUGEINT beyond 2^53, a wide DECIMAL), every value in that column becomes
    that exact text, so a column never mixes numbers and strings."""
    stringified = _json_columns_to_stringify(rows, columns)
    if not stringified:
        return rows
    result = []
    for row in rows:
        copy = dict(row)
        for column in stringified:
            value = copy.get(column)
            if value is None:
                continue
            if isinstance(value, Decimal) or (isinstance(value, int)
                                              and not isinstance(value, bool)):
                copy[column] = str(value)
            else:
                copy[column] = _text(value)
        result.append(copy)
    return result


def _render_json_rows(rows: list[dict], columns: list[str]) -> str:
    rows = _coerce_json_columns(rows, columns)
    if not rows:
        return "[]"
    items = ",\n".join(f"  {_json_text(dict(row), 2)}" for row in rows)
    return f"[\n{items}\n]"


def _csv_rows(rows: list[dict]) -> list[dict]:
    return [{key: "" if value is None else _text(value) for key, value in row.items()}
            for row in rows]


def _table_output(rows: list[dict], columns: list[str]) -> str:
    if not columns:
        return "(0 columns)"
    def display(value):
        return "NULL" if value is None else _text(value)

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
        print(_render_json_rows(result.rows, result.columns))
    elif output_format == "csv":
        writer = csv.DictWriter(sys.stdout, fieldnames=result.columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(_csv_rows(result.rows))
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
_UNSEQUEL_DB_DIRECTIVE = re.compile(r"^#\s*db\s*:\s*(.+)$")

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


def _unsequel_directives(text: str, query_path: str):
    """Leading `# source name: path` / `# schema: path` / `# db: path` directives.

    Matches the compiler: directives must be the file's first lines, and an
    ordinary comment (or any other line) ends the directive block. Paths are
    resolved relative to the query file.
    """
    base = Path(query_path).resolve().parent if query_path != "-" else Path.cwd()
    sources: dict[str, str] = {}
    schema_path: str | None = None
    database_path: str | None = None
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
        match = _UNSEQUEL_DB_DIRECTIVE.match(trimmed)
        if match:
            database_path = _resolve_relative(base, match.group(1).strip())
            continue
        break
    return sources, schema_path, database_path


def _family_from_duckdb_type(type_name: str) -> str:
    base = type_name.upper().split("(")[0].strip()
    return _DUCKDB_TYPE_FAMILIES.get(base, "unknown")


def _attach_specs(args) -> dict[str, str]:
    attach: dict[str, str] = {}
    for spec in getattr(args, "attach", None) or []:
        if "=" not in spec:
            raise UnsequelError("--attach must look like name=path.duckdb")
        name, path = spec.split("=", 1)
        if not name or not path:
            raise UnsequelError("--attach must look like name=path.duckdb")
        if name in attach:
            raise UnsequelError(f"duplicate --attach name {name!r}")
        attach[name] = path
    return attach


def _duckdb_available() -> bool:
    try:
        import duckdb  # noqa: F401
        return True
    except ImportError:
        return False


def _unsequel_schema_for(args, data, schema_path, database, attach) -> Schema:
    if schema_path:
        return Schema.load(schema_path)
    if database or attach or data:
        con = connect(data, delimiter=getattr(args, "delimiter", None),
                      column_types={name: _column_types_for(args, name)
                                    for name in data},
                      database_path=database, attach=attach)
        return describe_database(con)
    raise UnsequelError(
        ".unsequel files need a schema: pass --schema, add a '# schema:' or "
        "'# db:' directive, or supply --data sources to introspect")


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


def _unsequel_inputs(args):
    text = _read_query(args.query)
    data, directive_schema, directive_db = _unsequel_directives(text, args.query)
    for name, path in getattr(args, "data", []):
        data[name] = path
    database = getattr(args, "db", None) or directive_db
    attach = _attach_specs(args)
    schema = _unsequel_schema_for(args, data, args.schema or directive_schema,
                                  database, attach)
    return text, data, database, attach, schema


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
        sub.add_argument("--db", metavar="PATH",
                         help="open a file-backed DuckDB database (read-only); "
                              "'# source' files load as temporary tables")
        sub.add_argument("--attach", action="append", default=[], metavar="NAME=PATH",
                         help="ATTACH another DuckDB file for cross-database queries; "
                              "repeatable")
        sub.add_argument("--schema", metavar="PATH",
                         help="JSON schema file; validate the pipeline against it")

    run = commands.add_parser("run", help="parse and execute a query")
    add_pipeline_flags(run)
    run.add_argument("--create-db", action="store_true",
                     help="create/into: create the --db database file if it does not exist")
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
    check.add_argument("--data", action="append", default=[], type=_data_spec,
                       metavar="NAME=PATH", help="CSV/JSON table input; repeatable")
    check.add_argument("--delimiter", type=_delimiter_arg,
                       help="one-character CSV delimiter (default: comma)")
    check.add_argument("--column-type", action="append", default=[], type=_column_type_spec,
                       metavar="TABLE.COLUMN=TYPE",
                       help="override inferred type: text, integer, number, or boolean; repeatable")

    compile_ = commands.add_parser("compile", help="print the SQL a query compiles to")
    compile_.add_argument("query", help="query file, or '-' for stdin")
    compile_.add_argument("--unsequel", action="store_true",
                          help="treat the input (including stdin '-') as canonical "
                               ".unsequel syntax")
    compile_.add_argument("--db", metavar="PATH",
                          help="open a file-backed DuckDB database (read-only)")
    compile_.add_argument("--attach", action="append", default=[], metavar="NAME=PATH",
                          help="ATTACH another DuckDB file; repeatable")
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
    preview.add_argument("--db", metavar="PATH",
                         help="open a file-backed DuckDB database (read-only)")
    preview.add_argument("--attach", action="append", default=[], metavar="NAME=PATH",
                         help="ATTACH another DuckDB file; repeatable")
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

    schema_cmd = commands.add_parser(
        "schema", help="write a database's schema snapshot (<db>.schema.json) for the editor")
    schema_cmd.add_argument("--db", metavar="PATH", help="database file to describe")
    schema_cmd.add_argument("--attach", action="append", default=[], metavar="NAME=PATH",
                            help="ATTACH another DuckDB file; repeatable")
    schema_cmd.add_argument("--data", action="append", default=[], type=_data_spec,
                            metavar="NAME=PATH", help="CSV/JSON table input; repeatable")
    schema_cmd.add_argument("--out", metavar="PATH",
                            help="write here instead of <db>.schema.json ('-' prints it)")

    imp = commands.add_parser("import", help="materialize a data file as a persistent table")
    imp.add_argument("data", help="CSV/JSON/Parquet file")
    imp.add_argument("--into", metavar="PATH", required=True,
                     help="database file (created if needed)")
    imp.add_argument("--table", metavar="NAME", help="table name (default: the file stem)")
    imp.add_argument("--replace", action="store_true",
                     help="overwrite the table if it already exists")
    imp.add_argument("--delimiter", type=_delimiter_arg,
                     help="one-character CSV delimiter (default: comma)")
    imp.add_argument("--column-type", action="append", default=[], type=_column_type_spec,
                     metavar="TABLE.COLUMN=TYPE",
                     help="override inferred type; repeatable")
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
        program = parse_program(text)  # parse errors surface even without a schema
        data, directive_schema, directive_db = _unsequel_directives(text, args.query)
        for name, path in getattr(args, "data", []):
            data[name] = path
        database = getattr(args, "db", None) or directive_db
        attach = _attach_specs(args)
        schema_path = args.schema or directive_schema
        if schema_path:
            schema = Schema.load(schema_path)
        elif database or attach:
            con = connect(data, delimiter=getattr(args, "delimiter", None),
                          column_types={name: _column_types_for(args, name)
                                        for name in data},
                          database_path=database, attach=attach)
            schema = describe_database(con)
        elif data:
            tables = {
                name: load_table(name, path,
                                 delimiter=getattr(args, "delimiter", None),
                                 column_types=_column_types_for(args, name))
                for name, path in data.items()
            }
            schema = _schema_from_tables(tables)
        else:
            print("valid UNSeQueL program (syntax OK; columns not checked)")
            return
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
        text, _, database, attach, schema = _unsequel_inputs(args)
        program = parse_program(text)
        if args.stage is not None and any(
                isinstance(stage, (CreateStage, IntoStage)) for stage in program):
            raise UnsequelError("--stage does not apply to create/into programs")
        print(emit_unsequel_sql(program, schema, stop_at=args.stage))
        return
    _legacy_notice()
    if not args.schema:
        raise UnsequelError("--schema is required to compile a .pusql query")
    stages = _pipeline_stages(_read_query(args.query), args.schema)
    print(emit_sql(stages, Schema.load(args.schema), stop_at=args.stage))


def _cmd_preview(args) -> None:
    if _wants_unsequel(args):
        text, data, database, attach, schema = _unsequel_inputs(args)
        program = parse_program(text)
        if any(isinstance(stage, (CreateStage, IntoStage)) for stage in program):
            raise UnsequelError("preview does not apply to create/into programs")
        total = len(program)
        if not 1 <= args.stage <= total:
            raise UnsequelError(f"--stage must be between 1 and {total} for this query")
        inner = emit_unsequel_sql(program, schema, stop_at=args.stage).rstrip().rstrip(";")
        sql = f"SELECT * FROM (\n{inner}\n) AS _preview LIMIT {args.limit}"
        con = connect(data, delimiter=args.delimiter,
                      column_types={name: _column_types_for(args, name) for name in data},
                      database_path=database, attach=attach)
        result = fetch(con, sql)
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


def _emit_result(args, result, out_path, *, sql, data, database=None, attach=None) -> bool:
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
                     column_types={name: _column_types_for(args, name) for name in data},
                     database_path=database, attach=attach)
        print(f"wrote {out_path}")
        return True
    if suffix not in {".csv", ".json"}:
        raise UnsequelError("--out must end in .csv, .parquet, or .json")
    with target.open("w", encoding="utf-8", newline="") as handle:
        if suffix == ".csv":
            writer = csv.DictWriter(handle, fieldnames=result.columns,
                                    lineterminator="\n")
            writer.writeheader()
            writer.writerows(_csv_rows(result.rows))
        else:
            # The file gets the same formatter as stdout, so they never disagree.
            handle.write(_render_json_rows(result.rows, result.columns) + "\n")
    print(f"wrote {out_path}")
    return True


def _notify_shadowed_sources(con, data) -> None:
    """'# source' files load as temporary tables; note when one hides a database table."""
    if not data:
        return
    try:
        rows = con.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_catalog = current_database() AND table_schema = 'main' "
            "AND table_type <> 'LOCAL TEMPORARY'").fetchall()
    except Exception:
        return  # the note is advisory; never fail a run over it
    persistent = {str(row[0]).casefold() for row in rows}
    for name in data:
        if name.casefold() in persistent:
            print(f"note: source '{name}' hides database table '{name}'; "
                  "this run reads the file", file=sys.stderr)


def _cmd_run(args) -> None:
    if _wants_unsequel(args):
        if args.sqlite:
            raise UnsequelError("--sqlite is not supported for .unsequel files")
        text = _read_query(args.query)
        data, directive_schema, directive_db = _unsequel_directives(text, args.query)
        for name, path in args.data:
            data[name] = path
        database = args.db or directive_db
        attach = _attach_specs(args)
        engine = args.engine or (
            "duckdb" if (database or attach or _duckdb_available()) else "memory")
        if engine == "memory" and (database or attach):
            raise UnsequelError(
                "databases need DuckDB; drop --engine memory and install the "
                "duckdb extra (`pip install \"unsequel[duckdb]\"`) if needed")
        schema_path = args.schema or directive_schema
        program = parse_program(text)
        sql = None
        if any(isinstance(stage, (CreateStage, IntoStage)) for stage in program):
            if database is None:
                raise UnsequelError(
                    "create/into write to a database: add '# db: warehouse.duckdb' "
                    "to the file or pass --db")
            if args.out:
                raise UnsequelError("--out does not apply to create/into programs")
            con = connect(data, delimiter=args.delimiter,
                          column_types={name: _column_types_for(args, name)
                                        for name in data},
                          database_path=database, attach=attach, writable=True,
                          create_database=args.create_db)
            _notify_shadowed_sources(con, data)
            schema = Schema.load(schema_path) if schema_path else describe_database(con)
            analyze_unsequel_program(program, schema)
            _reject_views_over_temporary_sources(program, data)
            blocks = emit_statement_list(program, schema)
            run_script(con, [(f"stage {index} ({kind})", statement)
                             for index, kind, statement in blocks])
            _refresh_snapshot(database, con)
            print(f"applied {len(blocks)} statement(s)")
            return
        if engine == "duckdb":
            if not (schema_path or database or attach or data):
                raise UnsequelError(
                    ".unsequel files need a schema: pass --schema, add a "
                    "'# schema:' or '# db:' directive, or supply --data sources "
                    "to introspect")
            con = connect(data, delimiter=args.delimiter,
                          column_types={name: _column_types_for(args, name)
                                        for name in data},
                          database_path=database, attach=attach)
            _notify_shadowed_sources(con, data)
            schema = Schema.load(schema_path) if schema_path else describe_database(con)
            analyze_unsequel_program(program, schema)
            sql = emit_unsequel_sql(program, schema)
            if args.out and Path(args.out).suffix.lower() in {".parquet", ".pq"}:
                copy_to_file(sql, args.out, data, delimiter=args.delimiter,
                             column_types={name: _column_types_for(args, name)
                                           for name in data},
                             database_path=database, attach=attach)
                print(f"wrote {args.out}")
                return
            result = _fetch_with_stage(sql, con, program)
        else:
            tables = {
                name: load_table(name, path, delimiter=args.delimiter,
                                 column_types=_column_types_for(args, name))
                for name, path in data.items()
            }
            if schema_path:
                schema = Schema.load(schema_path)
            elif tables:
                schema = _schema_from_tables(tables)
            else:
                raise UnsequelError(
                    ".unsequel files need a schema: pass --schema, add a "
                    "'# schema:' or '# db:' directive, or supply --data sources "
                    "to introspect")
            analyze_unsequel_program(program, schema)
            result = execute_program(program, tables, schema=schema)
            if args.out and Path(args.out).suffix.lower() in {".parquet", ".pq"}:
                sql = emit_unsequel_sql(program, schema)
        if not _emit_result(args, result, args.out, sql=sql, data=data,
                            database=database, attach=attach):
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


def _grammar_stage_for_unit(program, unit: int) -> "int | None":
    """Grammar stage number for the U-th unit (group by + aggregate share one)."""
    seen = 0
    previous_was_group = False
    for index, stage in enumerate(program):
        if isinstance(stage, WithStage):
            previous_was_group = False
            continue
        if previous_was_group and isinstance(stage, AggregateStage):
            previous_was_group = False
            continue
        seen += 1
        if seen == unit:
            return index + 1
        previous_was_group = isinstance(stage, GroupByStage)
    return None


def _stage_for_cte_name(program, name: str) -> "int | None":
    """Generated CTEs are named after the stage that produced them:
    `stage_U` (U-th unit) or `<with|sub|set>_G_...` (grammar stage G)."""
    nested = re.match(r"^(?:with|sub|set)_(\d+)_", name)
    if nested:
        return int(nested.group(1))
    unit = re.match(r"^stage_(\d+)$", name)
    if unit:
        return _grammar_stage_for_unit(program, int(unit.group(1)))
    if name == "out":
        return len(program)
    return None


def _attribute_stage(sql: str, program, message: str, line_offset: int = 0) -> "str | None":
    """DuckDB reports `LINE n` of the generated SQL; the CTE owning that line
    names the stage, so read-query failures read like statement failures."""
    match = re.search(r"\bLINE (\d+):", message, re.I)
    if not match:
        return None
    line = int(match.group(1)) - line_offset
    if line < 1:
        return None
    owner = None
    for text in sql.split("\n")[:line]:
        opener = re.match(r"^([A-Za-z_][A-Za-z0-9_]*) AS \($", text)
        if opener:
            owner = opener.group(1)
    # Past the last CTE opener the final SELECT answers: the last stage.
    stage = len(program) if owner is None else _stage_for_cte_name(program, owner)
    if stage is None or not 1 <= stage <= len(program):
        return None
    return f"stage {stage} ({program[stage - 1].keyword})"


def _fetch_with_stage(sql: str, con, program):
    try:
        return fetch(con, sql)
    except UnsequelError as exc:
        label = _attribute_stage(sql, program, str(exc))
        if label:
            raise UnsequelError(f"{label}: {exc}") from exc
        raise


def _reject_views_over_temporary_sources(program, data: dict[str, str]) -> None:
    """A persistent view over a `# source`/--data temp table breaks once this run ends."""
    from .analyzer import referenced_tables
    with_programs = [stage.program for stage in program if isinstance(stage, WithStage)]
    for stage in program:
        if not isinstance(stage, CreateStage) or stage.object != "view":
            continue
        read = set().union(*(referenced_tables(p) for p in [stage.program, *with_programs]))
        temporary = sorted(read & set(data))
        if temporary:
            raise UnsequelError(
                f"view '{stage.name}' reads the temporary source '{temporary[0]}', which is gone "
                f"after this run; use 'create table {stage.name} = (...)' to keep the rows, or "
                f"import the file first: unsequel import {data[temporary[0]]} --into <database>")


def _schema_snapshot_text(con, *, persistent_only: bool = False) -> str:
    schema = describe_database(con, persistent_only=persistent_only)
    tables = {name: {"columns": dict(table.columns)} for name, table in schema.tables.items()}
    return json.dumps({"tables": tables}, indent=2) + "\n"


def _refresh_snapshot(database: str, con) -> None:
    """After a write, refresh '<db>.schema.json' if the user keeps one; a stale
    snapshot misleads editors and the language server. Persistent tables only:
    this run's '# source' tables are temporary and not part of the database."""
    snapshot = Path(f"{database}.schema.json")
    if snapshot.exists():
        snapshot.write_text(_schema_snapshot_text(con, persistent_only=True),
                            encoding="utf-8")


def _cmd_schema(args) -> None:
    """Same output as the TypeScript `schema` command, so the language server reads either."""
    data = {name: path for name, path in args.data}
    attach = _attach_specs(args)
    if not (args.db or attach or data):
        raise UnsequelError("schema needs --db, --attach, or --data sources")
    con = connect(data, database_path=args.db, attach=attach)
    text = _schema_snapshot_text(con)
    target = args.out if args.out else (f"{args.db}.schema.json" if args.db else "-")
    if target == "-":
        sys.stdout.write(text)
        return
    Path(target).write_text(text, encoding="utf-8")
    print(f"wrote {target}")


def _cmd_import(args) -> None:
    source = Path(args.data)
    if not source.exists():
        raise UnsequelError(f"data file not found: {args.data}")
    name = args.table or source.stem
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name or ""):
        raise UnsequelError(
            f"cannot derive a table name from {args.data!r}; pass --table name")
    created = not Path(args.into).exists()
    con = connect({}, database_path=args.into, writable=True, create_database=True)
    import_table(con, name, str(source), delimiter=args.delimiter,
                 column_types=_column_types_for(args, name), replace=args.replace)
    _refresh_snapshot(args.into, con)
    if created:  # make a mistyped --into path visible
        print(f"created new database {args.into}")
    print(f"imported {args.data} into {args.into} as {name}")


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    handlers = {
        "run": _cmd_run,
        "check": _cmd_check,
        "compile": _cmd_compile,
        "preview": _cmd_preview,
        "fmt": _cmd_fmt,
        "import": _cmd_import,
        "schema": _cmd_schema,
    }
    try:
        handlers[args.command](args)
    except (OSError, UnsequelError, ValueError) as exc:
        parser.exit(1, f"error: {exc}\n")
    except Exception as exc:  # anything else is a bug: report it cleanly, traceback on request
        if os.environ.get("UNSEQUEL_DEBUG"):
            raise
        parser.exit(1, f"error: internal error ({type(exc).__name__}: {exc}); rerun with "
                       "UNSEQUEL_DEBUG=1 for a traceback and please report it\n")
