/**
 * Verify that every reference example is unchanged by the canonical formatter.
 * Used by CI (`npm run fmt:check`) after `npm run build`.
 */
import { execFileSync } from "node:child_process";
import { readdirSync } from "node:fs";
import { join } from "node:path";

const dir = "examples/reference";
let failed = 0;
for (const file of readdirSync(dir).filter((name) => name.endsWith(".plq")).sort()) {
  try {
    execFileSync(process.execPath, ["dist/main.js", "fmt", join(dir, file), "--check"], {
      stdio: "inherit",
    });
  } catch {
    failed += 1;
  }
}
if (failed > 0) {
  console.error(`formatter check failed for ${failed} file(s)`);
  process.exit(1);
}
console.log("formatter check passed");
