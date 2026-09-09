# Step 5 — Schema-aware semantic analysis

**Plan step:** "Build semantic analysis (schema-aware validation)."
**Status:** done. Test suite 41 → 51 passing.

## Why this step

This is what lets the tool catch mistakes before running an expensive warehouse
query — a bad column name, the wrong type in an aggregate, a row column used
after a group. It needs a schema to check against; v0.2 uses a hand-written
JSON file, not live warehouse introspection.

## What changed

| File | What |
| --- | --- |
| `unsequel/schema.py` | New. `Schema.load(path)` reads a JSON schema (`{"tables": {name: {"columns": {col: type}}}}`). Type strings collapse to families: numeric / text / boolean / temporal / unknown. |
| `unsequel/semantics.py` | New. `analyze(stages, schema)` walks the stage IR tracking live columns per stage, raising a stage-located `SemanticError` for unknown tables/columns, dead-after-group references, and `SUM`/`AVG` over non-numeric columns. Returns per-stage column lineage. |
| `unsequel/cli.py` | `check` and `run` take `--schema PATH` (with `--pipeline`). `check` prints `valid UNSeQueL query (schema OK)`. |
| `examples/spec/schema.json` | New. Schema for the four-table sample dataset. |
| `tests/test_semantics.py` | New. All 20 reference queries pass cleanly; 6 invalid queries produce precise stage-located errors; lineage is checked across the group boundary. |

## Examples

```
$ unsequel check bad.pusql --pipeline --schema examples/spec/schema.json
error: stage 2 (where), line 2: where condition references unknown column 'shipping_cost'

$ unsequel check examples/spec/q20_full_pipeline.pusql --pipeline --schema examples/spec/schema.json
valid UNSeQueL query (schema OK)
```

## Side benefit banked

`analyze` already returns the live-column list after every stage (column
lineage). A `--lineage` export is now a small amount of extra work on top.

## Not yet

Ambiguous bare columns after a join are tracked but not raised as an error in
v0.2 (the `on lhs = rhs.col` idiom makes a strict rule noisy); revisit with a
`--strict` mode. YAML schema files — JSON only for now, to keep the package
dependency-free.

## What's next

**Step 6** — DuckDB SQL codegen, one CTE per stage, with golden-file and
execution tests.
