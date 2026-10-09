/**
 * Schema files for pre-execution validation (plan step 5).
 *
 * A schema is a hand-written JSON file describing the tables a program may
 * read:
 *
 *     {
 *       "tables": {
 *         "orders": {
 *           "columns": { "order_id": "integer", "unit_price": "number" }
 *         }
 *       }
 *     }
 *
 * Column type strings collapse into families: numeric, text, boolean,
 * temporal, or unknown. Anything unrecognised is `unknown` and never triggers
 * a type error.
 */

import { readFileSync } from "node:fs";

import { SchemaError } from "./errors.js";

export type TypeFamily = "numeric" | "text" | "boolean" | "temporal" | "unknown";

const NUMERIC = new Set([
  "integer", "int", "bigint", "number", "numeric", "float", "double", "real", "decimal",
]);
const TEXT = new Set(["string", "text", "varchar", "char"]);
const BOOLEAN = new Set(["bool", "boolean"]);
const TEMPORAL = new Set(["date", "time", "timestamp", "datetime"]);

export function typeFamily(name: string | null | undefined): TypeFamily {
  if (name === null || name === undefined) return "unknown";
  const key = name.trim().toLowerCase();
  if (NUMERIC.has(key)) return "numeric";
  if (TEXT.has(key)) return "text";
  if (BOOLEAN.has(key)) return "boolean";
  if (TEMPORAL.has(key)) return "temporal";
  return "unknown";
}

export interface SchemaTable {
  name: string;
  /** column -> declared type string */
  readonly columns: ReadonlyMap<string, string>;
}

export interface Schema {
  readonly tables: ReadonlyMap<string, SchemaTable>;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function parseSchema(data: unknown, origin = "<schema>"): Schema {
  if (!isRecord(data) || !("tables" in data)) {
    throw new SchemaError(`${origin}: schema must be an object with a 'tables' key`);
  }
  const rawTables = data["tables"];
  if (!isRecord(rawTables)) {
    throw new SchemaError(`${origin}: 'tables' must be an object`);
  }
  const tables = new Map<string, SchemaTable>();
  for (const [tableName, body] of Object.entries(rawTables)) {
    if (!isRecord(body) || !isRecord(body["columns"])) {
      throw new SchemaError(`${origin}: table '${tableName}' needs a 'columns' object`);
    }
    const columns = new Map<string, string>();
    for (const [columnName, columnType] of Object.entries(body["columns"])) {
      if (typeof columnType !== "string") {
        throw new SchemaError(`${origin}: ${tableName}.${columnName} type must be a string`);
      }
      columns.set(columnName, columnType);
    }
    tables.set(tableName, { name: tableName, columns });
  }
  return { tables };
}

export function loadSchema(path: string): Schema {
  let text: string;
  try {
    text = readFileSync(path, "utf8");
  } catch {
    throw new SchemaError(`schema file not found: ${path}`);
  }
  let data: unknown;
  try {
    data = JSON.parse(text);
  } catch (error) {
    throw new SchemaError(`${path}: invalid JSON (${(error as Error).message})`);
  }
  return parseSchema(data, path);
}
