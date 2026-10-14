# UNSeQueL pipeline semantics

The `.unsequel` dialect is a sequential relation language compiled to DuckDB SQL. Each stage
consumes the relation produced by the preceding stage. See the
[grammar](grammar.md) for syntax and the repository-level
[semantics/conformance page](https://github.com/absterjr/UNSeQueL/blob/main/docs/semantics.md)
for shared NULL, CSV, and engine behavior.

## Composition

- `from` starts one program. Joins, filters, derives, group/aggregate units,
  windows, projections, distinct, sort, skip, and take can be interleaved and
  repeated while their referenced columns remain in the current relation.
- Every `group by` must be immediately followed by `aggregate`; additional
  group/aggregate pairs can summarize an earlier aggregate result.
- `filter` filters the relation at its current point. `having` is a familiar
  post-aggregate filter spelling. `select` is a projection, not a program
  terminator; later stages see its output columns.
- `sort` records a hidden row ordinal. Filters, derives, windows, joins,
  projections, and pagination preserve it. A later sort replaces it.
  `group by`, `distinct`, and `sql` reset the ordering boundary. Use an explicit
  final sort when the output order is important.
- A sort key is evaluated at the `sort` stage. The internal ordinal preserves
  that order if a later projection drops the visible key, and `take`/`skip`
  apply against that ordinal.

## Joins and names

UNSeQueL supports inner, left, right, full outer, and cross joins. Qualified names
are resolved against their source label. If two inputs expose the same bare
column name, using that bare name after the join is an error; qualify it (for
example, `orders.id` or `products.id`). `select *` exposes colliding right-side
columns with a source prefix, such as `products_product_id`, rather than
silently dropping one.

Unquoted identifiers follow `[A-Za-z_][A-Za-z0-9_]*` and cannot be reserved
words. Backticks quote column/table names containing spaces, leading digits,

```text
from `Order Details`
select `2024_sales`, `order`
```

Each qualified component is quoted separately: `` `sales table`.`Order Date` ``.
Backticks are UNSeQueL's identifier quoting syntax; ordinary string literals remain
single- or double-quoted.

`select *` can be extended by named expressions:

```text
select *, line_total = quantity * unit_price
```

An added expression may not reuse a live output name; choose a distinct alias.

## Aggregates, windows, and logical order

Aggregate calls (`COUNT`, `SUM`, `AVG`, `MIN`, `MAX`) belong in an `aggregate`
stage, immediately after each `group by`. Later filters/projections reference
the names assigned there. `having` runs before a later `select`; a select alias
is not in scope there, and the compiler reports that explicitly.

`window` stages add window results to the current relation. They can follow a
group/aggregate and appear before or after a projection. Whole-relation windows
such as `window total = SUM(revenue) over ()` are supported. In conventional
SQL evaluation, windows occur after `HAVING` and before final `SELECT`; UNSeQueL's
explicit stage is a transformation interface, not a claim that its source
order lists every internal SQL evaluation phase.

Native scalar date helpers include `strftime` and `date_trunc`. The supported
function set remains smaller than DuckDB's full function catalog.

## CSV loading

CSV headers and all values are scanned before DuckDB loads a file. Inference is
consistent per column:

- all numeric non-empty values infer as `BIGINT` or `DOUBLE`;
- a leading-zero integer, non-numeric value, or mixed/dirty column makes the
  whole column `VARCHAR`;
- ISO `YYYY-MM-DD` and timestamp columns infer as DuckDB `DATE` / `TIMESTAMP`;
- blank cells are NULL; literal `null`, `none`, and `nan` strings remain text;
- duplicate headers and rows with the wrong number of fields are rejected.

`--delimiter C` selects a one-character delimiter. `--column-type table.column=type`
overrides a column; supported aliases include `text`,
`integer`, `number`, `boolean`, `date`, `time`, and `timestamp` (plus DuckDB
decimal types such as `decimal(10,2)`). Options also work with file directives.

## Boundaries

UNSeQueL targets DuckDB only. It does not yet implement CTE syntax, set operators,
or subquery sources; use the SQL hatch for an unsupported query or track the
feature in the [compiler backlog](backlog.md). The grammar deliberately
distinguishes supported syntax from the broader capabilities of DuckDB and
other SQL engines.
