import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { VERSION, runCli } from "../src/cli.js";

describe("cli (step 1 skeleton)", () => {
  it("keeps VERSION in sync with package.json", () => {
    const pkg = JSON.parse(
      readFileSync(join(__dirname, "..", "package.json"), "utf8"),
    ) as { version: string };
    expect(VERSION).toBe(pkg.version);
  });

  it("starts with no arguments and explains itself", () => {
    const result = runCli([]);
    expect(result.code).toBe(2);
    expect(result.stderr).toContain("usage: plq");
  });

  it("reports its version", () => {
    const result = runCli(["--version"]);
    expect(result.code).toBe(0);
    expect(result.stdout).toBe(`plq ${VERSION}\n`);
  });

  it("prints usage on --help", () => {
    const result = runCli(["--help"]);
    expect(result.code).toBe(0);
    expect(result.stdout).toContain("usage: plq");
  });

  it("rejects unknown commands until steps 2-7 land", () => {
    const result = runCli(["compile", "query.plq"]);
    expect(result.code).toBe(2);
    expect(result.stderr).toContain("not implemented");
  });
});
