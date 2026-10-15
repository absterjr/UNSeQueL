# Compiler backlog (from dogfooding)

This is the backlog for the **TypeScript compiler**. The Python prototype has
its own at [../../docs/backlog.md](../../docs/backlog.md).

Prioritised gaps found while migrating the five real queries in
[`examples/real/`](../examples/real/) through the CLI (steps 6-9), extended by
the October 2026 external review. The first version needed the SQL hatch for
monthly dates and share-of-total; v1.2 adds native functions and
whole-relation windows for those queries.

## P0 — next for everyday analysis

1. **Date literals and temporal type checks.** `strftime` and `date_trunc` are
   available, but date comparisons are still based on the input type/inference
   rather than a dedicated date-literal syntax.
2. **More scalar and aggregate functions** needed for everyday analysis, with
   type families and DuckDB parity tests for each addition.
3. **Stage-by-stage execution trace.** `run --trace` prints the relation after
   every stage — row count plus a few rows at `from`, after each
   filter/join/derive, at the group, and so on. This is the project's thesis
   made visible (execution order, not asserted) and the clearest
   differentiator from PRQL, Malloy, and pipe SQL, which sell conciseness
   rather than pedagogy.

## P1 — language completeness

4. Window frame clauses (`rows between ...`).
5. A second SQL target (e.g. PostgreSQL) if user feedback confirms warehouse
   portability is a core requirement. **Decision (October 2026): deferred**
   until external feedback; DuckDB remains the locked first target, and
   cross-target SQL text is a portability project rather than a bug fix.
6. More aggregate functions (`MEDIAN`, `STDDEV`, `STRING_AGG`).
7. **Reverse transpiler (SQL → UNSeQueL).** Paste a `SELECT` query and get it
   reordered into execution order; the natural on-ramp for people who already
   know SQL, and a strong playground/discovery hook for the site
   ("paste your SQL, see the order it actually runs in").
8. **SELECT-first input suggestion.** Detect `SELECT ... FROM ...` input and
   offer the reordered rewrite ("UNSeQueL starts from the data: ...") instead
   of a bare "must start with from" error.

## P2 — tooling

9. VS Code extension: the language server ships in `unsequel lsp` with
   diagnostics, completion, hover with generated SQL, formatting, an outline,
   quick-fix code actions, and go-to-definition for `with` names; the
   extension is a language client. Remaining: publish to the Marketplace and
   semantic tokens.
10. Standalone binaries for the npm side: a dependency-free compile exe
    (check/compile/fmt/lsp) builds via SEA (`scripts/build-sea.mjs`) and CI
    attaches it to releases; a run-capable Node exe stays optional because
    the Python package ships the bundled-DuckDB executable.
11. `--data` globs/directories and per-source delimiter settings (the current
    delimiter option applies to all loaded CSVs).
12. **Zero-install WASM playground** on the Pages site: an editor running
    DuckDB-WASM in the browser, which also sidesteps the pip/npm barrier in
    locked-down environments and doubles as the best discovery surface.
13. Integer-division helper (`FLOOR` or an explicit truncating operator) for
    SQL ported from PostgreSQL/Redshift/SQLite, where `/` truncates; the
    current float semantics are documented in [semantics.md](semantics.md).
14. **Connections** (started October 2026): `--db`/`# db:` file-backed
    databases, `--attach name=path`, `create table|view = (pipeline)`,
    `into <table> (pipeline)`, and `unsequel import` are shipped (grammar
    v1.4). Remaining: Postgres/MySQL/SQLite scanners and `httpfs` for S3/HTTP.
15. ~~**Output sinks**~~ **Done (October 2026):** `run --out
    results.csv|.parquet|.json` writes results via DuckDB COPY (both CLIs).
    An `into <table>` stage for persisting results remains with the
    connection work in item 14.
16. `--json` diagnostics mode for non-VS-Code tooling, and documented exit
    codes / shell completions.

Shipped since the original dogfood pass: `strftime`/`date_trunc`, `over ()`,
`select *, expression = ...`, right/full/cross joins, repeated group/aggregate
units, relational stages after projections/joins/pagination, stable sort order
through later stages, backtick identifiers, strict ambiguous-column errors,
nested programs (v1.3: `with` definitions, subquery sources, and
`union`/`union all`/`intersect`/`except`), stdin (`unsequel run -`), file
directives, line selections, interactive `repl`, source introspection, the
VS Code extension in [`../editors/vscode/`](../editors/vscode/), and
duplicate-output-name rejection.

## Intentionally out of scope

- DDL/DML (`CREATE`, `INSERT`, `UPDATE`, `DELETE`)
- Recursive queries
- User-defined functions
- Streaming execution
