import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { parseProgram } from "../src/parser.js";

/**
 * Step 2 guard: the reference corpus is the fixed behavioral baseline for the
 * lexer (step 3), parser (step 4), and every later stage of the compiler.
 * These checks keep the corpus and the grammar's stage vocabulary in sync.
 */

const REFERENCE = join(__dirname, "..", "examples", "reference");

const STAGE_KEYWORDS = new Set([
  "from",
  "join",
  "left join",
  "filter",
  "derive",
  "group by",
  "aggregate",
  "having",
  "window",
  "select",
  "sort",
  "skip",
  "take",
  "distinct",
  "sql",
]);

function stageKeyword(line: string): string {
  const lower = line.toLowerCase();
  if (lower.startsWith("left join")) return "left join";
  if (lower.startsWith("group by")) return "group by";
  return lower.split(/\s+/, 1)[0] ?? "";
}

const files = readdirSync(REFERENCE)
  .filter((name) => name.endsWith(".plq"))
  .sort();

describe("reference corpus (step 2)", () => {
  it("contains exactly the 21 validated queries", () => {
    expect(files).toHaveLength(21);
    expect(files[0]).toBe("q01_all_orders.plq");
    expect(files[20]).toBe("q21_sql_hatch.plq");
  });

  it("every query starts with a from stage", () => {
    for (const file of files) {
      const text = readFileSync(join(REFERENCE, file), "utf8");
      const first = text.split("\n").find((line) => line.trim() !== "") ?? "";
      expect(stageKeyword(first), file).toBe("from");
    }
  });

  it("only uses stage keywords from the frozen grammar", () => {
    for (const file of files) {
      const text = readFileSync(join(REFERENCE, file), "utf8");
      for (const line of text.split("\n")) {
        if (line.trim() === "" || line.trimStart().startsWith("#")) continue;
        expect(STAGE_KEYWORDS, `${file}: ${line}`).toContain(stageKeyword(line));
      }
    }
  });

  it("exercises the escape hatch exactly once (q21)", () => {
    const withSql = files.filter((file) =>
      /\bsql\s+"/i.test(readFileSync(join(REFERENCE, file), "utf8")),
    );
    expect(withSql).toEqual(["q21_sql_hatch.plq"]);
  });

  it("every reference query parses into a full program (step 4)", () => {
    for (const file of files) {
      const program = parseProgram(readFileSync(join(REFERENCE, file), "utf8"));
      expect(program.length, file).toBeGreaterThan(0);
      expect(program[0]?.kind, file).toBe("from");
      program.forEach((stage, index) => expect(stage.index, file).toBe(index + 1));
    }
  });
});
