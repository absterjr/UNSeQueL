#!/usr/bin/env node
/**
 * Build the dependency-free compile exe.
 *
 * Bundles the CLI (check, compile, fmt, lsp) with esbuild and injects it into
 * a copy of the Node runtime with SEA, producing a true single-file
 * executable that needs no Node, no npm, and no DuckDB. `run`/`preview`/`repl`
 * still require the DuckDB addon (see the run-capable distribution in the
 * Python package); everything else works offline in npm-locked environments.
 *
 * Usage (from compiler/): node scripts/build-sea.mjs
 */

import { execFileSync } from "node:child_process";
import { copyFileSync, cpSync, mkdirSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { build } from "esbuild";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const outDir = join(root, "build-exe");
const distDir = join(root, "dist");
mkdirSync(outDir, { recursive: true });
mkdirSync(distDir, { recursive: true });

const bundle = join(outDir, "unsequel.cjs");
await build({
  entryPoints: [join(root, "src", "main.ts")],
  outfile: bundle,
  bundle: true,
  platform: "node",
  format: "cjs",
  target: "node22",
  external: ["@duckdb/node-api"],
  logLevel: "warning",
});

const seaConfig = join(outDir, "sea-config.json");
writeFileSync(seaConfig, JSON.stringify({
  main: bundle,
  output: join(outDir, "unsequel.blob"),
  disableExperimentalSEAWarning: true,
}, null, 2));

execFileSync(process.execPath, ["--experimental-sea-config", seaConfig], { stdio: "inherit" });

const exeName = process.platform === "win32" ? "unsequel-compile.exe" : "unsequel-compile";
const exePath = join(distDir, exeName);
copyFileSync(process.execPath, exePath);

const postjectArgs = [
  exePath,
  "NODE_SEA_BLOB",
  join(outDir, "unsequel.blob"),
  "--sentinel-fuse", "NODE_SEA_FUSE_fce680ab2cc467b6e072b8b5df1996b2",
];
if (process.platform === "darwin") {
  postjectArgs.push("--macho-segment-name", "NODE_SEA");
}
execFileSync(
  process.execPath,
  [join(root, "node_modules", "postject", "dist", "cli.js"), ...postjectArgs],
  { stdio: "inherit" },
);

console.log(`built ${exePath}`);

if (process.argv.includes("--with-duckdb")) {
  // Stage a run-capable, per-platform bundle: the same exe plus the DuckDB
  // native module in `modules/`, which the CLI resolves from next to the
  // binary (see siblingAddon in src/duckdb.ts).
  const stage = join(distDir, "unsequel-run");
  rmSync(stage, { recursive: true, force: true });
  const runExeName = process.platform === "win32" ? "unsequel.exe" : "unsequel";
  const duckdbSource = join(root, "node_modules", "@duckdb");
  const duckdbTarget = join(stage, "modules", "node_modules", "@duckdb");
  mkdirSync(duckdbTarget, { recursive: true });
  copyFileSync(exePath, join(stage, runExeName));
  for (const entry of readdirSync(duckdbSource)) {
    cpSync(join(duckdbSource, entry), join(duckdbTarget, entry), { recursive: true });
  }
  writeFileSync(join(stage, "README.txt"), [
    "UNSeQueL run-capable executable",
    "",
    "This folder is a complete, self-contained UNSeQueL installation:",
    `  ${runExeName}   the CLI (run/check/compile/preview/fmt/repl, and \`lsp\` for editors)`,
    "  modules/      the bundled DuckDB native module",
    "",
    "No Node, npm, or Python is required. Keep the folder together.",
    "",
    "Examples:",
    `  ./${runExeName} --version`,
    `  ./${runExeName} run query.unsequel`,
    "",
    "This build is per-platform; download the archive for your OS.",
    "",
  ].join("\n"));
  console.log(`run-capable bundle staged at ${stage}`);
}
