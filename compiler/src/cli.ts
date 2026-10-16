/**
 * UNSeQueL CLI: check, compile, preview, run, fmt, and the interactive repl.
 *
 * SQL-like execution model: a query file may declare its own sources and
 * schema as leading directives, `-` reads the query from stdin, and
 * `--lines A:B` runs just a selected range. `preview` remains the standout
 * feature: run a program truncated at any grammar stage.
 */

import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, isAbsolute, resolve } from "node:path";
import { createInterface } from "node:readline";

import { emitSql, emitStatementList } from "./codegen.js";
import type { Program, Stage } from "./ast.js";
import { splitDirectives, type Directives } from "./directives.js";
import type { ConnectOptions, DuckDBConnection, SourceOptions, SqlResult } from "./duckdb.js";
import { PlqError } from "./errors.js";
import { formatProgram, isFormatted } from "./format.js";
import { OUTPUT_FORMATS, formatResult } from "./output.js";
import { parseProgram } from "./parser.js";
import { ReplSession } from "./repl.js";
import { loadSchema, type Schema } from "./schema.js";
import { analyze, referencedTables } from "./semantics.js";

/**
 * The DuckDB native addon loads lazily inside duckdb.ts: `check`, `fmt`, and
 * `compile` without sources work without it, and execution commands report a
 * clear install hint (including the no-npm run-bundle path) when it is absent.
 */
export async function loadDuckDB(): Promise<typeof import("./duckdb.js")> {
  return await import("./duckdb.js");
}

export interface CliResult {
  code: number;
  stdout: string;
  stderr: string;
}

export const VERSION = "0.6.1";

const USAGE = `unsequel ${VERSION}
UNSeQueL query language compiler

usage: unsequel <command> [options]

commands:
  check    <query.unsequel> [--schema s.json] [--lines A:B]
           parse a program; --schema (or a '# schema:' directive) validates it
  compile  <query.unsequel> [--schema s.json] [--stage N] [--lines A:B]
           [--data name=path] [--db mydb.duckdb] [--attach name=path] [--delimiter C]
           [--column-type table.column=type]
           print the generated DuckDB SQL (optionally truncated at stage N)
  preview  <query.unsequel> --stage N [--limit K] [--data name=path] [--db mydb.duckdb]
           [--attach name=path] [--lines A:B] [--delimiter C] [--column-type t.c=type]
           run the program truncated at grammar stage N and show sample rows
  run      <query.unsequel | -> [--data name=path] [--schema s.json] [--db mydb.duckdb]
           [--attach name=path] [--format table|json|csv] [--out results.csv|.parquet|.json]
           [--lines A:B] [--delimiter C] [--column-type table.column=type]
           execute the whole program ('-' reads the query from stdin);
           --out writes the results instead of printing them
  fmt      <query.unsequel> [--write | --check]
           print the canonical form; --write rewrites the file, --check exits 1
  repl     [--data name=path] [--schema s.json] [--format table|json|csv]
           [--delimiter C] [--column-type table.column=type]
           interactive session; a query runs on a blank line or ';'
  lsp      run the language server on stdio (used by editor integrations)
  schema   --db mydb.duckdb [--attach name=path] [--data name=path] [--out f.json]
           write a schema snapshot (JSON) for editors; with --db and no --out,
           '<db>.schema.json' is written next to the database
  import   <data.csv|.json|.parquet> --into mydb.duckdb [--table name]
           [--delimiter C] [--column-type table.column=type]
           materialize a file as a persistent table (creates the database
           if needed; replaces an existing table of the same name)
  (bare 'unsequel' opens the repl on a terminal)

query files can declare their own connection, so no flags are needed:

  # db: ../data/warehouse.duckdb
  # source orders: ../data/orders.csv
  # source products: ../data/products.csv
  # schema: ../data/schema.json
  from orders
  ...

--data overrides a directive with the same name; --schema overrides
'# schema:'. Without any schema, tables loaded from the sources are
introspected automatically. A '# source' file loads as a temporary table, so
it never clobbers or persists over tables in a '# db:'/--db database;
--attach name=path opens other DuckDB files for cross-database queries
('<name>.<table>').
`;

class UsageError extends Error {}

class CheckFailure extends Error {}

/** Advisory lines collected during a command; runCli merges them into stderr. */
let pendingNotes: string[] = [];

function note(message: string): void {
  pendingNotes.push(`note: ${message}\n`);
}

interface Args {
  positionals: string[];
  flags: Map<string, string[]>;
}

/** Flags that never take a value, so `fmt --write q.unsequel` keeps the file positional. */
const BOOLEAN_FLAGS = new Set(["write", "check", "replace", "create-db"]);

function parseArgs(argv: readonly string[]): Args {
  const flags = new Map<string, string[]>();
  const positionals: string[] = [];
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i] as string;
    if (arg.startsWith("--")) {
      const equals = arg.indexOf("=");
      if (equals > 0) {
        const list = flags.get(arg.slice(2, equals)) ?? [];
        list.push(arg.slice(equals + 1));
        flags.set(arg.slice(2, equals), list);
        continue;
      }
      const name = arg.slice(2);
      const next = argv[i + 1];
      if (!BOOLEAN_FLAGS.has(name) && next !== undefined && !next.startsWith("--")) {
        const list = flags.get(name) ?? [];
        list.push(next);
        flags.set(name, list);
        i += 1;
      } else {
        flags.set(name, ["true"]);
      }
    } else {
      positionals.push(arg);
    }
  }
  return { positionals, flags };
}

function flag(args: Args, name: string): string | undefined {
  return args.flags.get(name)?.at(-1);
}

function readAllStdin(): string {
  return readFileSync(0, "utf8").replace(/^\uFEFF/, "");
}

function readProgram(args: Args): { text: string; path: string } {
  const path = args.positionals[0];
  if (path === undefined) throw new UsageError("a query file is required");
  if (path === "-") return { text: readAllStdin(), path };
  try {
    return { text: readFileSync(path, "utf8").replace(/^\uFEFF/, ""), path };
  } catch {
    throw new PlqError(`query file not found: ${path}`);
  }
}

function applyLines(text: string, spec: string | undefined): string {
  if (spec === undefined) return text;
  const match = /^(\d+):(\d+)$/.exec(spec);
  if (match === null) throw new UsageError("--lines must look like START:END (1-based)");
  const start = Number(match[1]);
  const end = Number(match[2]);
  if (start < 1 || end < start) {
    throw new UsageError("--lines must be a valid 1-based range");
  }
  return text.split(/\r\n|\n/).slice(start - 1, end).join("\n");
}

function dataSources(args: Args, allowEmpty = false): Map<string, string> {
  const specs = args.flags.get("data") ?? [];
  if (specs.length === 0 && !allowEmpty) {
    throw new UsageError("at least one --data name=path is required");
  }
  const data = new Map<string, string>();
  for (const spec of specs) {
    const equals = spec.indexOf("=");
    if (equals > 0) {
      const name = spec.slice(0, equals);
      if (data.has(name)) throw new UsageError(`duplicate --data name '${name}'`);
      data.set(name, spec.slice(equals + 1));
      continue;
    }
    if (equals === 0) throw new UsageError("--data must look like name=path");
    const name = spec.split(/[\\/]/).pop()?.replace(/\.[^.]+$/, "") ?? spec;
    if (name.includes(".") || name.length === 0) {
      throw new UsageError(`cannot derive a table name from '${spec}'; use --data name=path`);
    }
    if (data.has(name)) throw new UsageError(`duplicate --data name '${name}'`);
    data.set(name, spec);
  }
  return data;
}

function resolveQueryPath(queryPath: string, target: string): string {
  if (queryPath === "-" || isAbsolute(target)) return target;
  return resolve(dirname(queryPath), target);
}

/** Directive sources first (paths resolved), then explicit --data overrides. */
function mergeSources(directives: Directives, args: Args, queryPath: string): Map<string, string> {
  const merged = new Map<string, string>();
  for (const [name, relative] of directives.sources) {
    merged.set(name, resolveQueryPath(queryPath, relative));
  }
  for (const [name, path] of dataSources(args, true)) merged.set(name, path);
  return merged;
}

/** `--attach name=path` databases, in order. */
function attachSources(args: Args): Map<string, string> {
  const attach = new Map<string, string>();
  for (const spec of args.flags.get("attach") ?? []) {
    const equals = spec.indexOf("=");
    if (equals <= 0 || equals === spec.length - 1) {
      throw new UsageError("--attach must look like name=path.duckdb");
    }
    const name = spec.slice(0, equals);
    if (attach.has(name)) throw new UsageError(`duplicate --attach name '${name}'`);
    attach.set(name, spec.slice(equals + 1));
  }
  return attach;
}

/** The connection target: --db flag, then the `# db:` directive. */
function databasePath(args: Args, directives: Directives, queryPath: string): string | undefined {
  const flagPath = flag(args, "db");
  if (flagPath !== undefined) return flagPath;
  if (directives.databasePath !== null) {
    return resolveQueryPath(queryPath, directives.databasePath);
  }
  return undefined;
}

function connectOptions(args: Args, sources: ReadonlyMap<string, string>,
                        database: string | undefined): ConnectOptions {
  const attach = attachSources(args);
  return {
    ...sourceOptions(args, sources),
    ...(database === undefined ? {} : { databasePath: database }),
    ...(attach.size === 0 ? {} : { attach }),
  };
}

function normalizeColumnType(typeName: string): string {
  const key = typeName.trim().toLowerCase();
  const aliases: Readonly<Record<string, string>> = {
    text: "VARCHAR", string: "VARCHAR", varchar: "VARCHAR", char: "VARCHAR",
    int: "BIGINT", integer: "BIGINT", bigint: "BIGINT", smallint: "SMALLINT",
    number: "DOUBLE", numeric: "DOUBLE", float: "FLOAT", real: "FLOAT", double: "DOUBLE",
    bool: "BOOLEAN", boolean: "BOOLEAN", date: "DATE", time: "TIME",
    timestamp: "TIMESTAMP", datetime: "TIMESTAMP",
  };
  const alias = aliases[key];
  if (alias !== undefined) return alias;
  const decimal = /^decimal\(\s*\d+\s*(?:,\s*\d+\s*)?\)$/i.exec(typeName.trim());
  if (decimal !== null) return typeName.trim().toUpperCase().replace(/\s+/g, "");
  throw new UsageError(
    `unsupported --column-type '${typeName}'; use text, integer, number, boolean, date, time, timestamp, or decimal(p,s)`);
}

function sourceOptions(args: Args, sources: ReadonlyMap<string, string>): SourceOptions {
  const delimiter = flag(args, "delimiter");
  if (delimiter !== undefined
      && (delimiter.length !== 1 || delimiter === '"' || delimiter === "\r" || delimiter === "\n")) {
    throw new UsageError("--delimiter must be exactly one non-quote character");
  }
  const columnTypes = new Map<string, Map<string, string>>();
  for (const spec of args.flags.get("column-type") ?? []) {
    const equals = spec.lastIndexOf("=");
    const dot = spec.indexOf(".");
    if (dot <= 0 || equals <= dot + 1 || equals === spec.length - 1) {
      throw new UsageError("--column-type must look like table.column=type");
    }
    const requestedTable = spec.slice(0, dot);
    const table = [...sources.keys()].find(
      (name) => name.toLowerCase() === requestedTable.toLowerCase());
    if (table === undefined) {
      throw new UsageError(`--column-type refers to table '${requestedTable}', not supplied by --data or '# source'`);
    }
    let column = spec.slice(dot + 1, equals).trim();
    if ((column.startsWith("`") && column.endsWith("`"))
        || (column.startsWith('"') && column.endsWith('"'))) {
      column = column.slice(1, -1).replace(/``/g, "`").replace(/""/g, '"');
    }
    if (column === "") throw new UsageError("--column-type column name cannot be empty");
    const types = columnTypes.get(table) ?? new Map<string, string>();
    const key = column.toLowerCase();
    if (types.has(key)) throw new UsageError(`duplicate --column-type for ${table}.${column}`);
    types.set(key, normalizeColumnType(spec.slice(equals + 1)));
    columnTypes.set(table, types);
  }
  return { ...(delimiter === undefined ? {} : { delimiter }), columnTypes };
}

function declaredSchema(args: Args, directives: Directives, queryPath: string): Schema | null {
  const flagPath = flag(args, "schema");
  if (flagPath !== undefined) return loadSchema(flagPath);
  if (directives.schemaPath !== null) {
    return loadSchema(resolveQueryPath(queryPath, directives.schemaPath));
  }
  return null;
}

interface QueryInput {
  /** Full file text (directives included). */
  text: string;
  path: string;
  /** Parsed from the selected range; directives come from the whole file. */
  program: ReturnType<typeof parseProgram>;
  /** Directives found at the top of the whole file (the "connection"). */
  directives: Directives;
}

/**
 * The file is the connection; `--lines A:B` (or a terminal selection) picks
 * one query out of it. Directives therefore always come from the whole file,
 * even when the selection excludes them.
 */
function readQueryInput(args: Args): QueryInput {
  const { text, path } = readProgram(args);
  const directives = splitDirectives(text);
  const selected = applyLines(text, flag(args, "lines"));
  const { body } = splitDirectives(selected);
  // Parse with blank lines where the directives were, so error lines match the file.
  const removed = selected.split("\n").length - body.split("\n").length;
  const program = parseProgram("\n".repeat(removed) + body);
  return { text, path, program, directives };
}

async function introspectFromSources(sources: ReadonlyMap<string, string>,
                                     options: ConnectOptions): Promise<Schema> {
  const { connect, introspectDatabase } = await loadDuckDB();
  const connection = await connect(sources, options);
  return introspectDatabase(connection);
}

async function schemaForConnection(args: Args, directives: Directives, queryPath: string,
                                   connection: DuckDBConnection,
                                   sources: ReadonlyMap<string, string>): Promise<Schema> {
  const declared = declaredSchema(args, directives, queryPath);
  if (declared !== null) return declared;
  const { introspectDatabase } = await loadDuckDB();
  return introspectDatabase(connection);
}

function validateFormat(value: string): string {
  if (!(OUTPUT_FORMATS as readonly string[]).includes(value)) {
    throw new UsageError(`--format must be one of ${OUTPUT_FORMATS.join(", ")}`);
  }
  return value;
}

/** Grammar stage number for the U-th lowering unit (group by + aggregate share one). */
function grammarStageForUnit(program: Program, unit: number): number | null {
  let seen = 0;
  let previousWasGroup = false;
  for (let i = 0; i < program.length; i += 1) {
    const kind = (program[i] as Stage).kind;
    if (kind === "with") {
      previousWasGroup = false;
      continue;
    }
    if (previousWasGroup && kind === "aggregate") {
      previousWasGroup = false;
      continue;
    }
    seen += 1;
    if (seen === unit) return i + 1;
    previousWasGroup = kind === "group by";
  }
  return null;
}

/**
 * A generated CTE is named after the stage that produced it: `stage_U` for the
 * U-th unit, `<with|sub|set>_G_...` prefixes carry the grammar stage G of the
 * owning definition. `out` CTEs strip a nested sort.
 */
function stageForCteName(program: Program, name: string): number | null {
  const nested = /^(?:with|sub|set)_(\d+)_/.exec(name);
  if (nested !== null) return Number(nested[1]);
  const unit = /^stage_(\d+)$/.exec(name);
  if (unit !== null) return grammarStageForUnit(program, Number(unit[1]));
  if (name === "out") return program.length;
  return null;
}

/**
 * DuckDB reports "LINE n" of the generated SQL; the CTE that owns that line
 * names the stage, so read-query failures read like statement failures.
 */
function attributeStage(sql: string, program: Program, message: string,
                        lineOffset = 0): string | null {
  const match = /\bLINE (\d+):/i.exec(message);
  if (match === null) return null;
  const line = Number(match[1]) - lineOffset;
  if (line < 1) return null;
  const lines = sql.split("\n");
  let owner: string | null = null;
  for (let i = 0; i < line && i < lines.length; i += 1) {
    const opener = /^([A-Za-z_][A-Za-z0-9_]*) AS \($/.exec(lines[i] as string);
    if (opener !== null) owner = opener[1] as string;
  }
  // Past the last CTE opener the final SELECT answers, which is the last stage.
  const stage = owner === null ? program.length : stageForCteName(program, owner);
  if (stage === null || stage < 1 || stage > program.length) return null;
  return `stage ${stage} (${(program[stage - 1] as Stage).keyword})`;
}

function sqlString(value: string): string {
  return `'${value.replace(/'/g, "''")}'`;
}

/** Wrap a compiled program in a DuckDB COPY so results land in a Parquet file. */
function copyToSql(sql: string, path: string): string {
  const inner = sql.trimEnd().replace(/;$/, "");
  if (!/\.(parquet|pq)$/i.test(path)) {
    throw new UsageError("--out must end in .csv, .parquet, or .json");
  }
  return `COPY (\n${inner}\n) TO ${sqlString(path)} (FORMAT PARQUET)`;
}

// --------------------------------------------------------------------------- //
// Commands
// --------------------------------------------------------------------------- //

async function cmdCheck(args: Args): Promise<string> {
  const { path, program, directives } = readQueryInput(args);
  const declared = declaredSchema(args, directives, path);
  if (declared !== null) {
    analyze(program, declared);
    return "valid UNSeQueL program (schema OK)\n";
  }
  const sources = mergeSources(directives, args, path);
  const database = databasePath(args, directives, path);
  const attachCount = attachSources(args).size;
  const syntaxOnly = "valid UNSeQueL program (syntax OK; columns not checked)\n";
  if (sources.size === 0 && database === undefined && attachCount === 0) {
    return syntaxOnly;
  }
  const { connect, engineAvailable, introspectDatabase } = await loadDuckDB();
  if (!(await engineAvailable())) return syntaxOnly;
  const connection = await connect(sources, connectOptions(args, sources, database));
  const schema = await introspectDatabase(connection);
  analyze(program, schema);
  return "valid UNSeQueL program (schema OK)\n";
}

async function cmdCompile(args: Args): Promise<string> {
  const { path, program, directives } = readQueryInput(args);
  if (program.some((stage) => stage.kind === "create" || stage.kind === "into")
      && flag(args, "stage") !== undefined) {
    throw new UsageError("--stage does not apply to create/into programs");
  }
  const sources = mergeSources(directives, args, path);
  const database = databasePath(args, directives, path);
  const attach = attachSources(args);
  const schema = declaredSchema(args, directives, path)
    ?? (sources.size > 0 || database !== undefined || attach.size > 0
      ? await introspectFromSources(sources, connectOptions(args, sources, database))
      : (() => {
        throw new PlqError(
          "no schema available: pass --schema, add '# schema:'/'# db:' to the file, "
          + "or provide data sources");
      })());
  analyze(program, schema);
  const stopAt = flag(args, "stage");
  const stop = stopAt === undefined ? undefined : Number(stopAt);
  if (stop !== undefined && !Number.isInteger(stop)) {
    throw new UsageError("--stage must be an integer");
  }
  if (stop !== undefined && (stop < 1 || stop > program.length)) {
    throw new PlqError(`--stage must be between 1 and ${program.length} for this query`);
  }
  return `${emitSql(program, schema, { stopAt: stop })}\n`;
}

async function cmdPreview(args: Args): Promise<string> {
  const { path, program, directives } = readQueryInput(args);
  if (program.some((stage) => stage.kind === "create" || stage.kind === "into")) {
    throw new PlqError("preview does not apply to create/into programs");
  }
  const sources = mergeSources(directives, args, path);
  const database = databasePath(args, directives, path);
  const attachCount = attachSources(args).size;
  if (sources.size === 0 && database === undefined && attachCount === 0) {
    throw new UsageError(
      "preview needs --data name=path, a '# source' directive, --db, or --attach");
  }
  const { connect } = await loadDuckDB();
  const connection = await connect(sources, connectOptions(args, sources, database));
  const schema = await schemaForConnection(args, directives, path, connection, sources);
  analyze(program, schema);
  const stageText = flag(args, "stage");
  if (stageText === undefined) throw new UsageError("--stage is required");
  const stage = Number(stageText);
  if (!Number.isInteger(stage) || stage < 1 || stage > program.length) {
    throw new PlqError(`--stage must be between 1 and ${program.length} for this query`);
  }
  const limitText = flag(args, "limit") ?? "10";
  const limit = Number(limitText);
  if (!Number.isInteger(limit) || limit < 0) {
    throw new UsageError("--limit must be a non-negative integer");
  }
  const inner = emitSql(program, schema, { stopAt: stage }).trimEnd().replace(/;$/, "");
  const sql = `SELECT * FROM (\n${inner}\n) AS _preview LIMIT ${limit}`;
  const { runSql } = await loadDuckDB();
  const result = await runSql(connection, sql);
  const label = `stage ${stage}/${program.length}  (${program[stage - 1]?.keyword})`;
  return `${label}\n${formatResult(result, "table")}\n`;
}

/** A persistent view over a `# source`/--data temp table breaks once this run ends. */
function rejectViewsOverTemporarySources(program: Program,
                                         sources: ReadonlyMap<string, string>): void {
  const withPrograms = program.flatMap((stage) => (stage.kind === "with" ? [stage.program] : []));
  for (const stage of program) {
    if (stage.kind !== "create" || stage.object !== "view") continue;
    const read = [stage.program, ...withPrograms].flatMap((p) => [...referencedTables(p)]);
    const temporary = read.find((name) => sources.has(name));
    if (temporary !== undefined) {
      throw new PlqError(
        `view '${stage.name}' reads the temporary source '${temporary}', which is gone after `
        + `this run; use 'create table ${stage.name} = (...)' to keep the rows, or import the `
        + `file first: unsequel import ${sources.get(temporary)} --into <database>`);
    }
  }
}

/** '# source' files load as temporary tables; note when one hides a database table. */
async function noteShadowedSources(connection: DuckDBConnection,
                                   sources: ReadonlyMap<string, string>): Promise<void> {
  if (sources.size === 0) return;
  const { runSql } = await loadDuckDB();
  let persistent: Set<string>;
  try {
    const result = await runSql(connection,
      "SELECT table_name FROM information_schema.tables "
      + "WHERE table_catalog = current_database() AND table_schema = 'main' "
      + "AND table_type <> 'LOCAL TEMPORARY'");
    persistent = new Set(result.rows.map((row) => String(row.table_name).toLowerCase()));
  } catch {
    return; // the note is advisory; never fail a run over it
  }
  for (const name of sources.keys()) {
    if (persistent.has(name.toLowerCase())) {
      note(`source '${name}' hides database table '${name}'; this run reads the file`);
    }
  }
}

async function cmdRun(args: Args): Promise<string> {
  const { path, program, directives } = readQueryInput(args);
  const sources = mergeSources(directives, args, path);
  const database = databasePath(args, directives, path);
  const attachCount = attachSources(args).size;

  const isStatementProgram = program.some(
    (stage) => stage.kind === "create" || stage.kind === "into");
  if (isStatementProgram) {
    if (database === undefined) {
      throw new PlqError(
        "create/into write to a database: add '# db: warehouse.duckdb' to the file "
        + "or pass --db");
    }
    if (flag(args, "out") !== undefined) {
      throw new UsageError("--out does not apply to create/into programs");
    }
    const { connect, introspectDatabase, runStatements } = await loadDuckDB();
    const connection = await connect(sources, {
      ...connectOptions(args, sources, database),
      writable: true,
      createDatabase: args.flags.has("create-db"),
    });
    await noteShadowedSources(connection, sources);
    const schema = declaredSchema(args, directives, path) ?? await introspectDatabase(connection);
    analyze(program, schema);
    rejectViewsOverTemporarySources(program, sources);
    const blocks = emitStatementList(program, schema);
    await runStatements(connection, blocks.map((block) => ({
      label: `stage ${block.index} (${block.kind})`,
      sql: block.sql,
    })));
    await refreshSnapshot(connection, database);
    const count = program.filter(
      (stage) => stage.kind === "create" || stage.kind === "into").length;
    return `applied ${count} statement(s)\n`;
  }

  if (sources.size === 0 && database === undefined && attachCount === 0) {
    throw new UsageError(
      "run needs --data name=path, a '# source' directive, --db, or --attach");
  }
  const format = validateFormat(flag(args, "format") ?? "table");
  const outPath = flag(args, "out");
  const { connect, runSql } = await loadDuckDB();
  const connection = await connect(sources, connectOptions(args, sources, database));
  await noteShadowedSources(connection, sources);
  const schema = await schemaForConnection(args, directives, path, connection, sources);
  analyze(program, schema);
  const compiled = emitSql(program, schema);
  // Attribute a DuckDB failure to the stage that owns the failing SQL line.
  const execute = async (sql: string, lineOffset = 0): Promise<SqlResult> => {
    try {
      return await runSql(connection, sql);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      const stage = attributeStage(compiled, program, message, lineOffset);
      throw stage === null ? error : new PlqError(`${stage}: ${message}`);
    }
  };
  if (outPath !== undefined) {
    if (/\.(parquet|pq)$/i.test(outPath)) {
      await execute(copyToSql(compiled, outPath), 1);
      return `wrote ${outPath}\n`;
    }
    // CSV/JSON files use the same formatter as stdout, so a file and the
    // screen never disagree about how a value is spelled.
    const outFormat = /\.csv$/i.test(outPath) ? "csv" : /\.json$/i.test(outPath) ? "json" : null;
    if (outFormat === null) {
      throw new UsageError("--out must end in .csv, .parquet, or .json");
    }
    const result = await execute(compiled);
    writeFileSync(outPath, `${formatResult(result, outFormat)}\n`, "utf8");
    return `wrote ${outPath}\n`;
  }
  const result = await execute(compiled);
  return `${formatResult(result, format)}\n`;
}

function cmdFmt(args: Args): string {
  const { text, path } = readProgram(args);
  const check = args.flags.has("check");
  const write = args.flags.has("write");
  if (check && write) throw new UsageError("--check and --write are mutually exclusive");
  const formatted = formatProgram(text);
  if (check) {
    if (!isFormatted(text)) throw new CheckFailure();
    return "";
  }
  if (write) {
    if (path === "-") throw new UsageError("--write cannot rewrite stdin");
    writeFileSync(path, formatted, "utf8");
    return "";
  }
  return formatted;
}

function snapshotText(schema: Schema): string {
  const tables: Record<string, { columns: Record<string, string> }> = {};
  for (const [name, table] of schema.tables) {
    tables[name] = { columns: Object.fromEntries(table.columns) };
  }
  return `${JSON.stringify({ tables }, null, 2)}\n`;
}

/** Keep '<db>.schema.json' fresh after a write; a stale snapshot misleads the LSP. */
async function refreshSnapshot(connection: DuckDBConnection,
                               database: string | undefined): Promise<void> {
  if (database === undefined) return;
  const snapshot = `${database}.schema.json`;
  if (!existsSync(snapshot)) return;
  const { introspectDatabase } = await loadDuckDB();
  // persistentOnly: this run's temporary '# source' tables are not in the database.
  const fresh = await introspectDatabase(connection, { persistentOnly: true });
  writeFileSync(snapshot, snapshotText(fresh), "utf8");
}

async function cmdSchema(args: Args): Promise<string> {
  const database = flag(args, "db");
  const sources = dataSources(args, true);
  const attach = attachSources(args);
  const outPath = flag(args, "out");
  if (database === undefined && attach.size === 0 && sources.size === 0) {
    throw new UsageError("schema needs --db, --attach, or --data sources");
  }
  const { connect, introspectDatabase } = await loadDuckDB();
  const connection = await connect(sources, connectOptions(args, sources, database));
  const schema = await introspectDatabase(connection);
  const text = snapshotText(schema);
  if (outPath === "-") return text;
  if (outPath !== undefined) {
    writeFileSync(outPath, text, "utf8");
    return `wrote ${outPath}\n`;
  }
  if (database !== undefined) {
    const snapshot = `${database}.schema.json`;
    writeFileSync(snapshot, text, "utf8");
    return `wrote ${snapshot}\n`;
  }
  return text;
}

async function cmdImport(args: Args): Promise<string> {
  const file = args.positionals[0];
  if (file === undefined) {
    throw new UsageError(
      "import needs a data file: import data.csv --into warehouse.duckdb [--table name]");
  }
  if (!existsSync(file)) throw new PlqError(`data file not found: ${file}`);
  const target = flag(args, "into");
  if (target === undefined) {
    throw new UsageError("import needs --into warehouse.duckdb");
  }
  const explicitName = flag(args, "table");
  const derived = file.split(/[\\/]/).pop()?.replace(/\.[^.]+$/, "") ?? "";
  const name = explicitName ?? derived;
  if (name === "" || !/^[A-Za-z_][A-Za-z0-9_]*$/.test(name)) {
    throw new UsageError(`cannot derive a table name from '${file}'; pass --table name`);
  }
  const options = sourceOptions(args, new Map([[name, file]]));
  const { connect, importTableSource } = await loadDuckDB();
  const created = !existsSync(target);
  const connection = await connect(new Map(), {
    ...options,
    databasePath: target,
    writable: true,
    createDatabase: true, // import's job is to build a database
  });
  await importTableSource(connection, name, file, options, args.flags.has("replace"));
  await refreshSnapshot(connection, target);
  // Say so when the file is new, so a mistyped --into path is visible.
  return `${created ? `created new database ${target}\n` : ""}`
    + `imported ${file} into ${target} as ${name}\n`;
}

async function cmdRepl(args: Args): Promise<string> {
  const sources = dataSources(args, true);
  const session = new ReplSession({
    sources,
    sourceOptions: sourceOptions(args, sources),
    schemaPath: flag(args, "schema") ?? null,
    format: validateFormat(flag(args, "format") ?? "table"),
  });
  const outputs: string[] = [];
  for (const line of readAllStdin().split(/\r\n|\n/)) {
    const out = await session.handleLine(line);
    if (out !== "") outputs.push(out);
    if (session.done) break;
  }
  return outputs.length > 0 ? `${outputs.join("\n")}\n` : "";
}

// --------------------------------------------------------------------------- //
// Entry points
// --------------------------------------------------------------------------- //

function errorResult(error: unknown): CliResult {
  if (error instanceof CheckFailure) {
    return { code: 1, stdout: "", stderr: "" };
  }
  if (error instanceof UsageError) {
    return { code: 2, stdout: "", stderr: `error: ${error.message}\n\n${USAGE}` };
  }
  if (error instanceof PlqError) {
    return { code: 1, stdout: "", stderr: `error: ${error.message}\n` };
  }
  const message = error instanceof Error ? error.message : String(error);
  return { code: 1, stdout: "", stderr: `error: ${message}\n` };
}

const KNOWN_COMMANDS = ["check", "compile", "preview", "run", "fmt", "repl", "schema", "import"] as const;

const ALLOWED_FLAGS: Record<string, readonly string[]> = {
  check: ["schema", "lines", "data", "delimiter", "column-type", "db", "attach"],
  compile: ["schema", "stage", "lines", "data", "delimiter", "column-type", "db", "attach"],
  preview: ["schema", "stage", "limit", "data", "lines", "delimiter", "column-type",
            "db", "attach"],
  run: ["schema", "data", "format", "out", "lines", "delimiter", "column-type",
        "db", "attach", "create-db"],
  fmt: ["write", "check"],
  repl: ["schema", "data", "format", "delimiter", "column-type"],
  schema: ["db", "attach", "data", "delimiter", "column-type", "out"],
  import: ["into", "table", "delimiter", "column-type", "replace"],
};

function commandArgs(command: string, argv: readonly string[]): Args {
  const args = parseArgs(argv);
  const allowed = new Set(ALLOWED_FLAGS[command] ?? []);
  for (const name of args.flags.keys()) {
    if (!allowed.has(name)) {
      throw new UsageError(`unknown option --${name} for '${command}'`);
    }
  }
  return args;
}

async function closeDuckDBQuietly(): Promise<void> {
  try {
    const module = await import("./duckdb.js");
    module.closeDuckDB();
  } catch {
    // DuckDB was never loaded; nothing to close.
  }
}

export async function runCli(argv: readonly string[]): Promise<CliResult> {
  const wantsVersion = argv.includes("--version") || argv.includes("-V");
  if (wantsVersion) return { code: 0, stdout: `unsequel ${VERSION}\n`, stderr: "" };
  if (argv.includes("--help") || argv.includes("-h")) {
    return { code: 0, stdout: USAGE, stderr: "" };
  }

  const command = argv[0];
  if (command === undefined) return { code: 2, stdout: "", stderr: USAGE };
  if (!(KNOWN_COMMANDS as readonly string[]).includes(command)) {
    return { code: 2, stdout: "", stderr: `unknown command '${command}'\n\n${USAGE}` };
  }

  try {
    const args = commandArgs(command, argv.slice(1));
    let stdout: string;
    if (command === "check") stdout = await cmdCheck(args);
    else if (command === "compile") stdout = await cmdCompile(args);
    else if (command === "preview") stdout = await cmdPreview(args);
    else if (command === "fmt") stdout = cmdFmt(args);
    else if (command === "repl") stdout = await cmdRepl(args);
    else if (command === "schema") stdout = await cmdSchema(args);
    else if (command === "import") stdout = await cmdImport(args);
    else stdout = await cmdRun(args);
    const notes = pendingNotes.join("");
    pendingNotes = [];
    return { code: 0, stdout, stderr: notes };
  } catch (error) {
    const failure = errorResult(error);
    failure.stderr = pendingNotes.join("") + failure.stderr;
    pendingNotes = [];
    return failure;
  } finally {
    await closeDuckDBQuietly();
  }
}

/** Interactive terminal session (bare `unsequel` or `unsequel repl` on a TTY). */
export async function startInteractiveRepl(argv: readonly string[]): Promise<CliResult> {
  let session: ReplSession;
  try {
    const args = commandArgs("repl", argv.filter((arg) => arg !== "repl"));
    const sources = dataSources(args, true);
    session = new ReplSession({
      sources,
      sourceOptions: sourceOptions(args, sources),
      schemaPath: flag(args, "schema") ?? null,
      format: validateFormat(flag(args, "format") ?? "table"),
    });
  } catch (error) {
    return errorResult(error);
  }

  process.stdout.write(
    `unsequel ${VERSION} — one stage per line; run a query with a blank line, ';', or .run\n`);
  const terminal = createInterface({ input: process.stdin, output: process.stdout });
  const showPrompt = () => {
    terminal.setPrompt(session.prompt());
    terminal.prompt();
  };
  showPrompt();

  await new Promise<void>((resolveClose) => {
    terminal.on("line", (line) => {
      void (async () => {
        const out = await session.handleLine(line);
        if (out !== "") process.stdout.write(`${out}\n`);
        if (session.done) {
          terminal.close();
          return;
        }
        showPrompt();
      })();
    });
    terminal.on("close", () => resolveClose());
  });

  await closeDuckDBQuietly();
  return { code: 0, stdout: "", stderr: "" };
}
