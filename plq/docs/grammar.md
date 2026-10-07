# PLQ Language Specification v1.0 (Step 2)

Status: **frozen**. Changes require a version bump and re-validation of the
reference queries in [`examples/reference/`](../examples/reference/).

This document defines the complete syntax of the pipeline query language, every
stage's internal representation, and its DuckDB lowering. Stack decisions are
locked: TypeScript/Node compiler, DuckDB first target, hand-written recursive
descent parser, one CTE per pipeline stage.

## 1. Program model

A PLQ program is an ordered list of **stages**. Each stage transforms the
relation produced by the previous stage. The first stage is always `from`.

```text
from orders
derive line_total = quantity * unit_price
group by category
aggregate orders = COUNT(*), revenue = SUM(line_total)
having revenue > 100
sort -revenue
take 3
```

## 2. Lexical structure

| Token | Rule |
| --- | --- |
| keyword | one of the reserved stage words or expression words; case-insensitive, canonical output is lowercase for stage keywords and uppercase for expression keywords (`AND`, `IS NULL`, `CASE ... END`) |
| identifier | `[A-Za-z_][A-Za-z0-9_]*`; qualified names are `identifier.identifier` |
| number | `123` (integer) or `12.5` (real); no leading sign (signs are unary operators) |
| string | single- or double-quoted, **single line**; backslash escapes `\\` `\'` `\"` `\n`; doubled quotes (`''`) also escape; canonical output uses single quotes |
| comment | `#` or `--` to end of line; not preserved by the formatter |
| separator | a newline or `|` at parenthesis depth zero terminates a stage |

Stage keywords (lowercase canonical): `from`, `join`, `left join`, `filter`,
`derive`, `group by`, `aggregate`, `having`, `window`, `select`, `sort`,
`skip`, `take`, `distinct`, `sql`.

Deliberate v1.0 changes from the Python prototype:

| Prototype | PLQ v1.0 | Why |
| --- | --- | --- |
| `where` position-sensitive | `filter` (row only) + `having` (group only) | explicit stages give precise errors; matches the agreed stage list |
| `group keys (aggs)` combined | `group by` + `aggregate` stages | one concept per stage; maps 1:1 to SQL |

## 3. Stages

### 3.1 `from`

```ebnf
from_stage = "from" , source ;
source     = qualified_name , [ "as" , identifier ] ;
```

- Exactly one `from`, and it is the first stage.
- **IR:** `FromStage { source: { name, alias? } }`
- **SQL:** `stage_1 AS (SELECT * FROM <name> [AS <alias>])`

### 3.2 `join` / `left join`

```ebnf
join_stage = [ "left" ] , "join" , source , "on" , expr ;
```

- Row phase only (not after `group by`); any number of joins, in order.
- **IR:** `JoinStage { kind: "inner" | "left", source, on }`
- **SQL:** `stage_k AS (SELECT <prev>.*, <right cols> FROM stage_{k-1} [LEFT] JOIN <source> ON <expr>)`; name collisions in the right table are exposed as `<alias>_<column>`.

### 3.3 `filter`

```ebnf
filter_stage = "filter" , expr ;
```

- Row phase only, before `group by`. For group filtering use `having`.
- Multiple row `filter` stages combine with `AND`.
- **IR:** `FilterStage { condition }`
- **SQL:** `stage_k AS (SELECT * FROM stage_{k-1} WHERE <expr>)`

### 3.4 `derive`

```ebnf
derive_stage = "derive" , named_expr , { "," , named_expr } ;
named_expr   = identifier , "=" , expr ;
```

- Adds computed columns to the current relation.
- Allowed in the row phase (before `group by`) and after `aggregate`/`having`
  (group phase), where expressions may only reference surviving columns.
- A later row-phase `derive` may use an earlier derived name; a derived name may
  be shadowed by an aggregate name after `group by`.
- **IR:** `DeriveStage { items: [name, expr][], phase: "row" | "group" }`
- **SQL:** `stage_k AS (SELECT *, (<expr>) AS <name>, ... FROM stage_{k-1})`

### 3.5 `group by`

```ebnf
group_stage = "group" , "by" , key , { "," , key } ;
key         = identifier | named_expr ;
```

- At most once. Keys are bare column names or named expressions.
- After this stage the live columns are exactly the group keys.
- **IR:** `GroupByStage { keys: [name, expr][] }`
- **SQL:** `stage_k AS (SELECT <keys>, '<pending>' ... )` — see §3.6: codegen
  emits `group by` and `aggregate` as one SQL statement split across two CTEs
  is invalid, so the lowering is defined on the pair:

  `group by` + `aggregate` compile to a single CTE:
  `SELECT <key exprs> AS <names>, <agg> AS <name>, ... FROM stage_{k-1} GROUP BY <key exprs>`

### 3.6 `aggregate`

```ebnf
aggregate_stage = "aggregate" , aggregate_item , { "," , aggregate_item } ;
aggregate_item  = [ identifier , "=" ] , agg_call ;
agg_call        = ( "COUNT" , "(" , "*" , ")" )
                | ( "COUNT" | "SUM" | "AVG" | "MIN" | "MAX" ) ,
                  "(" , [ "DISTINCT" ] , expr , ")" ;
```

- At most once, requires a preceding `group by` (possibly zero rows between).
- Every item must be named with `name = AGG(expr)`; a bare `COUNT(*)` is
  auto-named `count`.
- `DISTINCT` is allowed for `COUNT(DISTINCT expr)`.
- After this stage the live columns are the group keys + aggregate names.
- **IR:** `AggregateStage { items: [name, call][] }`
- **SQL:** joined with `group by` per §3.5.

### 3.7 `having`

```ebnf
having_stage = "having" , expr ;
```

- Requires a preceding `aggregate`; may reference group keys, aggregate names,
  and aggregate calls.
- At most once.
- **IR:** `HavingStage { condition }`
- **SQL:** `stage_k AS (SELECT * FROM stage_{k-1} WHERE <expr>)`

### 3.8 `window`

```ebnf
window_stage = "window" , identifier , "=" , window_call ;
window_call  = window_func , "(" , [ args ] , ")" , "over"
               "(" , [ "partition" , "by" , expr_list ] ,
                     [ "order" , "by" , sort_key_list ] , ")" ;
window_func  = "ROW_NUMBER" | "RANK" | "DENSE_RANK"
             | "LAG" | "LEAD"
             | "SUM" | "AVG" | "COUNT" | "MIN" | "MAX" ;
args         = expr , { "," , expr } ;            (* LAG/LEAD accept offset *)
```

- Adds one column. Repeatable. Allowed after `from` and before `select`.
- The `over` clause needs at least one of `partition by` / `order by`.
- No frame clauses (`rows between ...`) in v1.0.
- Syntax is frozen in Step 2; implementation lands after the core loop
  (until then, use the `sql` hatch).
- **IR:** `WindowStage { name, func, args, partitionBy, orderBy }`
- **SQL:** `stage_k AS (SELECT *, <FUNC>(<args>) OVER (PARTITION BY <...> ORDER BY <...>) AS <name> FROM stage_{k-1})`

### 3.9 `select`

```ebnf
select_stage = "select" , item , { "," , item } ;
item         = "*" | named_expr | expr ;
```

- At most once. After `select`, only `sort`, `skip`, `take`, `distinct` may
  follow.
- `*` must be the only item when used.
- Aggregate calls are rejected here (they belong to `aggregate`).
- **IR:** `SelectStage { items: [{ expr, alias? }], star: boolean }`
- **SQL:** `stage_k AS (SELECT <items> FROM stage_{k-1})`

### 3.10 `sort`

```ebnf
sort_stage = "sort" , sort_key , { "," , sort_key } ;
sort_key   = [ "-" ] , expr , [ "asc" | "desc" ] ;
```

- Leading `-` or trailing `desc` means descending. Repeatable.
- **IR:** `SortStage { keys: [{ expr, descending }] }`
- **SQL:** `stage_k AS (SELECT * FROM stage_{k-1} ORDER BY <keys>)`

### 3.11 `skip` / `take`

```ebnf
skip_stage = "skip" , integer ;
take_stage = "take" , integer ;
```

- At most one of each. Position matters: each becomes its own CTE.
- **IR:** `SkipStage { count }` / `TakeStage { count }`
- **SQL:** `SELECT * FROM stage_{k-1} OFFSET <n>` / `... LIMIT <n>`

### 3.12 `distinct`

```ebnf
distinct_stage = "distinct" ;
```

- At most once; deduplicates the current relation's visible columns.
- **IR:** `DistinctStage`
- **SQL:** `stage_k AS (SELECT DISTINCT * FROM stage_{k-1})`

### 3.13 `sql` — escape hatch (implement in Step 8)

```ebnf
sql_stage = "sql" , string ;
```

- One string literal containing a single `SELECT`; `;` is rejected.
- The previous relation is the table `__input__`; named tables stay visible.
- A `sql` stage starts a fresh relation: phase resets to row and the
  once-per-pipeline stages (`group by`, `aggregate`, `having`, `skip`, `take`,
  `distinct`) may each appear once more after it.
- Column tracking stops at the hatch (semantics treat everything after it as
  opaque).
- **IR:** `RawSqlStage { text }`
- **SQL:** the text becomes its own CTE with `__input__` rewritten to
  `stage_{k-1}`.

## 4. Ordering rules (frozen)

1. `from` first, exactly once.
2. `join` / `left join` only in the row phase, before `group by`.
3. `filter` only in the row phase. Group filtering is `having`.
4. `group by` at most once; `aggregate` at most once and only after
   `group by`; `having` at most once and only after `aggregate`.
5. `derive` may appear in either phase (row or group).
6. `window` may appear before `select`; repeatable.
7. `select` at most once; only `sort` / `skip` / `take` / `distinct` after it.
8. `skip` and `take` at most once each; `distinct` at most once.
9. `sql` never first and never after `select`; it resets phase and singletons
   (§3.13).

## 5. Expression grammar

```ebnf
expr           = or_expr ;
or_expr        = and_expr , { "or" , and_expr } ;
and_expr       = not_expr , { "and" , not_expr } ;
not_expr       = [ "not" ] , comparison ;
comparison     = additive , { comparison_tail } ;
comparison_tail= ( "=" | "==" | "!=" | "<>" | "<" | "<=" | ">" | ">=" ) , additive
               | [ "not" ] , "in" , "(" , expr_list , ")"
               | [ "not" ] , "between" , additive , "and" , additive
               | [ "not" ] , "like" , additive
               | "is" , [ "not" ] , "null" ;
additive       = multiplicative , { ( "+" | "-" | "||" ) , multiplicative } ;
multiplicative = unary , { ( "*" | "/" | "%" ) , unary } ;
unary          = ( "+" | "-" ) , unary | primary ;
primary        = literal | qualified_name | func_call
               | case_expr | cast_expr | "(" , expr , ")" ;
literal        = number | string | "true" | "false" | "null" ;
func_call      = identifier , "(" , [ "distinct" ] , ( expr_list | "*" ) , ")" ;
case_expr      = "case" , "when" , expr , "then" , expr ,
                 { "when" , expr , "then" , expr } ,
                 [ "else" , expr ] , "end" ;
cast_expr      = "cast" , "(" , expr , "as" , type_name , ")" ;
type_name      = identifier ;
expr_list      = expr , { "," , expr } ;
```

Precedence, lowest to highest: `OR` → `AND` → `NOT` → comparison
(`=`, `==`, `!=`, `<>`, `<`, `<=`, `>`, `>=`, `LIKE`, `IN`, `BETWEEN`,
`IS [NOT] NULL`) → `+`, `-`, `||` → `*`, `/`, `%` → unary `+`, `-`.

The `AND` inside `BETWEEN a AND b` binds to `BETWEEN`, not to boolean `AND`.

Scalar functions: `COALESCE`, `NULLIF`, `LOWER`, `UPPER`, `ABS`, `ROUND`,
`LENGTH`, `TRIM`, `CONCAT`. Aggregates: `COUNT`, `SUM`, `AVG`, `MIN`, `MAX`
(valid only in `aggregate`; existing aggregate names may be referenced in
`having`, `derive`, `sort`, `select`).

## 6. Reference query validation

All 21 reference programs in [`examples/reference/`](../examples/reference/)
were hand-parsed against this grammar. Two mechanical translations from the
Python prototype apply (`where` → `filter`/`having`; split `group`), recorded
per query below. No query required a grammar revision after translation; the
two ambiguity questions raised during the pass are resolved by rules already in
§2/§5.

| # | File | Stages | Translation from prototype |
| --- | --- | --- | --- |
| 1 | q01_all_orders | from | — |
| 2 | q02_project_columns | from, select | — |
| 3 | q03_row_filter | from, filter, select | where → filter (row) |
| 4 | q04_filter_and_sort | from, filter, sort | where → filter (row) |
| 5 | q05_derive_column | from, derive, select | — |
| 6 | q06_derive_rank_take | from, derive, select, sort, take | — |
| 7 | q07_inner_join | from, join, select | — |
| 8 | q08_left_join_antijoin | from, left join, filter, select | where → filter (row, after join) |
| 9 | q09_group_count | from, group by, aggregate, sort | split group → group by + aggregate |
| 10 | q10_group_sum_rank | from, derive, group by, aggregate, sort | split group |
| 11 | q11_group_having | from, derive, group by, aggregate, having, sort | split group; where → having |
| 12 | q12_post_group_metric | from, derive, group by, aggregate, derive(group), sort | split group |
| 13 | q13_join_group_topn | from, join, derive, group by, aggregate, sort, take | split group |
| 14 | q14_distinct | from, select, distinct, sort | — |
| 15 | q15_pagination | from, select, sort, skip, take | — |
| 16 | q16_in_list | from, filter, select, sort | where → filter (row) |
| 17 | q17_between | from, filter, select, sort | where → filter (row) |
| 18 | q18_case_bucket | from, derive, select, sort | — |
| 19 | q19_multi_key_group | from, derive, group by, aggregate, sort | split group |
| 20 | q20_full_pipeline | from, join, derive, group by, aggregate, having, derive(group), sort, take | split group; where → having |
| 21 | q21_sql_hatch | from, sql, filter, select, sort | where → filter (row, after sql segment reset) |

### Ambiguity checks

- **q17** `filter order_date BETWEEN '2024-02-01' AND '2024-03-31'` — the `AND`
  is consumed by `BETWEEN` per §5; the grammar is unambiguous because
  `between ... and` is a single comparison tail.
- **q04** `sort -unit_price, order_id` — the leading `-` is the descending
  marker, not unary minus, because `sort_key` owns the optional prefix; `-` as
  subtraction is never at the start of a sort key.
- **q21** `sql` then `filter rn = 1` — §3.13 resets the phase, so `filter` is
  row-phase and legal.
- **q14** `distinct` after `select` — allowed by rule 7.
- **Newlines**: every stage is single-line in the reference set; the separator
  rule (§2) admits multi-line only via `|` or parenthesized expressions that
  stay on one line in v1.0.

Result: **21/21 queries parse unambiguously on paper.** Step 2 complete.

## 7. Formatter (implement in Step 8)

Canonical form: lowercase stage keywords, one stage per line, single-quoted
strings, `-column` for descending sort, spaces around binary operators,
trailing newline. Idempotent. Comments are not preserved.

## 8. Dataset

Reference queries run against the repository dataset in
[`../../examples/spec/`](../../examples/spec/): `orders` (15 rows),
`customers`, `products`, `members`, plus `schema.json`.
