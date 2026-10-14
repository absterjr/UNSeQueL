#!/usr/bin/env node
import { runCli, startInteractiveRepl } from "./cli.js";

const argv = process.argv.slice(2);
const interactive = process.stdin.isTTY === true
  && (argv.length === 0 || (argv.length === 1 && argv[0] === "repl"));

const result = interactive ? await startInteractiveRepl(argv) : await runCli(argv);
if (result.stdout) process.stdout.write(result.stdout);
if (result.stderr) process.stderr.write(result.stderr);
process.exitCode = result.code;
