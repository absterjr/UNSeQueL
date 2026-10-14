# Compiler backlog (from dogfooding)

This is the backlog for the **TypeScript compiler**. The Python prototype has
its own at [../../docs/backlog.md](../../docs/backlog.md).

Prioritised gaps found while migrating the five real queries in
[`examples/real/`](../examples/real/) through the CLI (steps 6-9). The first
version needed the SQL hatch for monthly dates and share-of-total; v1.2 adds
native functions and whole-relation windows for those queries.

## P0 — next for everyday analysis

1. **Date literals and temporal type checks.** `strftime` and `date_trunc` are
   available, but date comparisons are still based on the input type/inference
   rather than a dedicated date-literal syntax.
2. **More scalar and aggregate functions** needed for everyday analysis, with
   type families and DuckDB parity tests for each addition.

## P1 — language completeness

4. Window frame clauses (`rows between ...`).
5. A second SQL target (e.g. PostgreSQL) if user feedback confirms warehouse
   portability is a core requirement. **Decision (October 2026): deferred**
   until external feedback; DuckDB remains the locked first target, and
   cross-target SQL text is a portability project rather than a bug fix.
6. More aggregate functions (`MEDIAN`, `STDDEV`, `STRING_AGG`).

## P2 — tooling

7. VS Code extension polish: remember the UNSeQueL terminal's working directory
   per workspace, optionally run `unsequel fmt` on save, and publish the extension
   to the Marketplace.
8. Standalone binaries — spike `bun build --compile` or Node SEA with the
   DuckDB native addon so users need neither Node nor npm.
9. `--data` globs/directories and per-source delimiter settings (the current
   delimiter option applies to all loaded CSVs).

Shipped since the original dogfood pass: `strftime`/`date_trunc`, `over ()`,
`select *, expression = ...`, right/full/cross joins, repeated group/aggregate
units, relational stages after projections/joins/pagination, stable sort order
through later stages, backtick identifiers, strict ambiguous-column errors,
nested programs (v1.3: `with` definitions, subquery sources, and
`union`/`union all`/`intersect`/`except`), stdin (`unsequel run -`), file
directives, line selections, interactive `repl`, source introspection, and the
VS Code extension in [`../editors/vscode/`](../editors/vscode/).

## Intentionally out of scope

- DDL/DML (`CREATE`, `INSERT`, `UPDATE`, `DELETE`)
- Recursive queries
- User-defined functions
- Streaming execution
