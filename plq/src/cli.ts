/**
 * PLQ CLI — Step 1 skeleton.
 *
 * Nothing functional yet (plan Step 1): the only contract is that the CLI
 * starts, reports its version, and exits non-zero with usage when no command
 * is given. Real subcommands (compile / preview / run) arrive in Step 7.
 */

export interface CliResult {
  code: number;
  stdout: string;
  stderr: string;
}

export const VERSION = "0.0.1";

const USAGE = `plq ${VERSION}
pipeline query language compiler

usage: plq <command> [options]

commands are not implemented yet (plan steps 2-7); see docs/pipeline-spec.md
`;

export function runCli(argv: readonly string[]): CliResult {
  const args = argv.filter((arg) => arg !== "--");
  if (args.includes("--version") || args.includes("-V")) {
    return { code: 0, stdout: `plq ${VERSION}\n`, stderr: "" };
  }
  if (args.includes("--help") || args.includes("-h")) {
    return { code: 0, stdout: USAGE, stderr: "" };
  }
  return { code: 2, stdout: "", stderr: USAGE };
}
