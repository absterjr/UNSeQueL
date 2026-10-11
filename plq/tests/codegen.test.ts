import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { beforeAll, describe, expect, it } from "vitest";
import { DuckDBInstance, type DuckDBConnection } from "@duckdb/node-api";
import { loweringUnits } from "../src/ast.js";
import { CodegenError, emitSql } from "../src/codegen.js";
import { parseProgram } from "../src/parser.js";
import { loadSchema, parseSchema, type Schema } from "../src/schema.js";
import { analyze } from "../src/semantics.js";

/**
 * Plan step 6: DuckDB SQL codegen, one CTE per lowering unit.
 *
 * Correctness is covered two ways, as the plan requires:
 * 1. golden files — generated SQL text matches tests/golden/<name>.sql;
 * 2. execution parity — generated SQL and the hand-written equivalent in
 *    tests/handwritten/ return identical rows when run in DuckDB.
 */

const ROOT = join(__dirname, "..", "..");
const REFERENCE = join(__dirname, "..", "examples", "reference");
const COVERAGE = join(__dirname, "..", "examples", "coverage");
const GOLDEN = join(__dirname, "golden");
const HANDWRITTEN = join(__dirname, "handwritten");
const ROOT_SCHEMA = join(ROOT, "examples", "spec", "schema.json");
const SCHEMA = loadSchema(ROOT_SCHEMA);

// The coverage corpus includes a qualified source and a non-dataset column;
// extend the repository schema for those fixtures (same as semantics tests).
const EXTENDED: Schema = (() => {
  const raw = JSON.parse(readFileSync(ROOT_SCHEMA, "utf8")) as {
    tables: Record<string, { columns: Record<string, string> }>;
  };
  raw.tables["analytics.orders"] = raw.tables["orders"] as { columns: Record<string, string> };
  (raw.tables["orders"] as { columns: Record<string, string> }).columns["shipped"] = "boolean";
  return parseSchema(raw);
})();

const files = readdirSync(REFERENCE).filter((f) => f.endsWith(".plq")).sort();

function programFor(name: string) {
  return parseProgram(readFileSync(join(REFERENCE, `${name}.plq`), "utf8"));
}

function generate(name: string): string {
  return emitSql(programFor(name), SCHEMA);
}

describe("codegen: golden files", () => {
  it("matches the golden SQL for every reference query", () => {
    expect(files).toHaveLength(21);
    for (const file of files) {
      const name = file.replace(/\.plq$/, "");
      const expected = readFileSync(join(GOLDEN, `${name}.sql`), "utf8").trimEnd();
      expect(generate(name).trimEnd(), name).toBe(expected);
    }
  });

  it("emits one CTE per lowering unit and selects the last", () => {
    for (const file of files) {
      const name = file.replace(/\.plq$/, "");
      const program = programFor(name);
      const sql = emitSql(program, SCHEMA);
      const units = loweringUnits(program);
      expect(sql.match(/ AS \(\n/g)?.length ?? 0, name).toBe(units.length);
      expect(sql.trimEnd().endsWith(`SELECT * FROM stage_${units.length};`), name).toBe(true);
    }
  });

  it("shares one CTE for group by + aggregate", () => {
    const sql = generate("q09_group_count");
    expect(sql).toContain("GROUP BY country");
    expect(sql.match(/ AS \(\n/g)?.length).toBe(3); // from, group unit, sort
  });

  it("keeps the whole unit when truncated at group by", () => {
    const program = programFor("q20_full_pipeline");
    const atGroup = emitSql(program, SCHEMA, { stopAt: 4 });
    expect(atGroup).toContain("GROUP BY category");
    expect(atGroup.trimEnd().endsWith("SELECT * FROM stage_4;")).toBe(true);
    const atDerive = emitSql(program, SCHEMA, { stopAt: 3 });
    expect(atDerive).not.toContain("GROUP BY");
    const onlyFrom = emitSql(program, SCHEMA, { stopAt: 1 });
    expect(onlyFrom.trimEnd().endsWith("SELECT * FROM stage_1;")).toBe(true);
  });
});

describe("codegen: errors", () => {
  it("reports unknown tables with the stage", () => {
    expect(() => emitSql(parseProgram("from mystery_table"), SCHEMA))
      .toThrow(/stage 1 \(from\): unknown table 'mystery_table'/);
  });

  it("rejects deriving over a live column", () => {
    expect(() => emitSql(parseProgram("from orders\nderive order_id = quantity"), SCHEMA))
      .toThrow(/conflicts with a live column/);
  });

  it("inlines earlier derived names within one derive stage", () => {
    const program = parseProgram("from orders\nderive a = quantity * 2, b = a + 1");
    const sql = emitSql(program, SCHEMA);
    expect(sql).toContain("(quantity * 2) AS a");
    expect(sql).toContain("((quantity * 2) + 1) AS b");
  });

  it("preserves identifiers abutting __input__", () => {
    const program = parseProgram(
      'from orders\nsql "SELECT col__input__, __input__.order_id FROM __input__"');
    const sql = emitSql(program, SCHEMA);
    expect(sql).toContain("col__input__");
    expect(sql).toContain("stage_1.order_id");
  });

  it("rejects an invalid stopAt", () => {
    expect(() => emitSql(parseProgram("from orders"), SCHEMA, { stopAt: 0 }))
      .toThrow(CodegenError);
  });
});

describe("codegen: corpus agreement", () => {
  it("lowers every fixture that parses and analyzes (check => compile)", () => {
    const fixtures: Array<{ file: string; schema: Schema }> = [
      ...readdirSync(REFERENCE).filter((f) => f.endsWith(".plq"))
        .map((file) => ({ file: join(REFERENCE, file), schema: SCHEMA })),
      ...readdirSync(COVERAGE).filter((f) => f.endsWith(".plq"))
        .map((file) => ({ file: join(COVERAGE, file), schema: EXTENDED })),
    ];
    expect(fixtures).toHaveLength(34);
    for (const { file, schema } of fixtures) {
      const program = parseProgram(readFileSync(file, "utf8"));
      analyze(program, schema);
      expect(() => emitSql(program, schema), file).not.toThrow();
    }
  });
});

function norm(value: unknown): string {
  if (value === null || value === undefined) return "null";
  if (typeof value === "bigint") return value.toString();
  if (value instanceof Date) return value.toISOString();
  if (typeof value === "number") return String(Math.round(value * 1e9) / 1e9);
  if (typeof value === "object") {
    const text = String(value); // DuckDBDecimal and friends
    const num = Number(text);
    return Number.isFinite(num) ? String(Math.round(num * 1e9) / 1e9) : text;
  }
  return String(value);
}

async function rows(connection: DuckDBConnection, sql: string): Promise<string[]> {
  const reader = await connection.runAndReadAll(sql);
  const objects = reader.getRowObjectsJS() as Array<Record<string, unknown>>;
  return objects
    .map((row) => JSON.stringify(
      Object.keys(row).sort().map((key) => [key, norm(row[key])])))
    .sort();
}

describe("codegen: DuckDB execution parity", () => {
  let connection: DuckDBConnection;

  beforeAll(async () => {
    const instance = await DuckDBInstance.create(":memory:");
    connection = await instance.connect();
    const data = join(ROOT, "examples", "spec").replace(/\\/g, "/");
    for (const table of ["orders", "customers", "products", "members"]) {
      await connection.run(
        `CREATE TABLE ${table} AS SELECT * FROM read_csv_auto('${data}/${table}.csv')`);
    }
  });

  it("generated SQL returns the same rows as the handwritten equivalents", async () => {
    for (const file of files) {
      const name = file.replace(/\.plq$/, "");
      const generated = await rows(connection, generate(name));
      const handwrittenSql = readFileSync(join(HANDWRITTEN, `${name}.sql`), "utf8");
      const handwritten = await rows(connection, handwrittenSql);
      expect(generated, name).toEqual(handwritten);
    }
  });

  it("executes same-stage derive chains", async () => {
    const program = parseProgram(
      "from orders\nderive a = quantity * 2, b = a + 1\n"
      + "select order_id, a, b\nsort order_id\ntake 1");
    const reader = await connection.runAndReadAll(emitSql(program, SCHEMA));
    const result = reader.getRowObjectsJS() as Array<Record<string, unknown>>;
    expect(result).toEqual([{ order_id: 5001n, a: 20n, b: 21n }]);
  });
});
