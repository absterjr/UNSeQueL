/**
 * DuckDB backend for `run` and `preview` (plan step 7).
 *
 * Loads CSV/JSON/Parquet sources into a throwaway in-memory database and runs
 * one SQL statement at a time. Only the CLI commands that execute queries need
 * this module; parse, analyze, and compile stay pure.
 */

import { existsSync, readFileSync } from "node:fs";
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
  try {
    cachedApi = await import("@duckdb/node-api");
  } catch {
    cachedApi = siblingAddon();
  }
  if (cachedApi === null) {
    throw new PlqError(
      "this command executes queries and needs the DuckDB native module; "
      + "install it with `npm i @duckdb/node-api` "
      + "(check, compile, and fmt work without it)",
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

function inferredCsvTypes(path: string, delimiter: string,
                          overrides: ReadonlyMap<string, string>): Map<string, string> {
  const rows = csvRecords(readFileSync(path, "utf8"), delimiter);
  const header = rows[0];
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
  const types = new Map<string, string>();
  for (const [index, column] of header.entries()) {
    const override = overrides.get(column.toLowerCase());
    if (override !== undefined) {
      types.set(column, override);
      continue;
    }
    const values = rows.slice(1).map((row, rowIndex) => {
      if (row.length !== header.length) {
        throw new PlqError(
          `CSV row ${rowIndex + 2} has ${row.length} fields; expected ${header.length}: ${path}`);
      }
      return row[index] as string;
    }).filter((value) => value.trim() !== "");
    if (values.length === 0) {
      types.set(column, "VARCHAR");
      continue;
    }
    if (values.every((value) => /^\d{4}-\d{2}-\d{2}$/.test(value.trim()))) {
      types.set(column, "DATE");
      continue;
    }
    if (values.every((value) =>
      /^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?$/.test(value.trim()))) {
      types.set(column, "TIMESTAMP");
      continue;
    }
    const numbers = values.map((value) => {
      const trimmed = value.trim();
      if (/^[+-]?0\d+$/.test(trimmed)) return null;
      const number = Number(trimmed);
      return Number.isFinite(number) && Math.abs(number) <= Number.MAX_SAFE_INTEGER
        ? number : null;
    });
    if (numbers.every((value) => value !== null)) {
      types.set(column, numbers.every((value) => Number.isSafeInteger(value)) ? "BIGINT" : "DOUBLE");
    } else {
      types.set(column, "VARCHAR");
    }
  }
  for (const name of overrides.keys()) {
    if (!seen.has(name.toLowerCase())) {
      throw new PlqError(`column type override refers to unknown column '${name}' in ${path}`);
    }
  }
  return types;
}

function csvTypeMap(types: ReadonlyMap<string, string>): string {
  return `{${[...types].map(([column, type]) => `${quoted(column)}: ${quoted(type)}`).join(", ")}}`;
}

function readerFor(path: string, delimiter = ",",
                   overrides: ReadonlyMap<string, string> = new Map()): string {
  const lowered = path.toLowerCase();
  if (lowered.endsWith(".json")) {
    const columns = overrides.size === 0 ? "" : `, columns=${csvTypeMap(overrides)}`;
    return `read_json_auto(${quoted(path)}${columns})`;
  }
  if (lowered.endsWith(".parquet") || lowered.endsWith(".pq")) {
    if (overrides.size > 0) throw new PlqError("--column-type cannot be used with Parquet input");
    return `read_parquet(${quoted(path)})`;
  }
  const types = inferredCsvTypes(path, delimiter, overrides);
  return `read_csv_auto(${quoted(path)}, delim=${quoted(delimiter)}, types=${csvTypeMap(types)})`;
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
}

/**
 * Load one CSV/JSON/Parquet file into the connection as a **temporary
 * table**, so file sources never clobber or persist over tables in a
 * file-backed database.
 */
export async function loadTableSource(connection: DuckDBConnection, name: string,
                                      path: string, options: SourceOptions = {}): Promise<void> {
  if (!existsSync(path)) throw new PlqError(`data file not found: ${path}`);
  try {
    const overrides = options.columnTypes?.get(name) ?? new Map<string, string>();
    await connection.run(
      `CREATE OR REPLACE TEMP TABLE ${quotedIdent(name)} AS SELECT * FROM `
      + `${readerFor(path, options.delimiter, overrides)}`);
  } catch (error) {
    throw new PlqError(`could not load data file ${path}: ${(error as Error).message}`);
  }
}

export async function connect(data: ReadonlyMap<string, string>,
                              options: ConnectOptions = {}): Promise<DuckDBConnection> {
  const { DuckDBInstance } = await duckdbApi();
  let connection: DuckDBConnection;
  try {
    const instance = await DuckDBInstance.create(options.databasePath ?? ":memory:");
    openInstances.push(instance);
    connection = await instance.connect();
    openConnections.push(connection);
  } catch (error) {
    throw new PlqError(`DuckDB error: ${(error as Error).message}`);
  }
  for (const [name, path] of options.attach ?? []) {
    if (!existsSync(path)) {
      throw new PlqError(`database to attach not found: ${path}`);
    }
    try {
      await connection.run(`ATTACH ${quoted(path)} AS ${quotedIdent(name)}`);
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
    return {
      columns: reader.columnNames(),
      rows: reader.getRowObjectsJS() as Row[],
    };
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
      name: string;
      column_names: string[];
      column_types: string[];
    }>;
    const currentReader = await connection.runAndReadAll(
      "SELECT current_database() AS name");
    const current = String(
      (currentReader.getRowObjectsJS()[0] as { name: unknown } | undefined)?.name ?? "");
    const tables: Record<string, { columns: Record<string, string> }> = {};
    for (const row of rows) {
      const key = row.database === current || row.database === "temp"
        ? row.name
        : `${row.database}.${row.name}`;
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
