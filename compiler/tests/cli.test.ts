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
const Q20 = join(REFERENCE, "q20_full_pipeline.unsequel");
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
    expect(result.stderr).toContain("usage: unsequel");
  });

  it("reports its version", async () => {
    const result = await runCli(["--version"]);
    expect(result.code).toBe(0);
    expect(result.stdout).toBe(`unsequel ${VERSION}\n`);
  });

  it("prints usage on --help", async () => {
    const result = await runCli(["--help"]);
    expect(result.code).toBe(0);
    expect(result.stdout).toContain("usage: unsequel");
  });

  it("rejects unknown commands", async () => {
    const result = await runCli(["frobnicate", "query.unsequel"]);
    expect(result.code).toBe(2);
    expect(result.stderr).toContain("unknown command");
  });
});

describe("cli: check", () => {
  it("parses and validates against a schema", async () => {
    const result = await runCli(["check", Q20, "--schema", SCHEMA]);
    expect(result.code).toBe(0);
    expect(result.stdout).toContain("valid UNSeQueL query (schema OK)");
  });

  it("surfaces stage-located errors", async () => {
    const directory = mkdtempSync(join(tmpdir(), "unsequel-"));
    const path = join(directory, "bad.unsequel");
    writeFileSync(path, "from orders\nfilter missing_column > 1\n");
    try {
      const result = await runCli(["check", path, "--schema", SCHEMA]);
      expect(result.code).toBe(1);
      expect(result.stderr).toContain("stage 2 (filter)");
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
    expect(result.stdout).toContain("FROM stage_8 ORDER BY __unsequel_internal_order_");
  });

  it("truncates at a grammar stage, keeping the group unit whole", async () => {
    const result = await runCli(["compile", Q20, "--schema", SCHEMA, "--stage", "4"]);
    expect(result.code).toBe(0);
    expect(result.stdout).toContain("GROUP BY category");
    expect(result.stdout).not.toContain("stage_5");
  });

  it("explains the schema options when none is available", async () => {
    const result = await runCli(["compile", Q20]);
    expect(result.code).toBe(1);
    expect(result.stderr).toContain("pass --schema");
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

describe("cli: options and data validation", () => {
  it("rejects unknown options per command", async () => {
    const bogus = await runCli(["check", Q20, "--bogus"]);
    expect(bogus.code).toBe(2);
    expect(bogus.stderr).toContain("unknown option --bogus");
    const previewFormat = await runCli([
      "preview", Q20, "--schema", SCHEMA, "--stage", "1", "--format", "json", ...DATA,
    ]);
    expect(previewFormat.code).toBe(2);
    expect(previewFormat.stderr).toContain("unknown option --format");
  });

  it("rejects duplicate --data names", async () => {
    const result = await runCli([
      "run", Q20, "--schema", SCHEMA, "--data", "orders=a.csv", "--data", "orders=b.csv",
    ]);
    expect(result.code).toBe(2);
    expect(result.stderr).toContain("duplicate --data name 'orders'");
  });

  it("requires name=path for dotted data names", async () => {
    const result = await runCli(["run", Q20, "--schema", SCHEMA, "--data", "my.data.csv"]);
    expect(result.code).toBe(2);
    expect(result.stderr).toContain("use --data name=path");
  });

  it("validates the --stage range for compile", async () => {
    const result = await runCli(["compile", Q20, "--schema", SCHEMA, "--stage", "99"]);
    expect(result.code).toBe(1);
    expect(result.stderr).toContain("between 1 and 9");
  });

  it("strips a UTF-8 BOM from query and schema files", async () => {
    const directory = mkdtempSync(join(tmpdir(), "unsequel-bom-"));
    try {
      const queryPath = join(directory, "query.unsequel");
      writeFileSync(queryPath, "\uFEFFfrom orders\n");
      const schemaPath = join(directory, "schema.json");
      writeFileSync(schemaPath, `\uFEFF${readFileSync(SCHEMA, "utf8")}`);
      const result = await runCli(["check", queryPath, "--schema", schemaPath]);
      expect(result.code).toBe(0);
      expect(result.stdout).toContain("valid UNSeQueL query (schema OK)");
    } finally {
      rmSync(directory, { recursive: true, force: true });
    }
  });

  it("reports DuckDB errors as clean error lines", async () => {
    const directory = mkdtempSync(join(tmpdir(), "unsequel-dberr-"));
    try {
      const queryPath = join(directory, "query.unsequel");
      writeFileSync(queryPath,
        'from orders\nsql "SELECT bogus_col FROM __input__"\nselect bogus_col\n');
      const result = await runCli([
        "run", queryPath, "--schema", SCHEMA,
        `--data=orders=${join(ROOT, "examples", "spec", "orders.csv")}`,
      ]);
      expect(result.code).toBe(1);
      expect(result.stderr).toContain("error: DuckDB error:");
      expect(result.stderr).not.toContain("Node.js");
    } finally {
      rmSync(directory, { recursive: true, force: true });
    }
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

  it("preserves leading zeros, literal NULL-like strings, and supports CSV overrides", async () => {
    const directory = mkdtempSync(join(tmpdir(), "unsequel-csv-types-"));
    try {
      const data = join(directory, "codes.csv");
      const query = join(directory, "query.unsequel");
      writeFileSync(data, "postal;label\n02134;None\n00501;Nan\n");
      writeFileSync(query, "from codes\nselect postal, label\n");
      const inferred = await runCli([
        "run", query, "--format", "json", "--delimiter", ";", `--data=codes=${data}`,
      ]);
      expect(inferred.code).toBe(0);
      expect(JSON.parse(inferred.stdout)).toEqual([
        { postal: "02134", label: "None" },
        { postal: "00501", label: "Nan" },
      ]);

      writeFileSync(query, "from codes\nselect postal\n");
      const overridden = await runCli([
        "run", query, "--format", "json", "--delimiter", ";",
        "--column-type", "codes.postal=integer", `--data=codes=${data}`,
      ]);
      expect(overridden.code).toBe(0);
      expect(JSON.parse(overridden.stdout)).toEqual([{ postal: 2134 }, { postal: 501 }]);
    } finally {
      rmSync(directory, { recursive: true, force: true });
    }
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
