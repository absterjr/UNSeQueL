import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { tokenize } from "../src/lexer.js";
import { parseProgram } from "../src/parser.js";

/**
 * Audit batch B4: coverage fixtures for the grammar features the 21 reference
 * queries do not exercise. They are parser fixtures; step 4 parses every one
 * (see parser.test.ts for structural assertions). See
 * examples/coverage/README.md for the feature mapping.
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

  it("every fixture parses into a full program (step 4)", () => {
    for (const file of files) {
      const program = parseProgram(readFileSync(join(COVERAGE, file), "utf8"));
      expect(program[0]?.kind, file).toBe("from");
      program.forEach((stage, index) => expect(stage.index, file).toBe(index + 1));
    }
  });
});
