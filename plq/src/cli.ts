/**
 * PLQ CLI: check, compile, preview, run, fmt, and the interactive repl.
 *
 * SQL-like execution model: a query file may declare its own sources and
 * schema as leading directives, `-` reads the query from stdin, and
 * `--lines A:B` runs just a selected range. `preview` remains the standout
 * feature: run a program truncated at any grammar stage.
 */

import { readFileSync, writeFileSync } from "node:fs";
import { dirname, isAbsolute, resolve } from "node:path";
import { createInterface } from "node:readline";

import { emitSql } from "./codegen.js";
import { splitDirectives, type Directives } from "./directives.js";
import { connect, introspectSchema, runSql, type DuckDBConnection } from "./duckdb.js";
import { PlqError } from "./errors.js";
import { formatProgram, isFormatted } from "./format.js";
import { OUTPUT_FORMATS, formatResult } from "./output.js";
import { parseProgram } from "./parser.js";
import { ReplSession } from "./repl.js";
import { loadSchema, type Schema } from "./schema.js";
import { analyze } from "./semantics.js";

export interface CliResult {
  code: number;
  stdout: string;
  stderr: string;
}

export const VERSION = "0.1.0";

const USAGE = `plq ${VERSION}
pipeline query language compiler

usage: plq <command> [options]

commands:
  check    <query.plq> [--schema s.json] [--lines A:B]
           parse a program; --schema (or a '# schema:' directive) validates it
  compile  <query.plq> [--schema s.json] [--stage N] [--lines A:B]
           print the generated DuckDB SQL (optionally truncated at stage N)
  preview  <query.plq> --stage N [--limit K] [--data name=path] [--lines A:B]
           run the program truncated at grammar stage N and show sample rows
  run      <query.plq | -> [--data name=path] [--schema s.json]
           [--format table|json|csv] [--lines A:B]
           execute the whole program ('-' reads the query from stdin)
  fmt      <query.plq> [--write | --check]
           print the canonical form; --write rewrites the file, --check exits 1
  repl     [--data name=path] [--schema s.json] [--format table|json|csv]
           interactive session; a query runs on a blank line or ';'
           (bare 'plq' opens the repl on a terminal)

query files can declare their own connection, so no flags are needed:

  # source orders: ../data/orders.csv
  # source products: ../data/products.csv
  # schema: ../data/schema.json
  from orders
  ...

--data overrides a directive with the same name; --schema overrides
'# schema:'. Without any schema, tables loaded from the sources are
introspected automatically.
`;

class UsageError extends Error {}

class CheckFailure extends Error {}

interface Args {
  positionals: string[];
  flags: Map<string, string[]>;
}

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
      if (next !== undefined && !next.startsWith("--")) {
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
  const program = parseProgram(splitDirectives(selected).body);
  return { text, path, program, directives };
}

async function introspectFromSources(sources: ReadonlyMap<string, string>): Promise<Schema> {
  const connection = await connect(sources);
  return introspectSchema(connection, [...sources.keys()]);
}

async function schemaForConnection(args: Args, directives: Directives, queryPath: string,
                                   connection: DuckDBConnection,
                                   sources: ReadonlyMap<string, string>): Promise<Schema> {
  return declaredSchema(args, directives, queryPath)
    ?? introspectSchema(connection, [...sources.keys()]);
}

function validateFormat(value: string): string {
  if (!(OUTPUT_FORMATS as readonly string[]).includes(value)) {
    throw new UsageError(`--format must be one of ${OUTPUT_FORMATS.join(", ")}`);
  }
  return value;
}

// --------------------------------------------------------------------------- //
// Commands
// --------------------------------------------------------------------------- //

function cmdCheck(args: Args): string {
  const { path, program, directives } = readQueryInput(args);
  const schema = declaredSchema(args, directives, path);
  if (schema !== null) {
    analyze(program, schema);
    return "valid PLQ query (schema OK)\n";
  }
  return "valid PLQ query\n";
}

async function cmdCompile(args: Args): Promise<string> {
  const { path, program, directives } = readQueryInput(args);
  const sources = mergeSources(directives, args, path);
  const schema = declaredSchema(args, directives, path)
    ?? (sources.size > 0
      ? await introspectFromSources(sources)
      : (() => {
        throw new PlqError(
          "no schema available: pass --schema, add '# schema:' to the file, "
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
  const sources = mergeSources(directives, args, path);
  if (sources.size === 0) {
    throw new UsageError(
      "preview needs --data name=path or a '# source' directive at the top of the file");
  }
  const connection = await connect(sources);
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
  const result = await runSql(connection, sql);
  const label = `stage ${stage}/${program.length}  (${program[stage - 1]?.keyword})`;
  return `${label}\n${formatResult(result, "table")}\n`;
}

async function cmdRun(args: Args): Promise<string> {
  const { path, program, directives } = readQueryInput(args);
  const sources = mergeSources(directives, args, path);
  if (sources.size === 0) {
    throw new UsageError(
      "run needs --data name=path or a '# source' directive at the top of the file");
  }
  const format = validateFormat(flag(args, "format") ?? "table");
  const connection = await connect(sources);
  const schema = await schemaForConnection(args, directives, path, connection, sources);
  analyze(program, schema);
  const result = await runSql(connection, emitSql(program, schema));
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

async function cmdRepl(args: Args): Promise<string> {
  const session = new ReplSession({
    sources: dataSources(args, true),
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

const KNOWN_COMMANDS = ["check", "compile", "preview", "run", "fmt", "repl"] as const;

const ALLOWED_FLAGS: Record<string, readonly string[]> = {
  check: ["schema", "lines"],
  compile: ["schema", "stage", "lines", "data"],
  preview: ["schema", "stage", "limit", "data", "lines"],
  run: ["schema", "data", "format", "lines"],
  fmt: ["write", "check"],
  repl: ["schema", "data", "format"],
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

export async function runCli(argv: readonly string[]): Promise<CliResult> {
  const wantsVersion = argv.includes("--version") || argv.includes("-V");
  if (wantsVersion) return { code: 0, stdout: `plq ${VERSION}\n`, stderr: "" };
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
    if (command === "check") stdout = cmdCheck(args);
    else if (command === "compile") stdout = await cmdCompile(args);
    else if (command === "preview") stdout = await cmdPreview(args);
    else if (command === "fmt") stdout = cmdFmt(args);
    else if (command === "repl") stdout = await cmdRepl(args);
    else stdout = await cmdRun(args);
    return { code: 0, stdout, stderr: "" };
  } catch (error) {
    return errorResult(error);
  }
}

/** Interactive terminal session (bare `plq` or `plq repl` on a TTY). */
export async function startInteractiveRepl(argv: readonly string[]): Promise<CliResult> {
  let session: ReplSession;
  try {
    const args = commandArgs("repl", argv.filter((arg) => arg !== "repl"));
    session = new ReplSession({
      sources: dataSources(args, true),
      schemaPath: flag(args, "schema") ?? null,
      format: validateFormat(flag(args, "format") ?? "table"),
    });
  } catch (error) {
    return errorResult(error);
  }

  process.stdout.write(
    `plq ${VERSION} — one stage per line; run a query with a blank line, ';', or .run\n`);
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

  return { code: 0, stdout: "", stderr: "" };
}
