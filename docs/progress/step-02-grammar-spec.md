# Step 2 — Grammar as a real document

**Plan step:** "Write the grammar as a real document."
**Commit:** `docs: add pipeline language spec v0.2 with 20 reference queries`
**Status:** done. Test suite 24 → 29 passing.

## Why this step

The pipeline surface had only a frozen v0.1 proof-of-concept note with 6
examples. Before building a stage-preserving IR and SQL codegen, the grammar
needs to be pinned formally — every stage, its syntax, and what it compiles to
— because fixing a design flaw on paper costs an hour and fixing it after the
compiler is built costs days.

## What changed

| File | What |
| --- | --- |
| `docs/pipeline-spec.md` | New. Authoritative v0.2 spec: EBNF, IR node, and one-CTE-per-stage DuckDB lowering for every stage (`from, join, derive, where, group, select, sort, take, skip, distinct`). Worked 8-stage CTE example. Reserved: `sql` escape hatch, `window`. |
| `examples/spec/*.csv` | New. Four-table sample dataset (orders, customers, products, members) with deliberate edge cases — a customer with no orders, non-members. |
| `examples/spec/q01..q20.pusql` | New. 20 reference queries covering every stage and combination, from `from` alone to the full eight-stage pipeline. |
| `tests/test_spec_examples.py` | New. Asserts all 20 parse and execute, pins row counts, and pins exact output for group-filter (HAVING), anti-join, and the full pipeline. |
| `README.md`, `CHANGELOG.md` | Pointers to the new spec. |

## Decisions locked in v0.2

- **Build on the existing Python package**, not a fresh TypeScript rewrite —
  the lexer, parser, expression grammar, and in-memory engine already exist and
  pass tests.
- **DuckDB** is the first codegen target; the in-memory engine stays as a
  zero-dependency cross-check.
- **Positional `where`** carries both row-filter and group-filter meaning from
  its place in the pipeline — no separate `filter` / `having` keywords.
- **One CTE per stage** for codegen — verbose SQL, but it makes stage-by-stage
  preview a one-line operation.

## Known issue found (fix scheduled for Step 4)

If a row-phase `derive` has the same name as a `group` aggregate, a later
group-phase `where` / `derive` inlines the row expression instead of the
aggregate. The reference queries avoid it by naming the row derive `line_total`
and the aggregate `revenue`. Step 4's IR work resolves group-phase names with
precedence and adds a regression test.

## What's next

**Step 4 — parser and AST:** introduce a stage-preserving IR (the parser
currently collapses all stages into one `Query`, which cannot express
one-CTE-per-stage or drive preview), plus stage-located parse errors that point
at the exact stage and column.
