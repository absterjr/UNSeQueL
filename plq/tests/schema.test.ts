import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { PlqError, SchemaError } from "../src/errors.js";
import { loadSchema, parseSchema, typeFamily } from "../src/schema.js";

/**
 * Plan step 5 (part 1): the schema loader and its type families.
 */

const ROOT_SCHEMA = join(__dirname, "..", "..", "examples", "spec", "schema.json");

describe("schema: type families", () => {
  it("maps declared types to families", () => {
    expect(typeFamily("integer")).toBe("numeric");
    expect(typeFamily("NUMBER")).toBe("numeric");
    expect(typeFamily("varchar")).toBe("text");
    expect(typeFamily("boolean")).toBe("boolean");
    expect(typeFamily("timestamp")).toBe("temporal");
    expect(typeFamily("geography")).toBe("unknown");
    expect(typeFamily(null)).toBe("unknown");
  });
});

describe("schema: loading", () => {
  it("loads the repository schema", () => {
    const schema = loadSchema(ROOT_SCHEMA);
    expect([...schema.tables.keys()].sort())
      .toEqual(["customers", "members", "orders", "products"]);
    const orders = schema.tables.get("orders");
    expect(orders?.columns.size).toBe(7);
    expect(orders?.columns.get("quantity")).toBe("integer");
  });

  it("rejects malformed schemas with plain SchemaErrors", () => {
    const badInputs: unknown[] = [
      { nope: {} },
      { tables: { t: { columns: "bad" } } },
      { tables: { t: { columns: { x: 3 } } } },
    ];
    for (const bad of badInputs) {
      try {
        parseSchema(bad);
        throw new Error("expected SchemaError");
      } catch (error) {
        expect(error).toBeInstanceOf(SchemaError);
        expect(error).toBeInstanceOf(PlqError);
        // schema errors are file errors, not source positions
        expect((error as Error).message).not.toContain("at line");
      }
    }
  });

  it("reports a missing schema file", () => {
    expect(() => loadSchema(join(ROOT_SCHEMA, "..", "missing.json")))
      .toThrow(/schema file not found/);
  });

  it("keeps the source text available for malformed JSON", () => {
    const raw = readFileSync(ROOT_SCHEMA, "utf8");
    expect(raw).toContain("\"tables\"");
  });
});
