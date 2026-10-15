import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { DuckDBInstance } from "@duckdb/node-api";
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

describe("cli: output sinks", () => {
  it("writes results with --out (csv, parquet, json)", async () => {
    const directory = mkdtempSync(join(tmpdir(), "unsequel-out-"));
    try {
      const csv = join(directory, "result.csv");
      const csvRun = await runCli([
        "run", Q20, "--schema", SCHEMA, "--out", csv, ...DATA,
      ]);
      expect(csvRun.code).toBe(0);
      expect(csvRun.stdout).toContain(`wrote ${csv}`);
      const csvText = readFileSync(csv, "utf8");
      expect(csvText.split("\n")[0]).toBe("category,orders,revenue,avg_order");
      expect(csvText).toContain("home");

      const parquet = join(directory, "result.parquet");
      const parquetRun = await runCli([
        "run", Q20, "--schema", SCHEMA, "--out", parquet, ...DATA,
      ]);
      expect(parquetRun.code).toBe(0);
      const parquetBytes = readFileSync(parquet);
      expect(parquetBytes.subarray(0, 4).toString("ascii")).toBe("PAR1");
      expect(parquetBytes.length).toBeGreaterThan(100);

      const json = join(directory, "result.json");
      const jsonRun = await runCli([
        "run", Q20, "--schema", SCHEMA, "--out", json, ...DATA,
      ]);
      expect(jsonRun.code).toBe(0);
      const jsonText = readFileSync(json, "utf8").trim();
      expect(jsonText.split("\n").length).toBeGreaterThanOrEqual(1);
      expect(JSON.parse(jsonText.split("\n")[0] as string)).toHaveProperty("category");
    } finally {
      rmSync(directory, { recursive: true, force: true });
    }
  });

  it("rejects an unsupported --out extension", async () => {
    const result = await runCli([
      "run", Q20, "--schema", SCHEMA, "--out", "result.xlsx", ...DATA,
    ]);
    expect(result.code).toBe(2);
    expect(result.stderr).toContain("--out must end in");
  });
});

describe("cli: databases", () => {
  it("attaches an existing .duckdb file and queries it", async () => {
    const directory = mkdtempSync(join(tmpdir(), "unsequel-attach-"));
    try {
      const dbPath = join(directory, "library.duckdb");
      const instance = await DuckDBInstance.create(dbPath);
      const connection = await instance.connect();
      await connection.run(
        "CREATE TABLE books AS SELECT * FROM "
        + "(VALUES (1, 'Dune'), (2, 'Emma')) AS t(id, title)");
      connection.closeSync();
      instance.closeSync();

      const query = join(directory, "query.unsequel");
      writeFileSync(query, "from library.books\nselect title\nsort title\n");
      const result = await runCli(["run", query, "--attach", `library=${dbPath}`]);
      expect(result.code).toBe(0);
      expect(result.stdout).toContain("Dune");
      expect(result.stdout).toContain("Emma");

      // Aggregation over the attached table works too.
      writeFileSync(query, "from library.books\ngroup by id\naggregate n = COUNT(*)\nsort id\n");
      const counted = await runCli([
        "run", query, "--attach", `library=${dbPath}`, "--format", "json",
      ]);
      expect(counted.code).toBe(0);
      expect(JSON.parse(counted.stdout)).toEqual([
        { id: 1, n: 1 },
        { id: 2, n: 1 },
      ]);
    } finally {
      try {
        rmSync(directory, { recursive: true, force: true });
      } catch {
        // the database lock may outlive the test on Windows
      }
    }
  });

  it("reports a missing database instead of creating one", async () => {
    const directory = mkdtempSync(join(tmpdir(), "unsequel-nodb-"));
    try {
      const missing = join(directory, "typo.duckdb");
      const query = join(directory, "query.unsequel");
      writeFileSync(query, "from keep\nselect x\n");
      const result = await runCli(["run", query, "--db", missing]);
      expect(result.code).toBe(1);
      expect(result.stderr).toContain("database not found");
      expect(existsSync(missing)).toBe(false);
    } finally {
      rmSync(directory, { recursive: true, force: true });
    }
  });

  it("writes a schema snapshot for editors", async () => {
    const directory = mkdtempSync(join(tmpdir(), "unsequel-snapshot-"));
    try {
      const dbPath = join(directory, "library.duckdb");
      const instance = await DuckDBInstance.create(dbPath);
      const connection = await instance.connect();
      await connection.run("CREATE TABLE books AS SELECT 1 AS id, 'Dune' AS title");
      connection.closeSync();
      instance.closeSync();

      const result = await runCli(["schema", "--db", dbPath]);
      expect(result.code).toBe(0);
      const snapshotPath = `${dbPath}.schema.json`;
      expect(result.stdout).toContain(snapshotPath);
      expect(existsSync(snapshotPath)).toBe(true);
      const snapshot = JSON.parse(readFileSync(snapshotPath, "utf8")) as {
        tables: Record<string, { columns: Record<string, string> }>;
      };
      expect(snapshot.tables["books"]?.columns).toEqual({ id: "INTEGER", title: "VARCHAR" });
    } finally {
      try {
        rmSync(directory, { recursive: true, force: true });
      } catch {
        // the database lock may outlive the test on Windows
      }
    }
  });

  it("loads CSVs larger than the sampling window", async () => {
    const directory = mkdtempSync(join(tmpdir(), "unsequel-bigcsv-"));
    try {
      const large = join(directory, "big.csv");
      const lines = ["id,label"];
      for (let index = 0; index < 120_000; index += 1) {
        lines.push(`${index},label_${index}`);
      }
      writeFileSync(large, `${lines.join("\n")}\n`);
      const query = join(directory, "query.unsequel");
      writeFileSync(query, "# source big: big.csv\nfrom big\nselect id\nsort id\ntake 5\n");
      const result = await runCli(["run", query, "--format", "json"]);
      expect(result.code).toBe(0);
      expect(JSON.parse(result.stdout)).toEqual([
        { id: 0 }, { id: 1 }, { id: 2 }, { id: 3 }, { id: 4 },
      ]);
    } finally {
      rmSync(directory, { recursive: true, force: true });
    }
  });

  it("keeps a file-backed database and never clobbers persistent tables", async () => {
    const directory = mkdtempSync(join(tmpdir(), "unsequel-db-"));
    try {
      const dbPath = join(directory, "work.duckdb");
      const instance = await DuckDBInstance.create(dbPath);
      const connection = await instance.connect();
      await connection.run("CREATE TABLE keep AS SELECT 7 AS x");
      connection.closeSync();
      instance.closeSync();

      const persistent = join(directory, "persistent.unsequel");
      writeFileSync(persistent, "from keep\nselect x\n");
      const first = await runCli(["run", persistent, "--db", dbPath]);
      expect(first.code).toBe(0);
      expect(first.stdout).toContain("7");

      const orders = join(ROOT, "examples", "spec", "orders.csv").replace(/\\/g, "/");
      const sourced = join(directory, "sourced.unsequel");
      writeFileSync(sourced, `# source orders: ${orders}\nfrom orders\nselect order_id\nsort order_id\ntake 1\n`);
      const second = await runCli(["run", sourced, "--db", dbPath]);
      expect(second.code).toBe(0);
      expect(second.stdout).toContain("5001");

      // The persistent table survived; the file source loaded as a temp view.
      const verify = await DuckDBInstance.create(dbPath);
      const verifyConnection = await verify.connect();
      const reader = await verifyConnection.runAndReadAll(
        "SELECT table_name FROM duckdb_tables()");
      const names = reader.getRowObjectsJS().map((row) => String(row["table_name"]));
      expect(names).toContain("keep");
      expect(names).not.toContain("orders");
      verifyConnection.closeSync();
      verify.closeSync();
    } finally {
      try {
        rmSync(directory, { recursive: true, force: true });
      } catch {
        // the database lock may outlive the test on Windows
      }
    }
  });
});
