// Copies the compiled CLI/LSP bundle into the VS Code extension so language
// features work without downloading the standalone run bundle. Run `npm run
// build` in compiler/ first (or use `npm run vsix` in editors/vscode).
import { cpSync, existsSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const dist = join(here, "..", "dist");
const target = join(here, "..", "editors", "vscode", "server");
if (!existsSync(dist)) {
  console.error("dist/ not found; run `npm run build` in compiler/ first");
  process.exit(1);
}
rmSync(target, { recursive: true, force: true });
mkdirSync(target, { recursive: true });
cpSync(dist, target, {
  recursive: true,
  filter: (source) => !source.endsWith(".d.ts") && !source.endsWith(".map"),
});
// The CLI bundle is ESM; the extension's package.json is not, so the server
// folder declares its own module type.
writeFileSync(join(target, "package.json"), `${JSON.stringify({ type: "module" }, null, 2)}\n`);
console.log(`copied ${dist} -> ${target}`);
