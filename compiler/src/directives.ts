/**
 * Leading file directives for self-contained `.unsequel` files:
 *
 *     # source orders: ../data/orders.csv
 *     # schema: ../data/schema.json
 *     from orders
 *     ...
 *
 * Directives must be the first lines of the file (no blank line or ordinary
 * comment before them). Paths are resolved relative to the query file's
 * directory. The formatter preserves directives; every other comment is
 * dropped.
 */

export interface Directives {
  sources: Map<string, string>;
  schemaPath: string | null;
  /** `# db: path` — a file-backed DuckDB database (the connection target). */
  databasePath: string | null;
  /** Canonical directive lines (each with a trailing newline), or "". */
  prefix: string;
  /** The program text with the directive lines removed. */
  body: string;
}

const SOURCE = /^#\s*source\s+([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.+)$/;
const SCHEMA = /^#\s*schema\s*:\s*(.+)$/;
const DATABASE = /^#\s*db\s*:\s*(.+)$/;

export function splitDirectives(text: string): Directives {
  const lines = text.split(/\r\n|\n/);
  const sources = new Map<string, string>();
  const prefixLines: string[] = [];
  let schemaPath: string | null = null;
  let databasePath: string | null = null;
  let index = 0;

  for (; index < lines.length; index += 1) {
    const trimmed = (lines[index] as string).trim();
    if (!trimmed.startsWith("#")) break;
    const source = SOURCE.exec(trimmed);
    if (source !== null) {
      const name = source[1] as string;
      const path = (source[2] as string).trim();
      sources.set(name, path);
      prefixLines.push(`# source ${name}: ${path}`);
      continue;
    }
    const schema = SCHEMA.exec(trimmed);
    if (schema !== null) {
      schemaPath = (schema[1] as string).trim();
      prefixLines.push(`# schema: ${schemaPath}`);
      continue;
    }
    const database = DATABASE.exec(trimmed);
    if (database !== null) {
      databasePath = (database[1] as string).trim();
      prefixLines.push(`# db: ${databasePath}`);
      continue;
    }
    break; // an ordinary comment ends the directive block
  }

  return {
    sources,
    schemaPath,
    databasePath,
    prefix: prefixLines.length > 0 ? `${prefixLines.join("\n")}\n` : "",
    body: lines.slice(index).join("\n"),
  };
}
