/**
 * DuckDB backend for `run` and `preview` (plan step 7).
 *
 * Loads CSV/JSON/Parquet sources into a throwaway in-memory database and runs
 * one SQL statement at a time. Only the CLI commands that execute queries need
 * this module; parse, analyze, and compile stay pure.
 */

import { existsSync } from "node:fs";

import { DuckDBInstance, type DuckDBConnection } from "@duckdb/node-api";

import { PlqError } from "./errors.js";
import { parseSchema, type Schema } from "./schema.js";

export type { DuckDBConnection };

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

function readerFor(path: string): string {
  const lowered = path.toLowerCase();
  if (lowered.endsWith(".json")) return `read_json_auto(${quoted(path)})`;
  if (lowered.endsWith(".parquet") || lowered.endsWith(".pq")) {
    return `read_parquet(${quoted(path)})`;
  }
  return `read_csv_auto(${quoted(path)})`;
}

/** Load one CSV/JSON/Parquet file into the connection as table `name`. */
export async function loadTableSource(connection: DuckDBConnection, name: string,
                                      path: string): Promise<void> {
  if (!existsSync(path)) throw new PlqError(`data file not found: ${path}`);
  try {
    await connection.run(
      `CREATE OR REPLACE TABLE ${quotedIdent(name)} AS SELECT * FROM ${readerFor(path)}`);
  } catch (error) {
    throw new PlqError(`could not load data file ${path}: ${(error as Error).message}`);
  }
}

export async function connect(data: ReadonlyMap<string, string>): Promise<DuckDBConnection> {
  let connection: DuckDBConnection;
  try {
    const instance = await DuckDBInstance.create(":memory:");
    connection = await instance.connect();
  } catch (error) {
    throw new PlqError(`DuckDB error: ${(error as Error).message}`);
  }
  for (const [name, path] of data) {
    await loadTableSource(connection, name, path);
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
