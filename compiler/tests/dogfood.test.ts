import { existsSync, readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { DuckDBInstance } from "@duckdb/node-api";
import { emitSql } from "../src/codegen.js";
import { splitDirectives } from "../src/directives.js";
import { formatProgram } from "../src/format.js";
import { parseProgram } from "../src/parser.js";
import { loadSchema } from "../src/schema.js";
import { analyze } from "../src/semantics.js";

/**
 * Plan step 9: dogfooding. The five queries in examples/real/ are real analyst
 * tasks migrated through the CLI. The date and grand-total window examples
 * originally needed the sql hatch; they now exercise native `strftime` and
 * `over ()`. Each file declares its sources with `# source` directives so it
 * runs flagless. This suite parses, validates, formats, and executes each one
 * against the sample dataset.
 */

const ROOT = join(__dirname, "..", "..");
const REAL = join(__dirname, "..", "examples", "real");
const SPEC_DIR = join(__dirname, "..", "examples", "spec");
const SHARED_SPEC = join(ROOT, "examples", "spec");
const SCHEMA = loadSchema(join(ROOT, "examples", "spec", "schema.json"));

const EXPECTED_ROWS: Record<string, number> = {
  r01_top_products: 5,
  r02_segment_revenue: 3,
  r03_repeat_customers: 4,
  r04_monthly_revenue: 6,
  r05_share_of_total: 3,
  r06_composition: 3,
};

const files = readdirSync(REAL).filter((f) => f.endsWith(".unsequel")).sort();

describe("dogfood: real queries", () => {
  it("ships the migrated queries", () => {
    expect(files).toHaveLength(6);
    expect(files.map((f) => f.replace(/\.unsequel$/, ""))).toEqual(Object.keys(EXPECTED_ROWS));
  });

  it("keeps packaged data copies in sync and resolves every source directive", () => {
    for (const file of ["orders.csv", "products.csv", "customers.csv", "members.csv", "schema.json"]) {
      const packaged = readFileSync(join(SPEC_DIR, file), "utf8").replace(/\r\n/g, "\n");
      const shared = readFileSync(join(SHARED_SPEC, file), "utf8").replace(/\r\n/g, "\n");
      expect(packaged, file).toBe(shared);
    }
    for (const file of files) {
      const source = readFileSync(join(REAL, file), "utf8");
      for (const relative of splitDirectives(source).sources.values()) {
        expect(existsSync(join(REAL, relative)), `${file}: ${relative}`).toBe(true);
      }
    }
  });

  it("parses, validates, and stays formatter-idempotent", () => {
    for (const file of files) {
      const source = readFileSync(join(REAL, file), "utf8");
      expect(splitDirectives(source).sources.size, file).toBeGreaterThan(0);
      const program = parseProgram(splitDirectives(source).body);
      analyze(program, SCHEMA);
      const once = formatProgram(source);
      expect(formatProgram(once), file).toBe(once);
      expect(parseProgram(splitDirectives(once).body).length, file).toBe(program.length);
    }
  });

  it("executes every query with the expected shape", async () => {
    const instance = await DuckDBInstance.create(":memory:");
    const connection = await instance.connect();
    const data = join(ROOT, "examples", "spec").replace(/\\/g, "/");
    for (const table of ["orders", "customers", "products", "members"]) {
      await connection.run(
        `CREATE TABLE ${table} AS SELECT * FROM read_csv_auto('${data}/${table}.csv')`);
    }

    const results = new Map<string, Array<Record<string, unknown>>>();
    for (const file of files) {
      const name = file.replace(/\.unsequel$/, "");
      const source = readFileSync(join(REAL, file), "utf8");
      const program = parseProgram(splitDirectives(source).body);
      const reader = await connection.runAndReadAll(emitSql(program, SCHEMA));
      results.set(name, reader.getRowObjectsJS() as Array<Record<string, unknown>>);
    }

    for (const [name, expected] of Object.entries(EXPECTED_ROWS)) {
      expect(results.get(name)?.length, name).toBe(expected);
    }

    const top = results.get("r01_top_products") ?? [];
    expect(top[0]?.["product_name"]).toBe("Desk Lamp");
    expect(Number(top[0]?.["revenue"])).toBeCloseTo(168);

    const repeat = results.get("r03_repeat_customers") ?? [];
    expect(repeat.map((row) => row["customer"])).toEqual([
      "Ada", "Grace", "Alan", "Katherine",
    ]);
  });
});
