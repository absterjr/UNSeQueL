import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { VERSION, runCli, type CliResult } from "../src/cli.js";

/**
 * Plan step 7: the check / compile / preview / run CLI.
 */

const ROOT = join(__dirname, "..", "..");
const REFERENCE = join(__dirname, "..", "examples", "reference");
const SCHEMA = join(ROOT, "examples", "spec", "schema.json");
const Q20 = join(REFERENCE, "q20_full_pipeline.plq");
const DATA = [
  `--data=orders=${join(ROOT, "examples", "spec", "orders.csv")}`,
  `--data=products=${join(ROOT, "examples", "spec", "products.csv")}`,
];

function version(): string {
  const pkg = JSON.parse(
    readFileSync(join(__dirname, "..", "package.json"), "utf8"),
  ) as { version: string };
  return pkg.version;
}

describe("cli: basics", () => {
  it("keeps VERSION in sync with package.json", () => {
    expect(VERSION).toBe(version());
  });

  it("starts with no arguments and explains itself", async () => {
    const result = await runCli([]);
    expect(result.code).toBe(2);
    expect(result.stderr).toContain("usage: plq");
  });

  it("reports its version", async () => {
    const result = await runCli(["--version"]);
    expect(result.code).toBe(0);
    expect(result.stdout).toBe(`plq ${VERSION}\n`);
  });

  it("prints usage on --help", async () => {
    const result = await runCli(["--help"]);
    expect(result.code).toBe(0);
    expect(result.stdout).toContain("usage: plq");
  });

  it("rejects unknown commands", async () => {
    const result = await runCli(["frobnicate", "query.plq"]);
    expect(result.code).toBe(2);
    expect(result.stderr).toContain("unknown command");
  });
});

describe("cli: check", () => {
  it("parses and validates against a schema", async () => {
    const result = await runCli(["check", Q20, "--schema", SCHEMA]);
    expect(result.code).toBe(0);
    expect(result.stdout).toContain("valid PLQ query (schema OK)");
  });

  it("surfaces stage-located errors", async () => {
    const directory = mkdtempSync(join(tmpdir(), "plq-"));
    const path = join(directory, "bad.plq");
    writeFileSync(path, "from orders\ntake 5\nfilter quantity > 1\n");
    try {
      const result = await runCli(["check", path]);
      expect(result.code).toBe(1);
      expect(result.stderr).toContain("stage 3 (filter)");
    } finally {
      rmSync(directory, { recursive: true, force: true });
    }
  });
});

describe("cli: compile", () => {
  it("prints the generated SQL", async () => {
    const result = await runCli(["compile", Q20, "--schema", SCHEMA]);
    expect(result.code).toBe(0);
    expect(result.stdout).toContain("WITH stage_1 AS (");
    expect(result.stdout).toContain("SELECT * FROM stage_8;");
  });

  it("truncates at a grammar stage, keeping the group unit whole", async () => {
    const result = await runCli(["compile", Q20, "--schema", SCHEMA, "--stage", "4"]);
    expect(result.code).toBe(0);
    expect(result.stdout).toContain("GROUP BY category");
    expect(result.stdout).not.toContain("stage_5");
  });

  it("requires a schema", async () => {
    const result = await runCli(["compile", Q20]);
    expect(result.code).toBe(2);
    expect(result.stderr).toContain("--schema is required");
  });
});

describe("cli: preview", () => {
  it("runs the program truncated at a stage and labels it", async () => {
    const result = await runCli([
      "preview", Q20, "--schema", SCHEMA, "--stage", "3", "--limit", "3", ...DATA,
    ]);
    expect(result.code).toBe(0);
    expect(result.stdout).toContain("stage 3/9  (derive)");
    expect(result.stdout).toContain("line_total");
    expect(result.stdout).toContain("3 row(s)");
  });

  it("rejects an out-of-range stage", async () => {
    const result = await runCli([
      "preview", Q20, "--schema", SCHEMA, "--stage", "99", ...DATA,
    ]);
    expect(result.code).toBe(1);
    expect(result.stderr).toContain("between 1 and 9");
  });

  it("requires data", async () => {
    const result = await runCli(["preview", Q20, "--schema", SCHEMA, "--stage", "1"]);
    expect(result.code).toBe(2);
    expect(result.stderr).toContain("--data");
  });
});

describe("cli: run", () => {
  it("executes the whole program as JSON", async () => {
    const result = await runCli([
      "run", Q20, "--schema", SCHEMA, "--format", "json", ...DATA,
    ]);
    expect(result.code).toBe(0);
    const rows = JSON.parse(result.stdout) as Array<Record<string, unknown>>;
    expect(rows).toHaveLength(3);
    expect(rows[0]?.["category"]).toBe("home");
  });

  it("reports a missing data file", async () => {
    const result = await runCli([
      "run", Q20, "--schema", SCHEMA, "--data", "orders=does_not_exist.csv",
      "--data", "products=does_not_exist.csv",
    ]);
    expect(result.code).toBe(1);
    expect(result.stderr).toContain("data file not found");
  });
});
