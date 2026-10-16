/**
 * Result formatting shared by the CLI and the REPL.
 */

import type { SqlResult } from "./duckdb.js";

export function valueOf(value: unknown): string {
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
  // DECIMAL (and friends): a JSON number at DuckDB's scale when the digits
  // survive the round trip through a JS number, otherwise the exact text.
  const text = String(value);
  const normalized = text.includes(".") ? text.replace(/\.?0+$/, "") : text;
  const num = Number(text);
  if (Number.isFinite(num) && String(num) === normalized) return num;
  return text;
}

export const OUTPUT_FORMATS = ["table", "json", "csv"] as const;

export function formatResult(result: SqlResult, format: string): string {
  if (format === "json") return JSON.stringify(result.rows, jsonReplacer, 2);
  if (format === "csv") return csvOutput(result);
  return `${tableOutput(result)}\n\n${result.rows.length} row(s)`;
}
