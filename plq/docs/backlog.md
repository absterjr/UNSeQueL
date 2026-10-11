# Compiler backlog (from dogfooding)

This is the backlog for the **TypeScript compiler**. The Python prototype has
its own at [../../docs/backlog.md](../../docs/backlog.md).

Prioritised gaps found while migrating the five real queries in
[`examples/real/`](../examples/real/) through the CLI (steps 6-9). Two of the
five reached for `sql "..."`.

## P0 — needed to express everyday analysis without the hatch

1. **Date/time functions** (`strftime`, `date_trunc`, month bucketing) — r04
   needed the hatch to group orders by month.
2. **Grand-total windows** — share-of-total needs `SUM(x) over ()`; the grammar
   requires at least one of `partition by` / `order by`, so r05 used a scalar
   subquery in the hatch.
3. **`select *, name = expression`** — the frozen `*`-only rule forces
   splitting projections into two stages.

## P1 — language completeness

4. Nested pipeline sources (`from (from orders ...) as x`) and pipeline CTEs.
5. Window frame clauses (`rows between ...`).
6. More aggregate functions (`MEDIAN`, `STDDEV`, `STRING_AGG`).
7. Date literals (comparisons today are string literals against dates).

## P2 — tooling

8. `--format json` for `preview` (currently table only).
9. VS Code extension polish: remember the PLQ terminal's working directory
   per workspace, optionally run `plq fmt` on save, and publish the extension
   to the Marketplace.
10. Standalone binaries — spike `bun build --compile` or Node SEA with the
    DuckDB native addon so users need neither Node nor npm.
11. `--data` globs/directories.

Shipped since dogfooding: stdin input (`plq run -`), file directives with
flagless `run`/`compile`/`preview`, `--lines A:B` selections that keep the
file's directives, interactive `repl` (`.run`, pasted-prompt stripping),
source introspection when no JSON schema is given, and the VS Code extension
in [`../editors/vscode/`](../editors/vscode/).

## Intentionally out of scope

- DDL/DML (`CREATE`, `INSERT`, `UPDATE`, `DELETE`)
- Recursive queries
- User-defined functions
- Streaming execution
