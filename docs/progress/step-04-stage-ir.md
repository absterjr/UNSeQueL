# Step 4 — Parser and stage-preserving AST

**Plan step:** "Build the parser and AST."
**Status:** done. Test suite 29 → 41 passing.

## Why this step

The pipeline parser collapsed every stage straight into one ordered-clause
`Query`. That is fine for the in-memory engine, but it throws away the stage
boundaries — and one-CTE-per-stage codegen (Step 6) and stage preview (Step 7)
both need those boundaries. This step introduces an explicit intermediate
representation that keeps them.

## What changed

| File | What |
| --- | --- |
| `unsequel/pipeline_ir.py` | New. Typed stage nodes (`From, Join, Derive, Where, Group, Select, Sort, Take, Skip, Distinct`), one per pipeline stage, each carrying its source line. `parse_pipeline_stages(text) -> list[Stage]`. `Derive` / `Where` are tagged `phase="row"` or `"group"` by position. |
| `unsequel/pipeline.py` | Rewritten as a thin lowering pass: `lower_to_query(stages)` collapses the IR onto `Query`; `parse_pipeline` = parse + lower. Public API unchanged. |
| `tests/test_pipeline_ir.py` | New. IR shape, phase tagging, the name-collision fix, and 8 stage-located error cases. |

## Stage-located errors

Every pipeline error is now a `PipelineError` naming the stage index, keyword,
and line:

```
stage 3 (where), line 3: where cannot appear after select
stage 2 (group), line 2: group requires an aggregate list in parentheses
stage 2 (select), line 2: aggregates belong in a group stage, not select
```

Reserved stages (`sql`, `window`, `right/full/cross join`) parse to an explicit
"not in v0.2" message rather than a generic failure.

## Bug fixed

A row-phase `derive` whose name matched a `group` aggregate used to shadow the
aggregate in a later group-phase `where` / `derive`. Group-phase name
resolution now uses group keys + aggregate names + group-phase derives only.
Guarded by `tests/test_pipeline_ir.py::NameCollisionTests`.

## What's next

**Step 5** (shipped in the same push): schema-aware validation over this IR.
