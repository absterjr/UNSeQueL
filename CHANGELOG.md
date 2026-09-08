# Changelog

## 0.3.0 - 2026-09-07

- Added the pipeline-syntax POC (frozen grammar v0.1 in `docs/poc-syntax.md`).
- Pipeline queries lower onto the existing ordered-clause engine; no new
  execution semantics.
- Added `--pipeline` to `unsequel run` and `unsequel check`.
- Added six hand-translated pipeline examples and 7 pipeline tests.
- Added `==` to the lexer for LINQ-style equality.

## 0.2.0 - 2026-09-07

- Expanded the MVP into a complete read/query language core.
- Added CTEs, nested `FROM` sources, `DISTINCT`, `OFFSET`, and set operations.
- Added `RIGHT JOIN`, `FULL JOIN`, `CROSS JOIN`, and qualified null handling.
- Added `CASE`, `CAST`, `IN`, `BETWEEN`, concatenation, distinct aggregates,
  and additional scalar functions.
- Added a dependency-free SQLite table/view adapter.
- Added GitHub Actions tests and wheel builds.

## 0.1.0 - 2026-09-07

- Initial UNSeQueL language and command-line runner.
- Ordered clauses: `FROM`, `JOIN`, `WHERE`, `GROUP BY`, `HAVING`, `SELECT`,
  `ORDER BY`, and `LIMIT`.
- CSV and JSON input with no runtime dependencies.
- Aggregates, aliases, expressions, and basic joins.
