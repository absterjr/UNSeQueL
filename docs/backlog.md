# Feature backlog (from dogfooding)

This is the backlog for the **Python prototype**. The production TypeScript
compiler has its own at [compiler/docs/backlog.md](../compiler/docs/backlog.md).

Prioritised after migrating the reference queries through the formatter and
writing `q21_sql_hatch` — the first query the core stages cannot express
without the escape hatch.

Recent correctness audit fixes (now covered by regression tests): empty-result
formatting, mixed-type arithmetic/aggregate/sort errors, strict GROUP BY
validation, `ORDER BY` ordinals, ambiguous joins and `SELECT *` collisions,
duplicate `SELECT` output names, case-sensitive LIKE, NULL display,
whole-column CSV inference, duplicate header checks, preserved leading-zero
identifiers, explicit delimiter/type overrides, and non-blocking stdin tests.

## P0 — convergence (October 2026 review)

1. **One language: the composing `.unsequel` grammar.** The `.usql` and
   `.pusql` surfaces are two more grammars with different stage names
   (`where` vs `filter`) and capabilities (no post-aggregate joins, no
   repeated grouping, no set operators in `.pusql`). Decide the prototype's
   role: port grammar v1.3 so `.unsequel` files run on the memory/DuckDB
   engines, or retire the old surfaces from the CLI and docs — the file
   extension stays `.unsequel` either way.
   **Progress (October 2026):** the v1.3 front-end (lexer, AST, parser) is
   ported in `unsequel/grammar.py` and parses the compiler's reference,
   coverage, and dogfood corpora; the analyzer, DuckDB codegen, and CLI
   wiring come next.
2. **Native window syntax** in the prototype grammar
   (`window rank = ROW_NUMBER() over (partition by ... order by ...)`). The
   `sql` hatch covers it today, but ranking is the first thing analysts reach
   for; becomes moot if the convergence decision retires the old surfaces.
3. **Stage-located runtime errors for the memory engine.** Parse and schema
   errors point at stage + line; execution errors do not yet.

## P1 — language completeness

4. `right join` / `full join` / `cross join` in the Python `.pusql` grammar
   (the production `.unsequel` grammar supports all five join kinds).
5. Subquery / nested-pipeline sources in `.pusql` (`from (from orders ...) as x`).
6. Set operations as `.pusql` stages (`union`, `intersect`, `except`); the
   ordered `.usql` core already supports them.
7. **DataFrame / notebook entry point:** `unsequel.run(query, {"orders": df})`
   over pandas/Polars frames, so the language is usable on real in-memory data
   without CSV plumbing.
8. Preserve comments in `unsequel fmt` (today they are dropped).
9. Triple-quoted `sql """..."""` spelling for longer escape hatches (raw
   newlines inside a regular `sql "..."` string already work, and the
   formatter re-escapes them).
10. Hatch guardrails: reject raw SQL that references `stage_N` CTEs directly
    (multi-statement text, non-query openings, and `join` after the hatch are
    already rejected at parse time; `__input__` substitution already respects
    strings, comments, and quoted identifiers).

## P2 — execution and tooling

11. A second codegen target (e.g. `sqlite` or `postgres`) — the CTE chain is
    portable but `_reader` and types are DuckDB-specific.
12. `--lineage` export of the per-stage column lineage collected by
    `check --schema`.
13. `preview` for the memory engine (today preview always runs via DuckDB).
14. Formatter: fold long `group (...)` and `select ...` lines over multiple
    lines when they exceed a width budget.
15. Resolve remaining cross-engine policy differences: division by zero
    (memory `NULL` vs DuckDB infinity) and normalized float rendering in JSON.
    Tie-order behavior and current differences are now documented in
    [`semantics.md`](semantics.md) and covered by conformance probes where
    deterministic comparison is possible.
16. Integer division: `/` documents DuckDB float semantics (`7 / 2` is
    `3.5`); consider a `FLOOR` helper or explicit truncating operator for SQL
    ported from PostgreSQL/Redshift/SQLite, which truncate.

## Intentionally out of scope

- DDL / DML (`CREATE`, `INSERT`, `UPDATE`, `DELETE`)
- Recursive queries
- User-defined functions
- Streaming execution
