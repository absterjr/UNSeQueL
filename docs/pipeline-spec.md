# UNSeQueL Pipeline Language — Specification v0.2

This is the authoritative grammar and lowering spec for the pipeline surface of
UNSeQueL (`.pusql` files). It supersedes the proof-of-concept note in
[poc-syntax.md](poc-syntax.md), which stays as the frozen v0.1 history and the
prior-art credits (PRQL, LINQ, Malloy).

The pipeline is **one transform per line**. Stages run top to bottom, each
consuming the relation produced by the stage above it. This document defines,
for every stage:

1. its concrete syntax (EBNF),
2. the IR node it parses into (Step 4 of the implementation plan), and
3. the SQL it lowers to — **one CTE per stage** — when compiled for DuckDB
   (Step 6).

## 1. Design decisions

These are locked for v0.2. Changing any of them is a v0.3 spec change, not a
silent edit.

| Decision | Choice | Rationale |
| --- | --- | --- |
| Compiler language | Python (existing `unsequel` package) | Lexer, parser, expression grammar, and in-memory engine already exist and pass a test suite; rebuilding them buys nothing. |
| First execution target | DuckDB | No server; runs in local dev and CI. The in-memory engine stays as a zero-dependency fallback and cross-check. |
| Parser style | Hand-written recursive descent (already built) | Full control over stage-located error messages. |
| Codegen shape | One CTE per pipeline stage | Verbose SQL, but it is what makes stage-by-stage preview (`unsequel preview`) a one-line operation. |
| Row filter vs group filter | Positional `where` (not separate `filter` / `having` keywords) | The stage's position in the pipeline already carries the meaning; a second keyword would be redundant. See §3.4. |

### Relation to the plan's stage list

The implementation plan names: `from, filter, join, group by, aggregate,
having, window, select, sort, take, distinct`. The mapping:

| Plan stage | Pipeline spelling | Status |
| --- | --- | --- |
| from | `from` | v0.2 |
| filter (rows) | `where` before `group` | v0.2 |
| join | `join`, `left join` | v0.2 |
| group by + aggregate | `group keys (name = AGG(expr), ...)` | v0.2 |
| having | `where` after `group` | v0.2 |
| select | `select` | v0.2 |
| sort | `sort` | v0.2 |
| take / distinct | `take`, `skip`, `distinct` | v0.2 |
| (escape hatch) | `sql "..."` | **reserved, Step 8** |
| window | `window name = FUNC(...) over (...)` | **reserved, not yet implemented** |
| right/full/cross join | ordered syntax only for now | reserved |

Reserved stages are parsed to a clear "not implemented in v0.2" error, not a
generic failure.

## 2. Lexical structure

Tokenising is unchanged from the core language ([lexer.py](../unsequel/lexer.py)):

- **Identifiers** — `[A-Za-z_][A-Za-z0-9_$]*`, optionally dotted
  (`products.category`). Case-sensitive for data columns; stage keywords and
  function names are matched case-insensitively.
- **Numbers** — integer or decimal (`10`, `4.50`).
- **Strings** — single or double quoted, `''`/`""` or `\` for an escaped
  quote (`'O''Brien'`).
- **Operators** — `+ - * / %`, `= == != <> < <= > >=`, `|`, `||`, `( ) , .`.
- **Comments** — `--` or `#` to end of line.
- **Stage separator** — a newline, or `|` at parenthesis depth 0. A stage may
  not span multiple lines.

## 3. Stages

Notation: `expr` is the core expression grammar (arithmetic, comparisons,
`AND/OR/NOT`, `IS NULL`, `IN`, `BETWEEN`, `LIKE`, `CASE`, `CAST`, `||`, scalar
and aggregate function calls) — see the
[language reference](language-reference.md). `ident` is an identifier,
`int` a non-negative integer literal, `name` a single (undotted) identifier.

```ebnf
pipeline      = from_stage , { newline , stage } ;
stage         = join_stage | where_stage | derive_stage | group_stage
              | select_stage | sort_stage | take_stage | skip_stage
              | distinct_stage | sql_stage | window_stage ;
```

### 3.1 `from` — source

```ebnf
from_stage  = "from" , table_ref ;
table_ref   = ident [ "as" ident ]
            | "(" , pipeline , ")" "as" ident ;
```

- Must be the first stage. Exactly one per pipeline.
- **IR:** `From(source, alias)`.
- **SQL:** `stage_1 AS (SELECT * FROM <source> [AS <alias>])`.

### 3.2 `join` / `left join`

```ebnf
join_stage  = [ "left" ] , "join" , table_ref , "on" , expr ;
```

- May only appear after `from` and before `group` / `select`.
- The `on` expression may reference columns from either side; unqualified
  names must be unambiguous.
- **IR:** `Join(kind, source, alias, on)` where `kind ∈ {inner, left}`.
- **SQL:** `stage_k AS (SELECT * FROM stage_{k-1} [LEFT] JOIN <source> ON <on>)`.

### 3.3 `derive` — computed columns

```ebnf
derive_stage = "derive" , named_expr , { "," , named_expr } ;
named_expr   = name , "=" , expr ;
```

- Adds columns; never removes them. A later `derive` may use an earlier
  derived name.
- Before `group`: the expression is remembered and inlined wherever the name
  is used later (row scope).
- After `group`: becomes an extra output column computed from the grouped
  relation.
- Aggregate calls are **not** allowed here; they belong in `group`.
- **IR:** `Derive([(name, expr), ...], phase)` with `phase ∈ {row, group}`.
- **SQL:** `stage_k AS (SELECT *, (<expr>) AS <name>, ... FROM stage_{k-1})`.

### 3.4 `where` — filter (position-sensitive)

```ebnf
where_stage = "where" , expr ;
```

- **Before `group`** — filters rows. Multiple pre-group `where` stages combine
  with `AND`.
- **After `group`** — filters groups (SQL `HAVING`). May reference group keys
  and aggregate output names.
- May not appear after `select`.
- **IR:** `Where(expr, phase)` with `phase ∈ {row, group}`.
- **SQL (row):** `stage_k AS (SELECT * FROM stage_{k-1} WHERE <expr>)`.
- **SQL (group):** identical shape; the CTE simply sits after the `group` CTE,
  so `WHERE` over aggregate columns is legal.

### 3.5 `group` — group by + aggregate

```ebnf
group_stage   = "group" , key_list , "(" , aggregate_list , ")" ;
key_list      = key , { "," , key } ;
key           = ident | named_expr ;
aggregate_list= aggregate , { "," , aggregate } ;
aggregate     = name , "=" , agg_call
              | "COUNT" , "(" , "*" , ")" ;          (* auto-named "count" *)
agg_call      = ( "COUNT" | "SUM" | "AVG" | "MIN" | "MAX" ) , "(" , [ "DISTINCT" ] , expr , ")"
              | "COUNT" , "(" , "*" , ")" ;
```

- At most one `group` per pipeline.
- After `group` the live columns are exactly: the group keys and the named
  aggregates (plus any post-group `derive` outputs). Original row columns are
  gone.
- **IR:** `Group([(keyname, expr), ...], [(aggname, call), ...])`.
- **SQL:** `stage_k AS (SELECT <keys>, <agg> AS <name>, ... FROM stage_{k-1} GROUP BY <keys>)`.

### 3.6 `select` — projection

```ebnf
select_stage = "select" , item , { "," , item } ;
item         = "*" | ident | named_expr ;
```

- At most one `select`. After it, only `sort` / `take` / `skip` / `distinct`
  may follow.
- `*` expands to the currently live columns.
- Aggregate calls are rejected here (use `group`).
- **IR:** `Select([SelectItem(expr, alias), ...])`.
- **SQL:** `stage_k AS (SELECT <items> FROM stage_{k-1})`.

### 3.7 `sort`

```ebnf
sort_stage = "sort" , sort_key , { "," , sort_key } ;
sort_key   = [ "-" ] , expr , [ "asc" | "desc" ] ;
```

- Leading `-` or trailing `desc` means descending. Default ascending.
- **IR:** `Sort([OrderKey(expr, descending), ...])`.
- **SQL:** `stage_k AS (SELECT * FROM stage_{k-1} ORDER BY <keys>)`.

### 3.8 `take` / `skip`

```ebnf
take_stage = "take" , int ;
skip_stage = "skip" , int ;
```

- At most one of each. `skip` before `take` in the same pipeline is applied as
  SQL `OFFSET` + `LIMIT`.
- **IR:** `Limit(count)` / `Offset(count)`.
- **SQL:** `stage_k AS (SELECT * FROM stage_{k-1} LIMIT <n> [OFFSET <m>])`.

### 3.9 `distinct`

```ebnf
distinct_stage = "distinct" ;
```

- Takes no arguments. At most one.
- **IR:** `Distinct()`.
- **SQL:** `stage_k AS (SELECT DISTINCT * FROM stage_{k-1})`.

### 3.10 `sql` — raw escape hatch (reserved, Step 8)

```ebnf
sql_stage = "sql" , string ;
```

- The string is spliced into the CTE chain untouched, with `stage_{k-1}`
  available as a table name. Parsed in v0.2, lowered in Step 8.

### 3.11 `window` — (reserved, not implemented)

Placeholder syntax `window name = FUNC(args) over (partition by ... order by ...)`.
Parsing it in v0.2 produces `window is reserved and not implemented in v0.2`.

## 4. Ordering rules (frozen)

1. The first stage is `from`.
2. `join`, `derive` (row phase), `where` (row phase), and `group` may not
   appear after `select`.
3. `group`, `take`, `skip`, and `distinct` appear at most once.
4. `where` before `group` filters rows; after `group` filters groups.
5. Aggregates appear only inside `group`. `select` and `derive` cannot create
   new aggregates.
6. Named entries use `name = expression`. A bare identifier is its own name; a
   bare `COUNT(*)` inside `group` is named `count`.

## 5. CTE lowering — worked example

`examples/spec/q20_full_pipeline.pusql`:

```text
from orders
join products on product_id = products.product_id
derive line_total = quantity * unit_price
group category (orders = COUNT(*), revenue = SUM(line_total))
where revenue > 100
derive avg_order = revenue / orders
sort -revenue
take 3
```

lowers to (target DuckDB, one CTE per stage):

```sql
WITH stage_1 AS (SELECT * FROM orders),
     stage_2 AS (SELECT * FROM stage_1 JOIN products ON stage_1.product_id = products.product_id),
     stage_3 AS (SELECT *, (quantity * unit_price) AS line_total FROM stage_2),
     stage_4 AS (SELECT category, COUNT(*) AS orders, SUM(line_total) AS revenue
                 FROM stage_3 GROUP BY category),
     stage_5 AS (SELECT * FROM stage_4 WHERE revenue > 100),
     stage_6 AS (SELECT *, (revenue / orders) AS avg_order FROM stage_5),
     stage_7 AS (SELECT * FROM stage_6 ORDER BY revenue DESC),
     stage_8 AS (SELECT * FROM stage_7 LIMIT 3)
SELECT * FROM stage_8;
```

`unsequel preview q20_full_pipeline.pusql --stage 4` runs
`SELECT * FROM stage_4 LIMIT <sample>` and nothing below it.

## 6. Validation — the 20 reference queries

The 20 queries in [`examples/spec/`](../examples/spec/) exercise every v0.2
stage and combination. Each parses unambiguously against §3 and executes on the
sample dataset (§7); `tests/test_spec_examples.py` asserts this.

| # | File | Stages exercised | Rows on sample data |
| --- | --- | --- | --- |
| 1 | `q01_all_orders` | from | 15 |
| 2 | `q02_project_columns` | from, select | 15 |
| 3 | `q03_row_filter` | from, where(row), select | 6 |
| 4 | `q04_filter_and_sort` | from, where(row, AND), sort(multi, desc) | 5 |
| 5 | `q05_derive_column` | from, derive(row), select | 15 |
| 6 | `q06_derive_rank_take` | from, derive(row), select, sort(desc), take | 5 |
| 7 | `q07_inner_join` | from, join(inner), select | 15 |
| 8 | `q08_left_join_antijoin` | from, left join, where(IS NULL), select | 3 |
| 9 | `q09_group_count` | from, group(COUNT *), sort | 3 |
| 10 | `q10_group_sum_rank` | from, derive, group(SUM + COUNT), sort | 3 |
| 11 | `q11_group_having` | from, derive, group, where(group phase), sort | 3 |
| 12 | `q12_post_group_metric` | from, derive, group, derive(group phase), sort | 5 |
| 13 | `q13_join_group_topn` | from, join, derive, group, sort, take | 2 |
| 14 | `q14_distinct` | from, select, distinct, sort | 3 |
| 15 | `q15_pagination` | from, select, sort, skip, take | 5 |
| 16 | `q16_in_list` | from, where(IN), select, sort | 8 |
| 17 | `q17_between` | from, where(BETWEEN), select, sort | 6 |
| 18 | `q18_case_bucket` | from, derive(CASE), select, sort | 15 |
| 19 | `q19_multi_key_group` | from, derive, group(2 keys, 2 aggs), sort(multi) | 11 |
| 20 | `q20_full_pipeline` | all eight core stages | 3 |

### Name resolution across the `group` boundary

Before `group`, names resolve against row columns and row-phase `derive`
outputs. After `group`, names resolve against group keys, aggregate names, and
group-phase `derive` outputs **only** — a row-phase `derive` with the same name
as an aggregate does not leak through. (Fixed in Step 4;
`tests/test_pipeline_ir.py::NameCollisionTests` guards it.)

## 7. Sample dataset

[`examples/spec/`](../examples/spec/) ships four CSVs:

- **orders**(order_id, customer, country, product_id, quantity, unit_price, order_date) — 15 rows
- **customers**(customer, country, segment, signup_date) — 6 rows; `Barbara` has no orders
- **products**(product_id, product_name, category, list_price) — 5 rows
- **members**(customer, plan, joined_date) — 4 rows; `Alan` and `Barbara` are not members

Run any reference query against it:

```bash
python -m unsequel run examples/spec/q20_full_pipeline.pusql --pipeline \
  --data orders=examples/spec/orders.csv \
  --data products=examples/spec/products.csv
```

## 8. Schema validation

A **schema file** (`examples/spec/schema.json`) declares the tables and column
types a query may read:

```json
{ "tables": { "orders": { "columns": { "unit_price": "number", ... } } } }
```

`unsequel check --pipeline --schema <file>` (and `run --schema`) walks the stage
IR against it *before* touching any data, tracking which columns are alive at
each stage. It reports, with the offending stage and line:

- an unknown source or joined table,
- a reference to a column that is not alive at that stage — including a
  row-level column used after `group`,
- `SUM` / `AVG` over a non-numeric column.

Type families: `integer/number/float/decimal` → numeric, `string/text/varchar`
→ text, `bool` → boolean, `date/time/timestamp` → temporal; anything else is
`unknown` and never raises a type error. Ambiguous bare columns after a join are
tracked for lineage but not yet an error in v0.2.

The walk also returns **column lineage** — the live column list after every
stage — which is the basis for a future `--lineage` export.
