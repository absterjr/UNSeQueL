# Changelog

## 0.1.0 — 2026-10-16

First release. UNSeQueL is a query language that reads in execution order: a
program is a pipeline of stages (`from`, `filter`, `join`, `group by` +
`aggregate`, `sort`, ...) that compiles to DuckDB SQL. This release ships the
complete language, both implementations, and ready-to-run binaries.

**Language (grammar v1.5, `.unsequel`)**
- Composable stages in logical order — `filter`/`having`, a split
  `group by` + `aggregate`, window functions, `distinct`, pagination — with
  `select`, `derive`, and correctness of names checked at every stage.
- Nested programs: `with` definitions, subquery sources, set operators
  (`union`, `union all`, `intersect`, `except`), and joins (inner, left,
  right, full, cross, and anti-joins).
- Write statements: `create [or replace] table|view name = (pipeline)` and
  `into name (pipeline)` (append **by name** with type-family checks, one
  transaction), plus `--create-db` for a missing database file.
- Databases: `# db:`, `--db`, `--attach` for cross-database queries,
  read-only opens for read programs, and `unsequel schema` snapshots that the
  language server reads.
- Self-contained query files: leading `# source name: file.csv`,
  `# schema:`, and `# db:` directives; `unsequel import` materializes CSV,
  JSON, or Parquet files as persistent tables.
- Expressions: `CASE`, `CAST` with type parameters (`DECIMAL(10,2)`),
  scientific-notation literals, source-text number literals that never round
  (large IDs stay exact), `LIKE`, `IN`, `BETWEEN`, backtick identifiers.

**One behavior across two implementations**
- TypeScript/Node compiler: `check`, `compile`, `preview` (run any stage
  prefix), `run`, `fmt`, `repl`, `schema`, `import`, and `lsp`.
- Python package: the same commands (`python -m unsequel`) with a built-in
  memory engine *and* DuckDB execution; the memory engine refuses the `sql`
  escape hatch by design.
- `scripts/parity.sh` diffs the tracks over every example and runs in CI:
  29/29 identical with DuckDB, 26/26 with the memory engine.

**Editor support**
- `unsequel lsp`: stage-located diagnostics, completion, hover with the
  generated SQL, formatting, outline, quick fixes, go-to-definition, and
  semantic tokens.
- VS Code extension with the language server bundled (no separate download)
  and a syntax grammar for `.unsequel` files.

**Output and inputs**
- One output format across tracks: temporal values as DuckDB text, JS number
  spellings, per-column JSON typing (a column never mixes numbers and
  strings), and exact text beyond 2^53.
- CSV typing by DuckDB's whole-file sniffer with `--delimiter` and
  `--column-type` overrides; `--out` sinks for CSV, JSON, and Parquet.

**Binaries**
- Standalone executables with DuckDB bundled for Windows, Linux, and macOS
  (x64 + arm64), Node-free run bundles, and TypeScript compile executables —
  attached to this release by the packaging workflows with smoke tests.
