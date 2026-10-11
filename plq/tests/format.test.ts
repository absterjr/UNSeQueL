import { mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { runCli } from "../src/cli.js";
import { formatProgram, isFormatted } from "../src/format.js";
import { parseProgram } from "../src/parser.js";

/**
 * Plan step 8 (part 2): the canonical, idempotent formatter.
 */

const REFERENCE = join(__dirname, "..", "examples", "reference");
const COVERAGE = join(__dirname, "..", "examples", "coverage");

const referenceFiles = readdirSync(REFERENCE).filter((f) => f.endsWith(".plq")).sort();
const coverageFiles = readdirSync(COVERAGE).filter((f) => f.endsWith(".plq")).sort();

describe("formatter: corpus", () => {
  it("treats every reference query as canonical", () => {
    expect(referenceFiles).toHaveLength(21);
    for (const file of referenceFiles) {
      const source = readFileSync(join(REFERENCE, file), "utf8").replace(/\r\n/g, "\n");
      expect(formatProgram(source), file).toBe(source);
      expect(isFormatted(source), file).toBe(true);
    }
  });

  it("treats CRLF input as canonical and normalizes on format", () => {
    const lf = "from orders\nsort order_id\n";
    const crlf = lf.replace(/\n/g, "\r\n");
    expect(isFormatted(crlf)).toBe(true);
    expect(formatProgram(crlf)).toBe(lf);
  });

  it("is idempotent and re-parseable on the coverage corpus", () => {
    expect(coverageFiles).toHaveLength(13);
    for (const file of coverageFiles) {
      const once = formatProgram(readFileSync(join(COVERAGE, file), "utf8"));
      const twice = formatProgram(once);
      expect(twice, file).toBe(once);
      parseProgram(once);
    }
  });
});

describe("formatter: rules", () => {
  it("lowercases stage keywords and uppercases expression keywords", () => {
    expect(formatProgram("FROM orders\nFILTER quantity > 1 AND customer IS NOT NULL"))
      .toBe("from orders\nfilter quantity > 1 AND customer IS NOT NULL\n");
  });

  it("canonicalizes sort markers to '-'", () => {
    expect(formatProgram("from orders\nsort order_id asc, unit_price DESC"))
      .toBe("from orders\nsort order_id, -unit_price\n");
  });

  it("keeps derive self-names and drops redundant parentheses", () => {
    expect(formatProgram("from orders\nderive a = a"))
      .toBe("from orders\nderive a = a\n");
    expect(formatProgram("from orders\nfilter (quantity > 1) AND (quantity < 3)"))
      .toBe("from orders\nfilter quantity > 1 AND quantity < 3\n");
  });

  it("preserves needed parentheses", () => {
    expect(formatProgram(
      "from orders\nfilter (quantity > 1 OR quantity < 3) AND unit_price > 0"))
      .toBe("from orders\nfilter (quantity > 1 OR quantity < 3) AND unit_price > 0\n");
  });

  it("re-quotes strings and keeps sql payloads on one line", () => {
    expect(formatProgram('from orders\nfilter customer = "O\'Brien"'))
      .toBe("from orders\nfilter customer = 'O''Brien'\n");
    const sql = formatProgram('from orders\nsql "SELECT a,\\n b FROM __input__"');
    expect(sql.trimEnd().split("\n")).toHaveLength(2);
    expect(sql).toContain("\\n");
    expect(isFormatted(sql)).toBe(true);
  });

  it("keeps group keys bare and aggregates named", () => {
    expect(formatProgram("from orders\ngroup by country\naggregate n = COUNT(*)"))
      .toBe("from orders\ngroup by country\naggregate n = COUNT(*)\n");
  });

  it("keeps consecutive minus signs lexable", () => {
    for (const source of [
      "from orders\nderive a = - -3",
      "from orders\nsort - -quantity",
      "from orders\nwindow rn = ROW_NUMBER() over (order by - -quantity)",
    ]) {
      const once = formatProgram(source);
      expect(once, source).not.toContain("--");
      expect(formatProgram(once), source).toBe(once);
    }
  });

  it("normalizes skip/take spacing and window spelling", () => {
    expect(formatProgram(
      "from orders\nwindow rn = ROW_NUMBER() over (order by quantity DESC)\ntake 3"))
      .toBe("from orders\nwindow rn = ROW_NUMBER() over (order by -quantity)\ntake 3\n");
  });
});

describe("formatter: CLI", () => {
  it("prints the canonical form", async () => {
    const path = join(REFERENCE, "q20_full_pipeline.plq");
    const result = await runCli(["fmt", path]);
    expect(result.code).toBe(0);
    expect(result.stdout).toBe(readFileSync(path, "utf8"));
  });

  it("--check accepts CRLF files", async () => {
    const directory = mkdtempSync(join(tmpdir(), "plq-crlf-"));
    const path = join(directory, "query.plq");
    try {
      writeFileSync(path, "from orders\r\nsort order_id\r\n");
      const result = await runCli(["fmt", path, "--check"]);
      expect(result.code).toBe(0);
    } finally {
      rmSync(directory, { recursive: true, force: true });
    }
  });

  it("--check exits 1 for non-canonical text and 0 after --write", async () => {
    const directory = mkdtempSync(join(tmpdir(), "plq-fmt-"));
    const path = join(directory, "query.plq");
    try {
      writeFileSync(path, "FROM orders\n");
      const bad = await runCli(["fmt", path, "--check"]);
      expect(bad.code).toBe(1);
      const write = await runCli(["fmt", path, "--write"]);
      expect(write.code).toBe(0);
      expect(readFileSync(path, "utf8")).toBe("from orders\n");
      const good = await runCli(["fmt", path, "--check"]);
      expect(good.code).toBe(0);
      expect(good.stdout).toBe("");
    } finally {
      rmSync(directory, { recursive: true, force: true });
    }
  });
});
