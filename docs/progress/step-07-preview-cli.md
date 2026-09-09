# Step 7 — Stage-preview CLI

**Plan step:** "Build the stage-preview CLI."
**Status:** done. Test suite → 63 passing (5 skip without the `duckdb` extra).

## Why this is the priority feature

Seeing the effect of each stage without commenting code out is the biggest
day-to-day win over raw SQL. This is the demo.

## New commands

| Command | What it does |
| --- | --- |
| `unsequel compile q.pusql --schema s.json [--stage N]` | prints the generated SQL (optionally truncated after stage N) |
| `unsequel preview q.pusql --schema s.json --stage N [--limit K] --data t=f.csv` | runs the pipeline truncated at stage N against DuckDB and shows K sample rows, labelled `stage N/total (keyword)` |
| `unsequel run q.pusql --pipeline --engine duckdb --schema s.json --data ...` | executes the full generated SQL in DuckDB instead of the in-memory engine |

`check` and `run` (memory engine) are unchanged; `check --schema` still works.

## Example

```
$ unsequel preview examples/spec/q20_full_pipeline.pusql \
    --schema examples/spec/schema.json --stage 3 --limit 4 \
    --data orders=examples/spec/orders.csv --data products=examples/spec/products.csv
stage 3/8  (derive)
order_id  customer  ...  category    line_total
--------  --------  ...  ----------  ----------
5001      Ada       ...  stationery  45.0
...

$ ... --stage 5   # after the group + having
stage 5/8  (where)
category    orders  revenue
----------  ------  -------
home        6       285.0
stationery  6       177.0
grocery     3       132.0
```

Step through `--stage 1..8` and watch the relation change shape at each step.

## What changed

| File | What |
| --- | --- |
| `unsequel/duckdb_backend.py` | New. `run_sql(sql, data)` — loads CSV/JSON/Parquet sources into a throwaway in-memory DuckDB and returns the result as the same `Table` the in-memory engine uses. Raises a clear error if the `duckdb` extra is missing. |
| `unsequel/cli.py` | Restructured into per-command handlers; adds `compile` and `preview`, and `run --engine {memory,duckdb}`. |
| `tests/test_cli.py` | New. compile output shape + truncation; preview labelling, column narrowing, range check; duckdb-engine run matches the memory engine. |

## What's next

**Step 8** — the raw `sql` escape-hatch stage and an idempotent `.pusql`
formatter.
