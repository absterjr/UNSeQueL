import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { runCli } from "../src/cli.js";
import { splitDirectives } from "../src/directives.js";
import { formatProgram, isFormatted } from "../src/format.js";
import { ReplSession } from "../src/repl.js";

/**
 * SQL-like execution model: leading file directives, selection ranges, and
 * the interactive REPL session.
 */

const ROOT = join(__dirname, "..", "..");
const ORDERS = join(ROOT, "examples", "spec", "orders.csv");
const SCHEMA = join(ROOT, "examples", "spec", "schema.json");

describe("directives", () => {
  it("extracts sources and schema from the leading lines", () => {
    const text = "# source orders: ../data/orders.csv\n"
      + "# source products: ../data/products.csv\n"
      + "# schema: ../data/schema.json\n"
      + "from orders\nselect *\n";
    const directives = splitDirectives(text);
    expect([...directives.sources]).toEqual([
      ["orders", "../data/orders.csv"],
      ["products", "../data/products.csv"],
    ]);
    expect(directives.schemaPath).toBe("../data/schema.json");
    expect(directives.body).toBe("from orders\nselect *\n");
    expect(isFormatted(text)).toBe(true);
  });

  it("an ordinary comment ends the directive block", () => {
    const directives = splitDirectives("# note\n# source a: b\nfrom orders\n");
    expect(directives.sources.size).toBe(0);
    expect(directives.schemaPath).toBeNull();
    expect(directives.body.startsWith("# note")).toBe(true);
  });

  it("formatProgram preserves directives and stays idempotent", () => {
    const text = "# source orders: o.csv\nFROM orders\nselect *\n";
    const formatted = formatProgram(text);
    expect(formatted).toBe("# source orders: o.csv\nfrom orders\nselect *\n");
    expect(formatProgram(formatted)).toBe(formatted);
  });
});

describe("repl session", () => {
  it("loads a source and runs a query with an introspected schema", async () => {
    const session = new ReplSession();
    expect(await session.handleLine(`.load orders=${ORDERS}`)).toBe("loaded orders");
    expect(session.sourceNames()).toEqual(["orders"]);
    await session.handleLine("from orders");
    await session.handleLine("group by country");
    await session.handleLine("aggregate n = COUNT(*)");
    const out = await session.handleLine("");
    expect(out).toContain("United Kingdom");
    expect(out).toContain("3 row(s)");
  });

  it("executes on ';' and honors .format json", async () => {
    const session = new ReplSession({ sources: new Map([["orders", ORDERS]]) });
    expect(await session.handleLine(".format json")).toBe("format: json");
    const out = await session.handleLine("from orders | take 1;");
    const rows = JSON.parse(out) as Array<Record<string, unknown>>;
    expect(rows).toHaveLength(1);
    expect(rows[0]?.["order_id"]).toBe(5001);
  });

  it("reports errors and keeps the session alive", async () => {
    const session = new ReplSession({ sources: new Map([["orders", ORDERS]]) });
    expect(await session.handleLine("from nope;")).toContain("error:");
    expect(await session.handleLine("from orders | take 1;")).toContain("1 row(s)");
  });

  it("handles dot commands", async () => {
    const session = new ReplSession();
    expect(await session.handleLine(".sources")).toBe("(no sources)");
    expect(await session.handleLine(".help")).toContain(".load");
    expect(await session.handleLine(".nope")).toContain("unknown command");
    expect(await session.handleLine(".load oops")).toContain("name=path");
    expect(await session.handleLine(".format xml")).toContain("error:");
    await session.handleLine(".quit");
    expect(session.done).toBe(true);
  });

  it("executes the pending buffer with .run", async () => {
    const session = new ReplSession({ sources: new Map([["orders", ORDERS]]) });
    await session.handleLine("from orders");
    await session.handleLine("take 1");
    expect(session.prompt()).toBe("...> ");
    const out = await session.handleLine(".run");
    expect(out).toContain("1 row(s)");
    expect(session.prompt()).toBe("unsequel> ");
  });

  it("accepts pasted transcripts with prompt prefixes", async () => {
    const session = new ReplSession({ sources: new Map([["orders", ORDERS]]) });
    expect(await session.handleLine("unsequel> .sources")).toContain("orders");
    await session.handleLine("unsequel> from orders");
    await session.handleLine("...> take 1");
    const out = await session.handleLine("");
    expect(out).toContain("1 row(s)");
  });

  it("warns when quitting with a pending query", async () => {
    const session = new ReplSession();
    await session.handleLine("from orders");
    expect(await session.handleLine(".quit")).toContain("discarded 1 pending line(s)");
    expect(session.done).toBe(true);
  });

  it("clears the pending buffer", async () => {
    const session = new ReplSession();
    await session.handleLine("from orders");
    expect(session.pendingLines).toBe(1);
    await session.handleLine(".clear");
    expect(session.pendingLines).toBe(0);
  });

  it("accepts an explicit .schema file", async () => {
    const session = new ReplSession({
      sources: new Map([["orders", ORDERS]]),
      schemaPath: SCHEMA,
    });
    const out = await session.handleLine("from orders | take 1;");
    expect(out).toContain("1 row(s)");
  });
});

describe("cli: selection and schema-less execution", () => {
  it("runs only the selected line range", async () => {
    const directory = mkdtempSync(join(tmpdir(), "plq-lines-"));
    const path = join(directory, "two_queries.unsequel");
    try {
      writeFileSync(path, "from orders\ntake 1\n\nfrom orders\ntake 2\n");
      const first = await runCli([
        "run", path, "--lines", "1:2", "--format", "json", `--data=orders=${ORDERS}`,
      ]);
      expect(first.code).toBe(0);
      expect((JSON.parse(first.stdout) as unknown[]).length).toBe(1);

      const second = await runCli([
        "run", path, "--lines", "4:5", "--format", "json", `--data=orders=${ORDERS}`,
      ]);
      expect(second.code).toBe(0);
      expect((JSON.parse(second.stdout) as unknown[]).length).toBe(2);

      const invalid = await runCli(["run", path, "--lines", "5:1", `--data=orders=${ORDERS}`]);
      expect(invalid.code).toBe(2);
      expect(invalid.stderr).toContain("--lines");
    } finally {
      rmSync(directory, { recursive: true, force: true });
    }
  });

  it("keeps file directives when --lines excludes them", async () => {
    const directory = mkdtempSync(join(tmpdir(), "plq-lines2-"));
    const path = join(directory, "two.unsequel");
    try {
      writeFileSync(path,
        `# source orders: ${ORDERS}\n`
        + "from orders\ngroup by country\naggregate n = COUNT(*)\nsort country\n\n"
        + "from orders\nfilter quantity >= 5\nselect order_id, customer, quantity\n"
        + "sort order_id\n");
      const second = await runCli(["run", path, "--lines", "7:10", "--format", "json"]);
      expect(second.code).toBe(0);
      expect((JSON.parse(second.stdout) as unknown[]).length).toBe(6);

      const first = await runCli(["run", path, "--lines", "2:5", "--format", "json"]);
      expect(first.code).toBe(0);
      expect((JSON.parse(first.stdout) as unknown[]).length).toBe(3);
    } finally {
      rmSync(directory, { recursive: true, force: true });
    }
  });

  it("runs and compiles without --schema by introspecting sources", async () => {
    const query = join(__dirname, "..", "examples", "reference", "q01_all_orders.unsequel");
    const result = await runCli(["run", query, "--format", "json", `--data=orders=${ORDERS}`]);
    expect(result.code).toBe(0);
    expect((JSON.parse(result.stdout) as unknown[]).length).toBe(15);

    const compiled = await runCli(["compile", query, `--data=orders=${ORDERS}`]);
    expect(compiled.code).toBe(0);
    expect(compiled.stdout).toContain("SELECT * FROM orders");
  });

  it("honors '# source' directives so files are self-contained", async () => {
    const directory = mkdtempSync(join(tmpdir(), "plq-self-"));
    const path = join(directory, "self.unsequel");
    try {
      writeFileSync(path, `# source orders: ${ORDERS}\nfrom orders\ntake 1\n`);
      const result = await runCli(["run", path, "--format", "json"]);
      expect(result.code).toBe(0);
      expect((JSON.parse(result.stdout) as unknown[]).length).toBe(1);
    } finally {
      rmSync(directory, { recursive: true, force: true });
    }
  });

  it("honors a '# schema:' directive for check", async () => {
    const directory = mkdtempSync(join(tmpdir(), "plq-schema-"));
    const path = join(directory, "checked.unsequel");
    try {
      writeFileSync(path, `# schema: ${SCHEMA}\nfrom orders\ntake 1\n`);
      const result = await runCli(["check", path]);
      expect(result.code).toBe(0);
      expect(result.stdout).toContain("(schema OK)");
    } finally {
      rmSync(directory, { recursive: true, force: true });
    }
  });
});
