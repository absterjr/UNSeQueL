# Compiler backlog (from dogfooding)

Prioritised gaps found while migrating the five real queries in
[`examples/real/`](../examples/real/) through the CLI (steps 6-9). Two of the
five reached for `sql "..."`.

## P0 — needed to express everyday analysis without the hatch

1. **Date/time functions** (`strftime`, `date_trunc`, month bucketing) — r04
   needed the hatch to group orders by month.
2. **Grand-total windows** — share-of-total needs `SUM(x) over ()`; the grammar
   requires at least one of `partition by` / `order by`, so r05 used a scalar
   subquery in the hatch.
3. **`select *, expression AS name`** — the frozen `*`-only rule forces
   splitting projections into two stages.

## P1 — language completeness

4. Nested pipeline sources (`from (from orders ...) as x`) and pipeline CTEs.
5. Window frame clauses (`rows between ...`).
6. More aggregate functions (`MEDIAN`, `STDDEV`, `STRING_AGG`).
7. Date literals (comparisons today are string literals against dates).

## P2 — tooling

8. stdin query input (`plq run - ...`), matching the Python CLI.
9. `--data` globs/directories; optional schema inference instead of requiring
   a JSON schema for `compile`.
10. `--format json` for `preview` (currently table only).

## Intentionally out of scope

- DDL/DML (`CREATE`, `INSERT`, `UPDATE`, `DELETE`)
- Recursive queries
- User-defined functions
- Streaming execution
