/**
 * DuckDB backend for `run` and `preview` (plan step 7).
 *
 * Loads CSV/JSON/Parquet sources into a throwaway in-memory database and runs
 * one SQL statement at a time. Only the CLI commands that execute queries need
 * this module; parse, analyze, and compile stay pure.
 */

import { closeSync, existsSync, openSync, readSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";

import { PlqError } from "./errors.js";
import { parseSchema, type Schema } from "./schema.js";

type DuckDBApi = typeof import("@duckdb/node-api");
export type DuckDBConnection = import("@duckdb/node-api").DuckDBConnection;

let cachedApi: DuckDBApi | null = null;

const openInstances: Array<{ closeSync(): void }> = [];
const openConnections: Array<{ closeSync(): void }> = [];

/**
 * Release every DuckDB connection and instance this process opened. Closing
 * the connections first is what actually releases the file lock.
 */
export function closeDuckDB(): void {
  while (openConnections.length > 0) {
    try {
      openConnections.pop()?.closeSync();
    } catch {
      // already closed
    }
  }
  while (openInstances.length > 0) {
    try {
      openInstances.pop()?.closeSync();
    } catch {
      // already closed
    }
  }
}

/**
 * The native addon loads lazily. Single-file run executables ship it in a
 * `modules/` directory next to the binary; inside SEA a plain import cannot
 * see the filesystem, so a `createRequire` anchored there is the fallback.
 */
function siblingAddon(): DuckDBApi | null {
  try {
    const modulesDir = join(dirname(process.execPath), "modules");
    if (!existsSync(join(modulesDir, "node_modules", "@duckdb", "node-api"))) {
      return null;
    }
    const anchor = createRequire(join(modulesDir, "index.js"));
    return anchor("@duckdb/node-api") as DuckDBApi;
  } catch {
    return null;
  }
}

async function duckdbApi(): Promise<DuckDBApi> {
  if (cachedApi !== null) return cachedApi;
  let reason = "";
  try {
    cachedApi = await import("@duckdb/node-api");
  } catch (error) {
    reason = error instanceof Error ? error.message : String(error);
    cachedApi = siblingAddon();
  }
  if (cachedApi === null) {
    throw new PlqError(
      "run, preview, repl, and database features need the DuckDB engine. "
      + "With npm: `npm i @duckdb/node-api`. Without npm: use the run bundle "
      + "from the releases (unsequel-run-<platform>.tar.gz ships the module in "
      + "modules/ next to the executable). check, fmt, and compile without "
      + `sources work without it. Details: ${reason}`,
    );
  }
  return cachedApi;
}

export type Row = Record<string, unknown>;

export interface SqlResult {
  columns: string[];
  rows: Row[];
}

function quoted(text: string): string {
  return `'${text.replace(/'/g, "''")}'`;
}

function quotedIdent(name: string): string {
  return `"${name.replace(/"/g, '""')}"`;
}

function csvRecords(text: string, delimiter: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let quotedField = false;
  for (let index = text.charCodeAt(0) === 0xfeff ? 1 : 0; index < text.length;) {
    const char = text[index] as string;
    if (quotedField) {
      if (char === '"' && text[index + 1] === '"') {
        field += '"';
        index += 2;
      } else if (char === '"') {
        quotedField = false;
        index += 1;
      } else {
        field += char;
        index += 1;
      }
      continue;
    }
    if (char === '"' && field === "") {
      quotedField = true;
      index += 1;
    } else if (char === delimiter) {
      row.push(field);
      field = "";
      index += 1;
    } else if (char === "\n" || char === "\r") {
      row.push(field);
      if (!(row.length === 1 && row[0] === "")) rows.push(row);
      row = [];
      field = "";
      if (char === "\r" && text[index + 1] === "\n") index += 2;
      else index += 1;
    } else {
      field += char;
      index += 1;
    }
  }
  if (quotedField) throw new PlqError("CSV file has an unterminated quoted field");
  if (row.length > 0 || field !== "") {
    row.push(field);
    rows.push(row);
  }
  return rows;
}

const HEADER_BYTES = 1 << 16;

/** The CSV header, validated: present, with no empty or duplicate names. */
function csvHeader(path: string, delimiter: string): string[] {
  const descriptor = openSync(path, "r");
  let sample: Buffer;
  try {
    sample = Buffer.alloc(HEADER_BYTES);
    sample = sample.subarray(0, readSync(descriptor, sample, 0, HEADER_BYTES, 0));
  } finally {
    closeSync(descriptor);
  }
  const header = csvRecords(sample.toString("utf8"), delimiter)[0];
  if (header === undefined || header.length === 0) {
    throw new PlqError(`CSV file has no header: ${path}`);
  }
  const seen = new Set<string>();
  for (const column of header) {
    if (column.trim() === "") throw new PlqError(`CSV file has an empty column name: ${path}`);
    const lowered = column.toLowerCase();
    if (seen.has(lowered)) throw new PlqError(`CSV file has duplicate column '${column}': ${path}`);
    seen.add(lowered);
  }
  return header;
}

/** The only types the CSV sniffer may pick; Python's inference uses the same set. */
const CSV_TYPE_CANDIDATES = "auto_type_candidates=['BIGINT', 'DOUBLE', 'DATE', 'TIMESTAMP', 'VARCHAR']";

/**
 * Whole-file CSV typing by DuckDB's own sniffer (`sample_size=-1`), so a late
 * zero-padded ZIP or a late 'N/A' decides the column's type. Dates must be ISO;
 * booleans, times and timestamps with an offset stay text. One correction:
 * DuckDB types integers too wide for BIGINT as DOUBLE, which drops digits, so
 * those columns stay text.
 */
async function inferCsvTypes(connection: DuckDBConnection, path: string, delimiter: string,
                             overrides: ReadonlyMap<string, string>): Promise<Map<string, string>> {
  const header = csvHeader(path, delimiter);
  const known = new Set(header.map((column) => column.toLowerCase()));
  for (const name of overrides.keys()) {
    if (!known.has(name.toLowerCase())) {
      throw new PlqError(`column type override refers to unknown column '${name}' in ${path}`);
    }
  }
  const csv = (options: string): string =>
    `read_csv_auto(${quoted(path)}, delim=${quoted(delimiter)}, header=true, ${options})`;
  const types = new Map<string, string>();
  try {
    const sniffed = await connection.runAndReadAll(`DESCRIBE SELECT * FROM ${csv(
      `sample_size=-1, ${CSV_TYPE_CANDIDATES}, dateformat='%Y-%m-%d'`)}`);
    for (const row of sniffed.getRowObjectsJS()) {
      types.set(String(row["column_name"]), String(row["column_type"]));
    }
    const doubles = [...types].filter(([, type]) => type === "DOUBLE").map(([column]) => column);
    if (doubles.length > 0) {
      const checks = doubles.map((column, i) => {
        const u = `ltrim(trim(${quotedIdent(column)}), '+-')`;
        return `bool_or(CASE WHEN length(${u}) >= 16 THEN regexp_full_match(${u}, '\\d+') `
          + `ELSE false END) AS w${i}`;
      });
      const wide = (await connection.runAndReadAll(
        `SELECT ${checks.join(", ")} FROM ${csv("all_varchar=true")}`)).getRowObjectsJS()[0];
      doubles.forEach((column, i) => {
        if (wide?.[`w${i}`] === true) types.set(column, "VARCHAR");
      });
    }
  } catch (error) {
    throw new PlqError(`could not read ${path}: ${(error as Error).message}`);
  }
  for (const column of types.keys()) {
    const override = overrides.get(column.toLowerCase());
    if (override !== undefined) types.set(column, override);
  }
  return types;
}

function csvTypeMap(types: ReadonlyMap<string, string>): string {
  return `{${[...types].map(([column, type]) => `${quoted(column)}: ${quoted(type)}`).join(", ")}}`;
}

async function readerFor(connection: DuckDBConnection, path: string, delimiter = ",",
                         overrides: ReadonlyMap<string, string> = new Map()): Promise<string> {
  const lowered = path.toLowerCase();
  if (lowered.endsWith(".json")) {
    const columns = overrides.size === 0 ? "" : `, columns=${csvTypeMap(overrides)}`;
    return `read_json_auto(${quoted(path)}${columns})`;
  }
  if (lowered.endsWith(".parquet") || lowered.endsWith(".pq")) {
    if (overrides.size > 0) throw new PlqError("--column-type cannot be used with Parquet input");
    return `read_parquet(${quoted(path)})`;
  }
  const types = await inferCsvTypes(connection, path, delimiter, overrides);
  return `read_csv_auto(${quoted(path)}, delim=${quoted(delimiter)}, header=true, `
    + `types=${csvTypeMap(types)})`;
}

export interface SourceOptions {
  delimiter?: string;
  columnTypes?: ReadonlyMap<string, ReadonlyMap<string, string>>;
}

export interface ConnectOptions extends SourceOptions {
  /** Open a file-backed database instead of the throwaway in-memory one. */
  databasePath?: string;
  /** `ATTACH` existing `.duckdb` files under these names. */
  attach?: ReadonlyMap<string, string>;
  /** Writes only: allow creating `databasePath` when it does not exist (`--create-db`). */
  createDatabase?: boolean;
  /**
   * Open the database read-write (for `create`/`into`/`import` programs) and
   * allow creating a missing file. Read-only remains the default.
   */
  writable?: boolean;
}

/** Load one CSV/JSON/Parquet file into the connection as a **temporary
 * table**, so file sources never clobber or persist over tables in a
 * file-backed database. */
export async function loadTableSource(connection: DuckDBConnection, name: string,
                                      path: string, options: SourceOptions = {}): Promise<void> {
  if (!existsSync(path)) throw new PlqError(`data file not found: ${path}`);
  try {
    const overrides = options.columnTypes?.get(name) ?? new Map<string, string>();
    await connection.run(
      `CREATE OR REPLACE TEMP TABLE ${quotedIdent(name)} AS SELECT * FROM `
      + `${await readerFor(connection, path, options.delimiter, overrides)}`);
  } catch (error) {
    throw new PlqError(`could not load data file ${path}: ${(error as Error).message}`);
  }
}

/** Materialize a file as a **persistent** table (the `import` command). */
export async function importTableSource(connection: DuckDBConnection, name: string,
                                        path: string, options: SourceOptions = {},
                                        replace = false): Promise<void> {
  if (!existsSync(path)) throw new PlqError(`data file not found: ${path}`);
  try {
    const overrides = options.columnTypes?.get(name) ?? new Map<string, string>();
    await connection.run(
      `CREATE ${replace ? "OR REPLACE " : ""}TABLE ${quotedIdent(name)} AS SELECT * FROM `
      + `${await readerFor(connection, path, options.delimiter, overrides)}`);
  } catch (error) {
    const exists = alreadyExists(error);
    if (exists !== null) {
      throw new PlqError(`table '${exists.name}' already exists; pass --replace to overwrite it`);
    }
    throw new PlqError(`could not import ${path}: ${(error as Error).message}`);
  }
}

/** A readable message for a database that would not open, naming the lock holder. */
function openError(path: string | undefined, error: unknown): string {
  const message = (error as Error)?.message ?? String(error);
  if (path !== undefined && message.includes("Could not set lock")) {
    const pid = /\(PID (\d+)\)/.exec(message)?.[1];
    return `database ${path} is in use by another process${pid ? ` (PID ${pid})` : ""}; `
      + "try again when it finishes";
  }
  return `DuckDB error: ${message}`;
}

/** Kind and name from DuckDB's "Table/View with name X already exists" error, else null. */
export function alreadyExists(error: unknown): { kind: string; name: string } | null {
  const match = /(Table|View) with name "?([^"\s!]+)"? already exists/i
    .exec((error as Error)?.message ?? "");
  return match === null ? null
    : { kind: (match[1] as string).toLowerCase(), name: match[2] as string };
}

/** Execute a (possibly multi-statement) script for create/into programs. */
export async function runStatements(connection: DuckDBConnection, sql: string): Promise<void> {
  try {
    // All statements commit together or not at all (DuckDB DDL is transactional).
    await connection.run("BEGIN TRANSACTION");
    await connection.run(sql);
    await connection.run("COMMIT");
  } catch (error) {
    try {
      await connection.run("ROLLBACK");
    } catch {
      // no transaction was open
    }
    const exists = alreadyExists(error);
    if (exists !== null) {
      throw new PlqError(`${exists.kind} '${exists.name}' already exists; use `
        + `'create or replace ${exists.kind} ${exists.name} = (...)' to overwrite it`);
    }
    throw new PlqError(`DuckDB error: ${(error as Error).message}`);
  }
}

export async function connect(data: ReadonlyMap<string, string>,
                              options: ConnectOptions = {}): Promise<DuckDBConnection> {
  const { DuckDBInstance } = await duckdbApi();
  if (options.databasePath !== undefined && !existsSync(options.databasePath)) {
    if (!options.writable) throw new PlqError(`database not found: ${options.databasePath}`);
    if (!options.createDatabase) {
      throw new PlqError(`database not found: ${options.databasePath}; `
        + "pass --create-db to create a new database file");
    }
  }
  let connection: DuckDBConnection;
  try {
    const instance = await DuckDBInstance.create(
      options.databasePath ?? ":memory:",
      options.databasePath === undefined || options.writable
        ? {} : { access_mode: "READ_ONLY" },
    );
    openInstances.push(instance);
    connection = await instance.connect();
    openConnections.push(connection);
  } catch (error) {
    throw new PlqError(openError(options.databasePath, error));
  }
  for (const [name, path] of options.attach ?? []) {
    if (!existsSync(path)) {
      throw new PlqError(`database to attach not found: ${path}`);
    }
    try {
      await connection.run(
        `ATTACH ${quoted(path)} AS ${quotedIdent(name)} (READ_ONLY)`);
    } catch (error) {
      throw new PlqError(`could not attach database ${path}: ${(error as Error).message}`);
    }
  }
  for (const [name, path] of data) {
    await loadTableSource(connection, name, path, options);
  }
  return connection;
}

export async function runSql(connection: DuckDBConnection, sql: string): Promise<SqlResult> {
  try {
    const reader = await connection.runAndReadAll(sql);
    const columns = reader.columnNames();
    const rows = reader.getRowObjectsJS() as Row[];
    // Temporal values use DuckDB's own text ("2024-01-01 00:00:00.123456"), as the
    // Python track prints them; a JS Date would add a 'Z' and drop microseconds.
    const temporal = reader.columnTypes().map((type) => /^(DATE|TIME)/.test(String(type)));
    if (temporal.some(Boolean)) {
      const raw = reader.getRowObjects();
      rows.forEach((row, r) => {
        columns.forEach((name, i) => {
          if (temporal[i] && row[name] !== null) row[name] = String(raw[r]?.[name]);
        });
      });
    }
    return { columns, rows };
  } catch (error) {
    throw new PlqError(`DuckDB error: ${(error as Error).message}`);
  }
}

/** Build a Schema from the live database (used when no schema file is given). */
export async function introspectSchema(connection: DuckDBConnection,
                                       names: readonly string[]): Promise<Schema> {
  const tables: Record<string, { columns: Record<string, string> }> = {};
  for (const name of names) {
    try {
      const reader = await connection.runAndReadAll(`DESCRIBE ${quotedIdent(name)}`);
      const rows = reader.getRowObjectsJS() as Array<{
        column_name: string; column_type: string;
      }>;
      tables[name] = {
        columns: Object.fromEntries(rows.map((row) => [row.column_name, row.column_type])),
      };
    } catch (error) {
      throw new PlqError(`could not introspect table ${name}: ${(error as Error).message}`);
    }
  }
  return parseSchema({ tables });
}

/**
 * Build a Schema for everything the connection can see: persistent tables,
 * attached databases, and the temporary tables from file sources. Attached
 * databases are addressable as `<name>.<table>`.
 */
export async function introspectDatabase(connection: DuckDBConnection): Promise<Schema> {
  try {
    const reader = await connection.runAndReadAll("SHOW ALL TABLES");
    const rows = reader.getRowObjectsJS() as Array<{
      database: string;
      schema: string;
      name: string;
      column_names: string[];
      column_types: string[];
      temporary: boolean;
    }>;
    const currentReader = await connection.runAndReadAll(
      "SELECT current_database() AS name");
    const current = String(
      (currentReader.getRowObjectsJS()[0] as { name: unknown } | undefined)?.name ?? "");
    const tables: Record<string, { columns: Record<string, string> }> = {};
    const temporaryNames = new Set(
      rows.filter((row) => row.temporary).map((row) => row.name));
    for (const row of rows) {
      if (!row.temporary && row.database === current && row.schema === "main"
          && temporaryNames.has(row.name)) {
        continue; // the temporary source shadows the persistent table
      }
      let key: string;
      if (row.temporary) {
        key = row.name;
      } else if (row.database === current) {
        key = row.schema === "main" ? row.name : `${row.schema}.${row.name}`;
      } else {
        key = row.schema === "main"
          ? `${row.database}.${row.name}`
          : `${row.database}.${row.schema}.${row.name}`;
      }
      tables[key] = {
        columns: Object.fromEntries(
          row.column_names.map((column, index) => [column, row.column_types[index] as string]),
        ),
      };
    }
    return parseSchema({ tables });
  } catch (error) {
    throw new PlqError(`could not introspect the database: ${(error as Error).message}`);
  }
}
