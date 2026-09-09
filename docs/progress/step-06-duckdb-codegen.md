# Step 6 — DuckDB SQL codegen (one CTE per stage)

**Plan step:** "Build SQL codegen for one dialect (DuckDB)."
**Status:** done. Test suite 56 → 56 (system) / all green with duckdb.

## Why one CTE per stage

Verbose SQL, but it is the enabler for stage preview: truncating the `WITH`
chain at `stage_k` and selecting from it *is* the intermediate result. Getting
one dialect completely right matters more than covering several.

## What changed

| File | What |
| --- | --- |
| `unsequel/codegen.py` | New. `emit_sql(stages, schema, stop_at=None)` → a `WITH stage_1 AS (...), stage_2 AS (...) ... SELECT * FROM stage_N`. Expression renderer covers every node (arithmetic, comparisons, `IS NULL`, `IN`, `BETWEEN`, `CASE`, `CAST`, `||`, aggregates). Joins project the new table's columns with stable names so qualified references (`members.customer`) stay resolvable in later CTEs. |
| `tests/golden/q01..q20.sql` | New. The generated SQL for every reference query, golden-file compared. |
| `tests/handwritten/q01..q20.sql` | New. A hand-written single-statement SQL equivalent for every reference query. |
| `tests/test_codegen.py` | New. (1) generated SQL matches golden; (2) one CTE per stage + correct tail; (3) preview truncation; (4) **execution parity** — generated vs hand-written SQL return the same rows in DuckDB (skipped without the `duckdb` extra). |
| `pyproject.toml` | `[project.optional-dependencies] duckdb`. Core stays dependency-free. |
| `.github/workflows/ci.yml` | Installs `.[duckdb]` so CI runs the execution-parity tests. |

## Validation

All 20 reference queries compile to valid DuckDB SQL and, executed against the
seeded sample data, return the same rows as their hand-written equivalents.
Golden files make any change to the generated SQL show up as a reviewable diff.

Codegen **requires a schema** (unlike the in-memory engine) — the join stages
need each table's column list.

## What's next

**Step 7** (same push): the `compile` / `preview` / `run --engine duckdb` CLI.
