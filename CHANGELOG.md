# Changelog

## Unreleased

- Added the pipeline language specification v0.2 (`docs/pipeline-spec.md`):
  every stage formally defined with its EBNF, IR node, and one-CTE-per-stage
  DuckDB lowering. Supersedes the frozen v0.1 POC note.
- Added 20 reference pipeline queries and a four-table sample dataset under
  `examples/spec/`, covering every v0.2 stage and combination.
- Added `tests/test_spec_examples.py` pinning parse + execution behaviour for
  all 20 reference queries.
- Added a stage-preserving IR (`unsequel/pipeline_ir.py`): one typed node per
  stage with its source line. Pipeline parse errors are now stage-located and
  reserved stages report an explicit "not in v0.2" message. `unsequel/pipeline.py`
  is a thin lowering pass. Fixed a row-derive shadowing a group aggregate.
- Added schema-aware validation: `unsequel/schema.py` (JSON schema files) and
  `unsequel/semantics.py` (`analyze`), via `check --schema` / `run --schema`.
  Catches unknown tables/columns, dead-after-group references, and non-numeric
  `SUM`/`AVG` before execution; returns per-stage column lineage.
- Added DuckDB SQL codegen (`unsequel/codegen.py`): `emit_sql` lowers the stage
  IR to one CTE per stage, with golden files and execution-parity tests against
  hand-written SQL.
- Added `unsequel compile`, `unsequel preview` (run a pipeline truncated at a
  stage and show a sample), and `unsequel run --engine duckdb`. New optional
  `duckdb` extra; the core stays dependency-free.

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
