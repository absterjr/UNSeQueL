import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { PipelineError, PlqError } from "../src/errors.js";
import { parseProgram } from "../src/parser.js";
import { loadSchema, parseSchema, type Schema } from "../src/schema.js";
import { SemanticError, analyze } from "../src/semantics.js";

/**
 * Plan step 5 (part 2): schema-aware validation and per-stage lineage.
 */

const ROOT = join(__dirname, "..", "..");
const REFERENCE = join(__dirname, "..", "examples", "reference");
const COVERAGE = join(__dirname, "..", "examples", "coverage");
const ROOT_SCHEMA = join(ROOT, "examples", "spec", "schema.json");

const SCHEMA: Schema = loadSchema(ROOT_SCHEMA);

// The coverage corpus includes a qualified source (analytics.orders) and a
// non-dataset column (shipped); extend the repository schema for those
// parser fixtures rather than weakening the checks.
const EXTENDED: Schema = (() => {
  const raw = JSON.parse(readFileSync(ROOT_SCHEMA, "utf8")) as {
    tables: Record<string, { columns: Record<string, string> }>;
  };
  raw.tables["analytics.orders"] = raw.tables["orders"] as { columns: Record<string, string> };
  (raw.tables["orders"] as { columns: Record<string, string> }).columns["shipped"] = "boolean";
  return parseSchema(raw);
})();

function semanticError(text: string, schema: Schema = SCHEMA): SemanticError {
  try {
    analyze(parseProgram(text), schema);
  } catch (error) {
    expect(error).toBeInstanceOf(SemanticError);
    expect(error).toBeInstanceOf(PipelineError);
    expect(error).toBeInstanceOf(PlqError);
    return error as SemanticError;
  }
  throw new Error(`expected a SemanticError for: ${text}`);
}

describe("semantics: reference corpus", () => {
  const files = readdirSync(REFERENCE).filter((f) => f.endsWith(".plq")).sort();

  it("analyzes all 21 reference queries", () => {
    expect(files).toHaveLength(21);
    for (const file of files) {
      const program = parseProgram(readFileSync(join(REFERENCE, file), "utf8"));
      const lineage = analyze(program, SCHEMA);
      expect(lineage.length, file).toBe(program.length);
      expect(lineage[0]?.keyword, file).toBe("from");
    }
  });

  it("tracks lineage for q01 and the q07 join collision rename", () => {
    const q01 = analyze(
      parseProgram(readFileSync(join(REFERENCE, "q01_all_orders.plq"), "utf8")), SCHEMA);
    expect(q01[0]?.columns).toHaveLength(7);

    const q07 = analyze(
      parseProgram(readFileSync(join(REFERENCE, "q07_inner_join.plq"), "utf8")), SCHEMA);
    const joined = q07[1]?.columns ?? [];
    expect(joined).toContain("products_product_id");
    expect(joined).toContain("product_name");
    expect(joined).toHaveLength(11);
  });

  it("tracks the group boundary for q09 and q12", () => {
    const q09 = analyze(
      parseProgram(readFileSync(join(REFERENCE, "q09_group_count.plq"), "utf8")), SCHEMA);
    expect(q09[1]?.columns).toEqual(["country"]);
    expect(q09[2]?.columns).toEqual(["country", "orders"]);

    const q12 = analyze(
      parseProgram(readFileSync(join(REFERENCE, "q12_post_group_metric.plq"), "utf8")), SCHEMA);
    expect(q12[q12.length - 1]?.columns).toContain("avg_order");
  });

  it("marks everything after the hatch opaque (q21)", () => {
    const q21 = analyze(
      parseProgram(readFileSync(join(REFERENCE, "q21_sql_hatch.plq"), "utf8")), SCHEMA);
    expect(q21[1]?.columns).toEqual(["<raw sql>"]);
    expect(q21[2]?.columns).toEqual(["<opaque>"]);
  });
});

describe("semantics: coverage corpus", () => {
  it("analyzes all 13 fixtures against an extended schema", () => {
    const files = readdirSync(COVERAGE).filter((f) => f.endsWith(".plq")).sort();
    expect(files).toHaveLength(13);
    for (const file of files) {
      const lineage = analyze(
        parseProgram(readFileSync(join(COVERAGE, file), "utf8")), EXTENDED);
      expect(lineage.length, file).toBeGreaterThan(0);
    }
  });
});

describe("semantics: invalid programs", () => {
  it("rejects an unknown table", () => {
    const error = semanticError("from warehouse_events");
    expect(error.stageIndex).toBe(1);
    expect(error.stageKeyword).toBe("from");
    expect(error.message).toContain("unknown table 'warehouse_events'");
  });

  it("rejects an unknown joined table", () => {
    const error = semanticError("from orders\njoin vendors on a = vendors.id");
    expect(error.stageKeyword).toBe("join");
    expect(error.message).toContain("unknown joined table 'vendors'");
  });

  it("rejects unknown columns, bare and qualified", () => {
    expect(semanticError("from orders\nfilter shipping_cost > 10").message)
      .toContain("unknown column 'shipping_cost'");
    expect(semanticError("from orders\njoin members on orders.plan = members.plan").message)
      .toContain("orders.plan");
  });

  it("rejects SUM over a text column", () => {
    const error = semanticError(
      "from orders\ngroup by country\naggregate bad = SUM(customer)");
    expect(error.stageKeyword).toBe("aggregate");
    expect(error.message).toContain("numeric");
  });

  it("rejects row references after the group unit", () => {
    const error = semanticError(
      "from orders\ngroup by country\naggregate n = COUNT(*)\nhaving quantity > 1");
    expect(error.stageKeyword).toBe("having");
    expect(error.message).toContain("unknown column 'quantity'");
  });

  it("rejects deriving over a live column", () => {
    const error = semanticError("from orders\nderive order_id = quantity");
    expect(error.message).toContain("conflicts with a live column");
  });

  it("rejects qualified names that misuse an alias", () => {
    const error = semanticError(
      "from orders as o\njoin products as p on o.product_id = p.product_id\n"
      + "select o.order_id, products.product_name");
    expect(error.message).toContain("products.product_name");
  });

  it("rejects unknown window references", () => {
    const error = semanticError(
      "from orders\nwindow rn = ROW_NUMBER() over (partition by region)");
    expect(error.stageKeyword).toBe("window");
    expect(error.message).toContain("region");
  });

  it("skips checks after the hatch", () => {
    const lineage = analyze(
      parseProgram('from orders\nsql "SELECT 1 AS x"\nfilter bogus > 1'), SCHEMA);
    expect(lineage[2]?.columns).toEqual(["<opaque>"]);
  });
});
