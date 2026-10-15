#!/usr/bin/env node
import { runCli, startInteractiveRepl } from "./cli.js";

async function main(): Promise<void> {
  const argv = process.argv.slice(2);

  if (argv[0] === "lsp") {
    // The language server owns stdio; it never returns a CLI result.
    const { startLspServer } = await import("./lsp.js");
    startLspServer();
    return;
  }

  const interactive = process.stdin.isTTY === true
    && (argv.length === 0 || (argv.length === 1 && argv[0] === "repl"));

  const result = interactive ? await startInteractiveRepl(argv) : await runCli(argv);
  if (result.stdout) process.stdout.write(result.stdout);
  if (result.stderr) process.stderr.write(result.stderr);
  process.exitCode = result.code;
}

void main();
