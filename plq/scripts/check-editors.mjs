import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const editorDir = join(root, "editors", "vscode");

function fail(message) {
  console.error(`editors check failed: ${message}`);
  process.exit(1);
}

function readJson(relative) {
  try {
    return JSON.parse(readFileSync(join(root, relative), "utf8"));
  } catch (error) {
    return fail(`${relative}: ${error.message}`);
  }
}

const pkg = readJson("editors/vscode/package.json");
if (pkg.contributes?.languages?.[0]?.id !== "unsequel") {
  fail("package.json must contribute the 'unsequel' language");
}
const commands = (pkg.contributes?.commands ?? []).map((command) => command.command);
for (const required of ["unsequel.runSelection", "unsequel.runFile", "unsequel.openRepl"]) {
  if (!commands.includes(required)) fail(`package.json missing command ${required}`);
}

readJson("editors/vscode/language-configuration.json");
for (const grammar of pkg.contributes?.grammars ?? []) {
  const relative = `editors/vscode/${String(grammar.path).replace(/^\.\//, "")}`;
  if (!existsSync(join(root, relative))) fail(`missing grammar file ${relative}`);
  const tm = readJson(relative);
  for (const key of ["scopeName", "patterns", "repository"]) {
    if (!(key in tm)) fail(`${relative} missing ${key}`);
  }
  const visit = (node) => {
    if (node === null || typeof node !== "object") return;
    if (Array.isArray(node)) {
      for (const item of node) visit(item);
      return;
    }
    if (typeof node.match === "string") {
      const pattern = node.match.startsWith("(?i)") ? node.match.slice(4) : node.match;
      try {
        new RegExp(pattern);
      } catch (error) {
        fail(`${relative}: invalid regex ${node.match} (${error.message})`);
      }
    }
    for (const value of Object.values(node)) visit(value);
  };
  visit(tm);
}

if (!existsSync(join(editorDir, "extension.js"))) fail("missing editors/vscode/extension.js");
console.log("editors check passed");
