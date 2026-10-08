import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { tokenize } from "../src/lexer.js";

/**
 * Audit batch B4: coverage fixtures for the grammar features the 21 reference
 * queries do not exercise. They are parser fixtures; step 4 must parse every
 * one (the parse wiring is added with the parser). See examples/coverage/README.md
 * for the feature mapping.
 */

const COVERAGE = join(__dirname, "..", "examples", "coverage");

const files = readdirSync(COVERAGE)
  .filter((name) => name.endsWith(".plq"))
  .sort();

describe("coverage corpus (B4)", () => {
  it("ships fixtures for every uncovered grammar feature", () => {
    expect(files).toHaveLength(13);
    expect(files[0]).toBe("c01_aliases.plq");
    expect(files[12]).toBe("c13_unary.plq");
  });

  it("every fixture lexes without errors", () => {
    for (const file of files) {
      const tokens = tokenize(readFileSync(join(COVERAGE, file), "utf8"));
      expect(tokens.at(-1)?.type, file).toBe("eof");
      expect(tokens.length, file).toBeGreaterThan(1);
    }
  });
});
