# Feature backlog (from dogfooding)

Prioritised after migrating the 21 reference queries through the formatter and
writing `q21_sql_hatch` — the first query the core stages cannot express
without the escape hatch.

## P0 — before recommending daily use

1. **Native window syntax** (`window rank = ROW_NUMBER() over (partition by ...
   order by ...)`). The `sql` hatch covers it today, but ranking is the first
   thing analysts reach for and should not require raw SQL.
2. **`--pipeline` should not be required for `.pusql` files.** Forgetting the
   flag is the most likely first-run failure; infer from the extension.
3. **Stage-located runtime errors for the memory engine.** Parse and schema
   errors point at stage + line; execution errors do not yet.

## P1 — language completeness

4. `right join` / `full join` / `cross join` in the pipeline grammar.
5. Subquery / nested-pipeline sources (`from (from orders ...) as x`).
6. Set operations as stages (`union`, `intersect`, `except`).
7. Preserve comments in `unsequel fmt` (today they are dropped).
8. Multi-line `sql """..."""` strings for longer escape hatches.
9. Hatch guardrails: reject raw SQL that references `stage_N` CTEs directly or
   contains more than one statement.

## P2 — execution and tooling

10. A second codegen target (e.g. `sqlite` or `postgres`) — the CTE chain is
    portable but `_reader` and types are DuckDB-specific.
11. `--lineage` export of the per-stage column lineage collected by
    `check --schema`.
12. `preview` for the memory engine (today preview always runs via DuckDB).
13. Formatter: fold long `group (...)` and `select ...` lines over multiple
    lines when they exceed a width budget.

## Intentionally out of scope

- DDL / DML (`CREATE`, `INSERT`, `UPDATE`, `DELETE`)
- Recursive queries
- User-defined functions
- Streaming execution
