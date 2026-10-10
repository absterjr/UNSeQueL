/**
 * PLQ CLI (plan step 7): check, compile, preview, run.
 *
 * `preview` is the standout feature: it executes the program truncated at any
 * grammar stage and shows a sample of the intermediate relation, so a
 * pipeline can be stepped through stage by stage without editing it.
 */

import { readFileSync, writeFileSync } from "node:fs";

import { emitSql } from "./codegen.js";
import { connect, runSql, type SqlResult } from "./duckdb.js";
import { PlqError } from "./errors.js";
import { formatProgram } from "./format.js";
import { parseProgram } from "./parser.js";
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
  check    <query.plq> [--schema s.json]
           parse a program; --schema also validates it
  compile  <query.plq> --schema s.json [--stage N]
           print the generated DuckDB SQL (optionally truncated at stage N)
  preview  <query.plq> --schema s.json --stage N --data name=path [--limit K]
           run the program truncated at grammar stage N and show sample rows
  run      <query.plq> --schema s.json --data name=path [--format table|json|csv]
           execute the whole program
  fmt      <query.plq> [--write | --check]
           print the canonical form; --write rewrites the file, --check exits 1
           when the file is not canonical

options are per command; --help prints this text
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

function required(args: Args, name: string): string {
  const value = flag(args, name);
  if (value === undefined) throw new UsageError(`--${name} is required`);
  return value;
}

function readProgram(args: Args): { text: string; path: string } {
  const path = args.positionals[0];
  if (path === undefined) throw new UsageError("a query file is required");
  try {
    return { text: readFileSync(path, "utf8"), path };
  } catch {
    throw new PlqError(`query file not found: ${path}`);
  }
}

function loadSchemaIfGiven(args: Args): Schema | null {
  const path = flag(args, "schema");
  return path === undefined ? null : loadSchema(path);
}

function dataSources(args: Args): Map<string, string> {
  const specs = args.flags.get("data") ?? [];
  if (specs.length === 0) throw new UsageError("at least one --data name=path is required");
  const data = new Map<string, string>();
  for (const spec of specs) {
    const equals = spec.indexOf("=");
    if (equals > 0) {
      data.set(spec.slice(0, equals), spec.slice(equals + 1));
      continue;
    }
    const name = spec.split(/[\\/]/).pop()?.replace(/\.[^.]+$/, "") ?? spec;
    data.set(name, spec);
  }
  return data;
}

// --------------------------------------------------------------------------- //
// Output formatting
// --------------------------------------------------------------------------- //

function tableOutput(result: SqlResult): string {
  const { columns, rows } = result;
  if (columns.length === 0) return "(0 columns)";
  const widths = columns.map((column) => Math.max(
    column.length,
    ...rows.map((row) => String(valueOf(row[column])).length),
  ));
  const header = columns.map((c, i) => c.padEnd(widths[i] as number)).join("  ");
  const rule = widths.map((w) => "-".repeat(w)).join("  ");
  const lines = rows.map((row) => columns
    .map((c, i) => String(valueOf(row[c])).padEnd(widths[i] as number))
    .join("  "));
  return [header, rule, ...lines].join("\n");
}

function valueOf(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "bigint") return value.toString();
  if (value instanceof Date) {
    const iso = value.toISOString();
    // DATE columns arrive as midnight UTC; show the date only.
    return iso.endsWith("T00:00:00.000Z") ? iso.slice(0, 10) : iso;
  }
  if (typeof value === "object") return String(value);
  return String(value);
}

function csvOutput(result: SqlResult): string {
  const cell = (value: unknown): string => {
    const text = valueOf(value);
    return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
  };
  const lines = [result.columns.map(cell).join(",")];
  for (const row of result.rows) {
    lines.push(result.columns.map((column) => cell(row[column])).join(","));
  }
  return lines.join("\n");
}

function jsonReplacer(_key: string, value: unknown): unknown {
  if (typeof value === "bigint") {
    const num = Number(value);
    return Number.isSafeInteger(num) ? num : value.toString();
  }
  if (value === null || typeof value !== "object") return value;
  if (value instanceof Date) return value.toISOString();
  if (Array.isArray(value) || Object.getPrototypeOf(value) === Object.prototype) return value;
  return String(value); // DuckDBDecimal and friends
}

function formatResult(result: SqlResult, format: string): string {
  if (format === "json") return JSON.stringify(result.rows, jsonReplacer, 2);
  if (format === "csv") return csvOutput(result);
  return `${tableOutput(result)}\n\n${result.rows.length} row(s)`;
}

// --------------------------------------------------------------------------- //
// Commands
// --------------------------------------------------------------------------- //

function cmdCheck(args: Args): string {
  const { text } = readProgram(args);
  const program = parseProgram(text);
  const schema = loadSchemaIfGiven(args);
  if (schema !== null) {
    analyze(program, schema);
    return "valid PLQ query (schema OK)\n";
  }
  return "valid PLQ query\n";
}

function cmdCompile(args: Args): string {
  const { text } = readProgram(args);
  const schemaPath = required(args, "schema");
  const program = parseProgram(text);
  const schema = loadSchema(schemaPath);
  analyze(program, schema);
  const stopAt = flag(args, "stage");
  const stop = stopAt === undefined ? undefined : Number(stopAt);
  if (stop !== undefined && !Number.isInteger(stop)) {
    throw new UsageError("--stage must be an integer");
  }
  return `${emitSql(program, schema, { stopAt: stop })}\n`;
}

async function cmdPreview(args: Args): Promise<string> {
  const { text } = readProgram(args);
  const schema = loadSchema(required(args, "schema"));
  const program = parseProgram(text);
  analyze(program, schema);
  const stageText = required(args, "stage");
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
  const connection = await connect(dataSources(args));
  const result = await runSql(connection, sql);
  const label = `stage ${stage}/${program.length}  (${program[stage - 1]?.keyword})`;
  return `${label}\n${formatResult(result, "table")}\n`;
}

async function cmdRun(args: Args): Promise<string> {
  const { text } = readProgram(args);
  const schema = loadSchema(required(args, "schema"));
  const program = parseProgram(text);
  analyze(program, schema);
  const format = flag(args, "format") ?? "table";
  if (!["table", "json", "csv"].includes(format)) {
    throw new UsageError("--format must be table, json, or csv");
  }
  const connection = await connect(dataSources(args));
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
    if (formatted !== text) throw new CheckFailure();
    return "";
  }
  if (write) {
    writeFileSync(path, formatted, "utf8");
    return "";
  }
  return formatted;
}

// --------------------------------------------------------------------------- //
// Entry point
// --------------------------------------------------------------------------- //

export async function runCli(argv: readonly string[]): Promise<CliResult> {
  const wantsVersion = argv.includes("--version") || argv.includes("-V");
  if (wantsVersion) return { code: 0, stdout: `plq ${VERSION}\n`, stderr: "" };
  if (argv.includes("--help") || argv.includes("-h")) {
    return { code: 0, stdout: USAGE, stderr: "" };
  }

  const command = argv[0];
  if (command === undefined) return { code: 2, stdout: "", stderr: USAGE };
  const known = new Set(["check", "compile", "preview", "run", "fmt"]);
  if (!known.has(command)) {
    return { code: 2, stdout: "", stderr: `unknown command '${command}'\n\n${USAGE}` };
  }

  try {
    const args = parseArgs(argv.slice(1));
    let stdout: string;
    if (command === "check") stdout = cmdCheck(args);
    else if (command === "compile") stdout = cmdCompile(args);
    else if (command === "preview") stdout = await cmdPreview(args);
    else if (command === "fmt") stdout = cmdFmt(args);
    else stdout = await cmdRun(args);
    return { code: 0, stdout, stderr: "" };
  } catch (error) {
    if (error instanceof CheckFailure) {
      return { code: 1, stdout: "", stderr: "" };
    }
    if (error instanceof UsageError) {
      return { code: 2, stdout: "", stderr: `error: ${error.message}\n\n${USAGE}` };
    }
    if (error instanceof PlqError) {
      return { code: 1, stdout: "", stderr: `error: ${error.message}\n` };
    }
    throw error;
  }
}
