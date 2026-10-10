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

export async function connect(data: ReadonlyMap<string, string>): Promise<DuckDBConnection> {
  const instance = await DuckDBInstance.create(":memory:");
  const connection = await instance.connect();
  for (const [name, path] of data) {
    if (!existsSync(path)) throw new PlqError(`data file not found: ${path}`);
    await connection.run(
      `CREATE TABLE ${quotedIdent(name)} AS SELECT * FROM ${readerFor(path)}`);
  }
  return connection;
}

export async function runSql(connection: DuckDBConnection, sql: string): Promise<SqlResult> {
  const reader = await connection.runAndReadAll(sql);
  return {
    columns: reader.columnNames(),
    rows: reader.getRowObjectsJS() as Row[],
  };
}
