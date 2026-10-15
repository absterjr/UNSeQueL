/**
 * Interactive REPL session (SQL-like execution model).
 *
 * A query executes when the user enters a blank line or ends it with `;`, so
 * multiple queries can be typed or pasted one after another. Dot commands
 * manage the "connection": `.load name=path`, `.sources`, `.schema`,
 * `.format`, `.clear`, `.quit`. Without an explicit schema, the session
 * introspects the loaded sources, so `.load` plus a query just works.
 */

import { emitSql } from "./codegen.js";
import type { DuckDBConnection, SourceOptions } from "./duckdb.js";
import { PlqError } from "./errors.js";
import { OUTPUT_FORMATS, formatResult } from "./output.js";
import { parseProgram } from "./parser.js";
import { loadSchema, type Schema } from "./schema.js";
import { analyze } from "./semantics.js";

export interface ReplOptions {
  sources?: ReadonlyMap<string, string>;
  sourceOptions?: SourceOptions;
  schemaPath?: string | null;
  format?: string;
}

const HELP = `commands:
  .run              execute the query being typed now
  .load name=path   load a CSV/JSON/Parquet file as a table
  .sources          list loaded sources
  .schema [path]    show or set an explicit schema file
  .format table|json|csv
  .clear            discard the query being typed
  .quit             exit

write one stage per line; end a query with a blank line, a lone ';', or .run`;

const PROMPT_PREFIXES = ["unsequel> ", "unsequel>", "...> ", "...>"];

/** Accept pasted transcripts: strip leading `unsequel>` / `...>` prompt text. */
function stripPromptPrefixes(line: string): string {
  let input = line;
  for (let i = 0; i < 3; i += 1) {
    const prefix = PROMPT_PREFIXES.find((candidate) => input.startsWith(candidate));
    if (prefix === undefined) break;
    input = input.slice(prefix.length);
  }
  return input;
}

export class ReplSession {
  private readonly sources = new Map<string, string>();
  private readonly sourceOptions: SourceOptions;
  private connection: DuckDBConnection | null = null;
  private schema: Schema | null = null;
  private explicitSchema: string | null;
  private format: string;
  private buffer: string[] = [];
  private quitting = false;
  private duckdbModule: Promise<typeof import("./duckdb.js")> | null = null;

  /** DuckDB loads lazily so the REPL starts (and reports) without it. */
  private async duckdb(): Promise<typeof import("./duckdb.js")> {
    if (this.duckdbModule === null) {
      this.duckdbModule = import("./duckdb.js").catch(() => {
        throw new PlqError(
          "the repl executes queries and needs the DuckDB native module; "
          + "install it with `npm i @duckdb/node-api`",
        );
      });
    }
    return this.duckdbModule;
  }

  constructor(options: ReplOptions = {}) {
    for (const [name, path] of options.sources ?? []) this.sources.set(name, path);
    this.sourceOptions = options.sourceOptions ?? {};
    this.explicitSchema = options.schemaPath ?? null;
    if (this.explicitSchema !== null) this.schema = loadSchema(this.explicitSchema);
    this.format = options.format ?? "table";
  }

  get done(): boolean {
    return this.quitting;
  }

  get pendingLines(): number {
    return this.buffer.length;
  }

  /** Prompt shown before the next line: continuation while a query is open. */
  prompt(): string {
    return this.buffer.length > 0 ? "...> " : "unsequel> ";
  }

  sourceNames(): string[] {
    return [...this.sources.keys()];
  }

  /** Feed one input line; returns output text ("" when nothing to show). */
  async handleLine(line: string): Promise<string> {
    const input = stripPromptPrefixes(line);
    const trimmed = input.trim();
    if (trimmed.startsWith(".")) {
      // Dot commands work at any point, like a SQL client.
      return this.command(trimmed);
    }
    if (trimmed === "") {
      if (this.buffer.length === 0) return "";
      const text = this.buffer.join("\n");
      this.buffer = [];
      return this.execute(text);
    }
    const stripped = input.trimEnd();
    if (stripped.endsWith(";")) {
      this.buffer.push(stripped.slice(0, -1));
      const text = this.buffer.join("\n");
      this.buffer = [];
      return this.execute(text);
    }
    this.buffer.push(input);
    return "";
  }

  private async command(line: string): Promise<string> {
    const [name = "", ...rest] = line.split(/\s+/);
    const argument = rest.join(" ").trim();
    switch (name) {
      case ".help":
        return HELP;
      case ".run": {
        if (this.buffer.length === 0) return "(nothing to run)";
        const text = this.buffer.join("\n");
        this.buffer = [];
        return this.execute(text);
      }
      case ".quit":
      case ".exit": {
        this.quitting = true;
        if (this.buffer.length > 0) {
          const discarded = this.buffer.length;
          this.buffer = [];
          return `discarded ${discarded} pending line(s)`;
        }
        return "";
      }
      case ".clear":
        this.buffer = [];
        return "";
      case ".format": {
        if (argument === "") return `format: ${this.format}`;
        if (!(OUTPUT_FORMATS as readonly string[]).includes(argument)) {
          return `error: --format must be one of ${OUTPUT_FORMATS.join(", ")}`;
        }
        this.format = argument;
        return `format: ${this.format}`;
      }
      case ".sources":
        return this.sources.size === 0
          ? "(no sources)"
          : [...this.sources].map(([table, path]) => `${table}: ${path}`).join("\n");
      case ".schema": {
        if (argument === "") {
          return this.explicitSchema === null ? "(no explicit schema; using introspected)"
            : `schema: ${this.explicitSchema}`;
        }
        try {
          this.schema = loadSchema(argument);
          this.explicitSchema = argument;
        } catch (error) {
          return `error: ${(error as Error).message}`;
        }
        return `schema: ${argument}`;
      }
      case ".load": {
        const equals = argument.indexOf("=");
        if (equals <= 0) return "error: .load needs name=path";
        const table = argument.slice(0, equals).trim();
        const path = argument.slice(equals + 1).trim();
        try {
          if (this.connection !== null) {
            const { loadTableSource } = await this.duckdb();
            await loadTableSource(this.connection, table, path, this.sourceOptions);
            this.sources.set(table, path);
            await this.refreshIntrospectedSchema();
          } else {
            this.sources.set(table, path);
            await this.ensureReady();
          }
        } catch (error) {
          if (error instanceof PlqError) return `error: ${error.message}`;
          return `error: ${(error as Error).message}`;
        }
        return `loaded ${table}`;
      }
      default:
        return `unknown command '${name}'; try .help`;
    }
  }

  private async ensureReady(): Promise<DuckDBConnection> {
    if (this.connection === null) {
      const { connect, loadTableSource, introspectSchema } = await this.duckdb();
      this.connection = await connect(new Map(), this.sourceOptions);
      for (const [table, path] of this.sources) {
        await loadTableSource(this.connection, table, path, this.sourceOptions);
      }
      if (this.explicitSchema === null) {
        this.schema = await introspectSchema(this.connection, [...this.sources.keys()]);
      }
    }
    return this.connection;
  }

  private async refreshIntrospectedSchema(): Promise<void> {
    if (this.explicitSchema !== null || this.connection === null) return;
    const { introspectSchema } = await this.duckdb();
    this.schema = await introspectSchema(this.connection, [...this.sources.keys()]);
  }

  private async execute(text: string): Promise<string> {
    if (text.trim() === "") return "";
    try {
      const program = parseProgram(text);
      const connection = await this.ensureReady();
      const { introspectSchema, runSql } = await this.duckdb();
      const schema = this.schema ?? await introspectSchema(connection, [...this.sources.keys()]);
      analyze(program, schema);
      const result = await runSql(connection, emitSql(program, schema));
      return formatResult(result, this.format);
    } catch (error) {
      if (error instanceof PlqError) return `error: ${error.message}`;
      return `error: ${error instanceof Error ? error.message : String(error)}`;
    }
  }
}
