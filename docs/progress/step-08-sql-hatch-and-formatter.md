# Step 8 — Raw-SQL escape hatch and canonical formatter

**Plan step:** "Add a `sql "..."` pass-through stage and a canonical,
idempotent formatter."
**Status:** done. Test suite → 84 passing (5 skip without the `duckdb` extra;
CI installs it).

## Why these two

The hatch removes the "pipeline can't express X" ceiling (window functions
today), and the formatter is what makes `.pusql` files diff-able and
reviewable — both were prerequisites for dogfooding (Step 9).

## The `sql "..."` hatch

One string literal, one `SELECT`, no `;`. The previous relation is table
`__input__`; named tables stay visible.

```text
from orders
sql "SELECT order_id, customer, quantity, ROW_NUMBER() OVER (PARTITION BY customer ORDER BY quantity DESC, order_id) AS rn FROM __input__"
where rn = 1
select order_id, customer, quantity
sort order_id
```

| Engine | How it runs |
| --- | --- |
| `run --pipeline` (memory) | pipeline is cut into segments at each `sql` stage; the raw segment runs against an in-memory **SQLite** (stdlib `sqlite3`, no new dependency) with `__input__` + the named tables |
| `compile` / `preview` / `run --engine duckdb` | the raw text becomes its own CTE, `__input__` rewritten to `stage_{k-1}` |

Semantics frozen in spec v0.3 (§3.10, §4): a `sql` stage starts a fresh
relation (row phase resets; `group`/`take`/`skip`/`distinct` singletons may
occur once per segment), it may not be first or follow `select`, and schema
validation treats everything after it as opaque (`<raw sql>` / `<opaque>`
lineage markers) instead of guessing columns.

`window` remains reserved (§3.11); the hatch covers that gap until the native
syntax lands.

## The formatter

`unsequel fmt [--write|--check]` re-renders the stage IR: lowercase keywords,
one stage per line, spaces around operators, `-column` descending,
`name = expr` only when the name differs, re-quoted (never re-formatted) SQL
strings, trailing newline. Idempotency is asserted across all 27 example
queries in the test suite.

## What changed

| File | What |
| --- | --- |
| `unsequel/pipeline_ir.py` | `sql` parses to the existing `RawSql` node (single string, single SELECT, no `;`); segment-reset rules in the driver; `window` stays reserved |
| `unsequel/sql_runtime.py` | New. Runs one raw stage on stdlib SQLite with `__input__`. |
| `unsequel/pipeline.py` | `execute_pipeline_stages` — segment splitting for the memory engine; `lower_to_query` rejects `RawSql` with a directed message |
| `unsequel/codegen.py` | `_raw_sql_stage` — opaque CTE with `__input__` → `stage_{k-1}` substitution; column tracking resets |
| `unsequel/semantics.py` | hatch is an integrity boundary; lineage markers, later stages unchecked |
| `unsequel/expressions.py` | `format_expr` / `format_string` canonical renderers |
| `unsequel/format.py` | New. `format_pipeline` / `is_formatted` from the stage IR. |
| `unsequel/cli.py` | `fmt` command; `run --pipeline` (memory) uses `execute_pipeline_stages` |
| `unsequel/lexer.py` | `|` is now a token (inline pipes); STRING tokens carry their start position (multi-line strings keep stage grouping correct) |
| `examples/spec/q21_sql_hatch.pusql` (+ golden, + handwritten) | Reference query 21: top order per customer via a window function |
| `tests/test_format_and_sql.py` | 24 tests: formatter idempotency/rules, memory hatch, codegen substitution + truncation, opaque semantics, CLI fmt/run/compile |

## What's next

**Step 9** — dogfooding: migrate real queries, prioritise the gaps
(`docs/backlog.md`), and hand the docs to someone external
(`docs/first-task.md`).
