# UNSeQueL Pipeline Syntax — POC v0.1 (FROZEN)

This is the proof-of-concept syntax for pipeline-style UNSeQueL queries. The
grammar below is frozen for the POC: changes require a new syntax version, not
a silent edit.

## Prior art (do not reinvent)

| Language | Idea adopted |
| --- | --- |
| [PRQL](https://prql-lang.org) | Newline/`\|` as the pipe; one lowercase transform per line; `group keys (aggregate …)`; `-column` for descending sort; `derive` for computed columns; `take`/`skip` instead of `LIMIT`/`OFFSET` |
| [LINQ](https://learn.microsoft.com/en-us/dotnet/csharp/linq/) | Query expressions lowered to chained operators (`Where`, `Select`, `GroupBy`, `OrderBy`, `Skip`, `Take`); a single `where` operator regardless of pre- or post-aggregation position |
| [Malloy](https://malloydata.dev) | Stage list as the query shape; aggregates declared with names next to their group keys |
| gcQL / Coveo QPL | Confirmation that pipe-based stages are an established interface style (found while checking "Melona"; no established query language by that name exists — the closest known designs are Malloy and gcQL) |

## What is reused (already built)

The pipeline is a **front-end only**. `unsequel.pipeline.parse_pipeline`
lowers each stage into the existing ordered-clause `Query` AST and executes it
with the existing engine, evaluator, I/O, and CLI. No new execution semantics
were invented:

| Pipeline stage | Lowered to (existing ordered clause) |
| --- | --- |
| `from` | `FROM` |
| `join` / `left join` | `JOIN` / `LEFT JOIN` |
| `where` before `group` | `WHERE` (multiple stages combine with `AND`) |
| `where` after `group` | `HAVING` (the LINQ-style single-filter insight) |
| `derive` before `group` | inlined expression, remembered by name |
| `derive` after `group` | extra `SELECT` item |
| `group` | `GROUP BY` + named aggregate `SELECT` items |
| `select` | `SELECT` |
| `sort` | `ORDER BY` |
| `take` / `skip` | `LIMIT` / `OFFSET` |
| `distinct` | `SELECT DISTINCT` |

## Grammar (EBNF, frozen v0.1)

```ebnf
pipeline      = stage , { stage } ;
stage         = one line of tokens ; stages are separated by a newline
                or by "|" at parenthesis depth zero ;
stage         = from_stage | join_stage | where_stage | derive_stage
              | group_stage | select_stage | sort_stage | take_stage
              | skip_stage | distinct_stage ;

from_stage    = "from" , table_ref ;
join_stage    = ( "join" , table_ref , "on" , expr )
              | ( "left" , "join" , table_ref , "on" , expr ) ;
where_stage   = "where" , expr ;
derive_stage  = "derive" , named_expr , { "," , named_expr } ;
group_stage   = "group" , key_list , "(" , aggregate_list , ")" ;
key_list      = key , { "," , key } ;
key           = identifier | named_expr ;
aggregate_list= aggregate , { "," , aggregate } ;
aggregate     = identifier , "=" , agg_call ;
agg_call      = ( "COUNT" , "(" , "*" , ")" )
              | ( "COUNT" | "SUM" | "AVG" | "MIN" | "MAX" , "(" , expr , ")" ) ;
select_stage  = "select" , item , { "," , item } ;
item          = "*" | identifier | named_expr ;
sort_stage    = "sort" , sort_key , { "," , sort_key } ;
sort_key      = [ "-" ] , expr ;
take_stage    = "take" , integer ;
skip_stage    = "skip" , integer ;
distinct_stage= "distinct" ;
named_expr    = identifier , "=" , expr ;
```

Ordering rules (frozen):

1. The first stage must be `from`.
2. `join`, `derive`, `where`, and `group` may not appear after `select`.
3. `group` may appear at most once; `take`, `skip`, and `distinct` likewise.
4. `where` before `group` filters rows; `where` after `group` filters groups.
5. Aggregates exist only inside `group`; `select` cannot compute new ones.
6. Named entries use `name = expression`; a bare identifier is its own name.
   A bare `COUNT(*)` inside `group` is auto-named `count`.

Expression syntax is the existing UNSeQueL expression language (`+ - * / %`,
comparisons, `AND/OR/NOT`, `LIKE`, `IN`, `BETWEEN`, `CASE`, `CAST`, `||`,
aggregate and scalar functions) and is unchanged by this POC.

## Worked examples (hand translated)

All examples run against the repository sample data:

```bash
python -m unsequel run examples/pipeline/revenue.pusql --pipeline \
  --data orders=examples/orders.csv
```

### 1. Aggregation + ranking — `examples/pipeline/revenue.pusql`

```text
from orders
derive total = quantity * unit_price
group country (revenue = SUM(total), orders = COUNT(*))
sort -revenue
```

SQL equivalent:

```sql
SELECT country, SUM(quantity * unit_price) AS revenue, COUNT(*) AS orders
FROM orders
GROUP BY country
ORDER BY revenue DESC;
```

### 2. Join + top-N — `examples/pipeline/top_customers.pusql`

```text
from orders
join customers on customer = customers.customer
group customer (revenue = SUM(quantity * unit_price))
sort -revenue
take 2
```

LINQ equivalent: `orders.Join(customers, …).GroupBy(…).OrderByDescending(…).Take(2)`.

### 3. Group filter + derived metric — `examples/pipeline/avg_order_value.pusql`

```text
from orders
group customer (orders = COUNT(*), revenue = SUM(quantity * unit_price))
where revenue >= 50
derive avg_order = revenue / orders
sort -avg_order
```

The single `where` replaces both SQL `WHERE` and `HAVING`; its meaning comes
from its position in the pipeline.

### 4. Data quality — `examples/pipeline/unmatched_orders.pusql`

```text
from orders
left join members on customer = members.customer
where members.customer IS NULL
select order_id, customer
```

### 5. Deduplication — `examples/pipeline/countries.pusql`

```text
from orders
select country
distinct
sort country
```

### 6. Pagination — `examples/pipeline/page.pusql`

```text
from orders
select order_id, customer
sort order_id
skip 2
take 2
```

## POC boundaries

- One stage per line (or `|`-separated); multi-line stages are not allowed.
- Only `join` and `left join` are exposed; the ordered syntax still offers the
  other join kinds while the pipeline grammar settles.
- No CTEs, no subquery sources, no set operations, and no window functions in
  the pipeline syntax yet.
- `where`/`derive` after `select` are rejected to keep lowering faithful.
