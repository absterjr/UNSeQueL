# UNSeQueL Pipeline Language Specification v1.2

Status: **frozen for v1.2**. Changes require a version bump and re-validation of the
reference queries in [`examples/reference/`](../examples/reference/).

- **v1.0** — initial frozen grammar; 21 reference queries hand-validated.
- **v1.1** — resolves the pre-parser audit: lowering-unit contract for
  `group by` + `aggregate`, mandatory aggregate, completed window EBNF,
  `select`/`not` binding rules, single comparison tail, aggregate
  naming/DISTINCT rules, the complete reserved-word table, number and
  separator semantics, and alias defaults. No reference query changes; the
  corpus re-validates unchanged against v1.1.
- **v1.2** — makes stages composable across projections, filters, joins,
  multiple group/aggregate units, and pagination; adds right/full/cross joins,
  backtick-quoted identifiers, `select *, name = expression`, whole-relation
  windows (`OVER ()`), and native `strftime`/`date_trunc`; rejects ambiguous
  unqualified references after joins. Golden SQL now carries an internal row
  ordinal so sort order remains well-defined through later stages.

Stack decisions are locked: TypeScript/Node compiler, DuckDB first target,
hand-written recursive descent parser, one CTE per pipeline stage.

## 1. Program model

A UNSeQueL program is an ordered list of **stages**. Each stage transforms the
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

### 1.1 Grammar stages vs lowering units

Two numbering systems exist; both are part of the contract.

- **Grammar stage (G)** — one source construct, 1-based in source order.
  This is what `compile --stage N` / `preview --stage N` count, and what
  parse errors report.
- **Lowering unit (U)** — the SQL-emitting group that becomes one CTE.
  Every stage is its own unit **except `group by` + `aggregate`**, which
  compile to a single CTE. CTEs are named `stage_{U}`.
- Truncating at grammar stage N keeps the unit containing N; truncating at
  `group by` therefore shows the handled unit's aggregated relation. The
  final statement selects from the last included unit's CTE.

Worked map for the program above (7 grammar stages, 6 units):

| G | Stage | U / CTE |
| --- | --- | --- |
| 1 | from | stage_1 |
| 2 | derive | stage_2 |
| 3 | group by | — (unit 3) |
| 4 | aggregate | stage_3 |
| 5 | having | stage_4 |
| 6 | sort | stage_5 |
| 7 | take | stage_6 |

## 2. Lexical structure

| Token | Rule |
| --- | --- |
| keyword | one of the reserved words in §2.1; case-insensitive |
| identifier | `[A-Za-z_][A-Za-z0-9_]*`; qualified names are `identifier.identifier` |
| quoted identifier | backtick-quoted single-line name; double backticks escape a backtick; quote each qualified part separately, e.g. `` `Order Date` `` or `` `order`.`Order Date` `` |
| integer | `digit , { digit }`; leading zeros allowed (`007`) |
| number | `integer` or `integer "." digit { digit }`; no leading sign, no leading/trailing dot (`12.` is not a number; the parser reports it) |
| string | single- or double-quoted, **single line**; escapes `\\` `\'` `\"` `\n`; doubled quotes (`''`, `""`) also escape; canonical output uses single quotes |
| comment | `#` or `--` to end of line; not preserved by the formatter |
| separator | a newline or `|` at parenthesis depth zero terminates a stage; a newline at depth > 0 is a parse error ("a stage cannot span lines") |

Canonical spelling (formatter): stage keywords lowercase; expression keywords
uppercase (`AND`, `IS NULL`, `CASE ... END`); `-column` for descending sort.
The lexer stores keywords in lowercase; the formatter restores case by
grammatical role.

### 2.1 Reserved words (complete)

Reserved words cannot be used as **unquoted** identifiers. Use backticks to
quote a reserved, spaced, or digit-leading name; the dataset usually avoids
collisions (`order_id` is a distinct identifier from the keyword `order`).

| Group | Words |
| --- | --- |
| stages | `from` `join` `left` `right` `full` `cross` `filter` `derive` `group` `by` `aggregate` `having` `window` `select` `sort` `skip` `take` `distinct` `sql` |
| clauses / modifiers | `on` `as` `over` `partition` `order` `asc` `desc` |
| expressions | `and` `or` `not` `in` `like` `between` `is` `null` `case` `when` `then` `else` `end` `cast` `true` `false` |

`group by`, `left join`, `right join`, `full join`, and `cross join` are
two-word phrases made from their listed keywords.

Function and aggregate names are **not** reserved and are case-insensitive:
`count`, `sum`, `avg`, `min`, `max`, `coalesce`, `nullif`, `lower`, `upper`,
`abs`, `round`, `length`, `trim`, `concat`, `row_number`, `rank`,
`dense_rank`, `lag`, `lead`, `strftime`, `date_trunc`.

Deliberate v1.2 lexical consequences:

- Unquoted identifiers cannot use reserved words or spaces; quote such names
  with backticks (e.g. `` `order` ``, `` `Order Date` ``).
- `--` starts a comment even when written as adjacent subtraction
  (`a--b` is `a` + comment; write `a - -b` for double negation). This matches
  SQL.

Deliberate dialect changes from the Python prototype (recorded for
cross-implementation awareness):

| Prototype | UNSeQueL v1.x | Why |
| --- | --- | --- |
| `where` position-sensitive | `filter` (current relation) + `having` (group-oriented spelling) | explicit stage names clarify the pipeline position |
| `group keys (aggs)` combined | `group by` + `aggregate` stages (one lowering unit) | one concept per stage; maps 1:1 to SQL while keeping one CTE per unit |
| subquery sources accepted | not part of v1.x | no silent data loss; subqueries are a future feature |
| `window` reserved | `window` implemented | ranking queries work natively; the prototype uses the `sql` hatch |
| identifiers may contain `$` | `[A-Za-z_][A-Za-z0-9_]*` only | stricter lexical rule |
| strings may contain raw newlines | strings are single-line (`\n` escape) | keeps one stage per line |
| an ordered `.usql` surface also exists | `.unsequel` is pipeline-only | the prototype hosts two surfaces; the compiler one |

## 3. Stages

The TypeScript contract for every node below is `plq/src/ast.ts`; each stage
node carries its 1-based grammar index and its source span.

### 3.1 `from`

```ebnf
from_stage = "from" , source ;
source     = qualified_name , [ "as" , identifier ] ;
```

- Exactly one `from`, and it is the first stage.
- The table name is matched against the schema by name. The source **label**
  (used for qualified references and join collisions) is the alias if given,
  otherwise the last segment of the name (`sales.orders` → `orders`).
- Subquery sources are not part of v1.x.
- **IR:** `FromStage { source: { name, alias? }, index, span }`
- **SQL (U=1):** `stage_1 AS (SELECT * FROM <name> [AS <alias>])`

### 3.2 `join` / `left join`

```ebnf
join_stage = [ "left" | "right" | "full" ] , "join" , source , "on" , expr
           | "cross" , "join" , source ;
```

- Joins may occur anywhere the current relation has the join keys, including
  after grouping and pagination; any number of joins, in source order.
- The ON expression may not contain aggregate calls (§4, rule 12).
- References in `ON` that exist on both sides must be qualified; use the
  incoming and right-side source labels. Other bare names may resolve against
  the incoming relation, but qualification is recommended.
- Right-table labels default as in §3.1. A right column whose name collides
  with a live column is exposed as `<label>_<column>` for `select *` output.
- **IR:** `JoinStage { kind: "inner" | "left" | "right" | "full" | "cross", source, on }`
- **SQL:** `stage_u AS (SELECT <prev>.*, <right cols> FROM stage_{u-1} [LEFT] JOIN <source> ON <expr>)`

### 3.3 `filter`

```ebnf
filter_stage = "filter" , expr ;
```

- Filters the current relation, including a relation that has already been
  grouped or projected. `having` is available as a familiar post-aggregate
  spelling, but both are relation filters at their position in the pipeline.
- Multiple `filter` stages apply in order (equivalent to successive WHEREs).
- **IR:** `FilterStage { condition }`
- **SQL:** `stage_u AS (SELECT * FROM stage_{u-1} WHERE <expr>)`

### 3.4 `derive`

```ebnf
derive_stage = "derive" , named_expr , { "," , named_expr } ;
named_expr   = identifier , "=" , expr ;
```

- Adds computed columns; a later row-phase `derive` may reference earlier
  derived names (left-to-right).
- Row phase (before `group by`): expressions may not contain aggregate calls.
- Group phase (after `aggregate`): expressions may reference group keys,
  aggregate names, and earlier group-phase derives.
- A row-phase derived name may be shadowed by an aggregate name after
  `group by`; resolution follows the phase scope.
- Multi-item `derive` items are evaluated left to right.
- **IR:** `DeriveStage { items: [name, expr][], phase: "row" | "group" }`
- **SQL:** `stage_u AS (SELECT *, (<expr>) AS <name>, ... FROM stage_{u-1})`

### 3.5 `group by`

```ebnf
group_stage = "group" , "by" , key , { "," , key } ;
key         = identifier | named_expr ;
```

- Repeatable, and each `group by` **must be immediately followed by `aggregate`**
  (no stage may appear between them). A `group by` without a following
  `aggregate` is a parse error.
- Keys are bare column names or named expressions; keys may not contain
  aggregate calls.
- After the group unit, live columns are the group keys plus the aggregate
  names (plus later group-phase derives).
- **IR:** `GroupByStage { keys: [name, expr][] }`
- **SQL:** none alone — lowering is defined on the unit in §3.6.

### 3.6 `aggregate`

```ebnf
aggregate_stage = "aggregate" , aggregate_item , { "," , aggregate_item } ;
aggregate_item  = [ identifier , "=" ] , agg_call ;
agg_call        = ( "COUNT" , "(" , "*" , ")" )
                | ( "COUNT" | "SUM" | "AVG" | "MIN" | "MAX" ) ,
                  "(" , [ "DISTINCT" ] , expr , ")" ;
```

- Required immediately after each `group by`; groups may be aggregated again
  later in the same pipeline.
- Every item must be named `name = AGG(expr)`; a bare `COUNT(*)` is
  auto-named `count`.
- `DISTINCT` is valid only for `COUNT`; `*` is valid only for `COUNT` and
  only without `DISTINCT` (`COUNT(DISTINCT *)` is rejected).
- Aggregate arguments may not contain aggregate calls (no nesting).
- **IR:** `AggregateStage { items: [name, call][] }`
- **SQL (the group unit, one CTE):**
  `stage_u AS (SELECT <key exprs> AS <names>, <agg> AS <name>, ... FROM stage_{u-1} GROUP BY <key exprs>)`

### 3.7 `having`

```ebnf
having_stage = "having" , expr ;
```

- Repeatable after an `aggregate` stage; it filters the current grouped relation.
- May reference group keys, aggregate names, and group-phase derives defined
  **before** it. Aggregate calls are not allowed here — reference the
  aggregate name (§3.6).
- **IR:** `HavingStage { condition }`
- **SQL:** `stage_u AS (SELECT * FROM stage_{u-1} WHERE <expr>)`

### 3.8 `window`

```ebnf
window_stage  = "window" , identifier , "=" , window_call ;
window_call   = window_func , "(" , [ window_args ] , ")" ,
                "over" , "(" , window_spec , ")" ;
window_args   = expr , { "," , expr } ;
window_spec   = [ "partition" , "by" , expr_list ] ,
                [ "order" , "by" , sort_key_list ] ;
sort_key_list = sort_key , { "," , sort_key } ;
sort_key      = [ "-" ] , expr , [ "asc" | "desc" ] ;
window_func   = "ROW_NUMBER" | "RANK" | "DENSE_RANK"
              | "LAG" | "LEAD"
              | "SUM" | "AVG" | "COUNT" | "MIN" | "MAX" ;
```

- Adds one named column. Repeatable.
- May appear anywhere after `from`, including after grouping or projection.
- Empty `over ()` is valid for whole-relation windows such as `SUM(x) over ()`.
- `COUNT(*) OVER (...)` is the only wildcard form; `COUNT(DISTINCT ...)`
  inside a window is not part of v1.x.
- Disambiguation: a window function is recognized as `identifier "(" … ")"`
  immediately followed by the keyword `over`; otherwise the same shape is a
  scalar/aggregate call.
- No frame clauses (`rows between ...`) in v1.x.
- A window stage may follow `sort`/`skip`/`take`; it adds its column over the
  current relation and preserves the established order.
- Syntax is frozen in v1.2; parsing and DuckDB lowering are
  implemented.
- **IR:** `WindowStage { name, func, args, partitionBy, orderBy }`
- **SQL:** `stage_u AS (SELECT *, <FUNC>(<args>) OVER (<spec>) AS <name> FROM stage_{u-1})`

### 3.9 `select`

```ebnf
select_stage = "select" , item , { "," , item } ;
item         = "*" | named_expr | expr ;
```

- Repeatable. The projection changes the current relation; subsequent stages
  see the projected columns.
- **Binding rule:** a select item that begins with a single identifier
  followed by `=` is a **named item** (alias); otherwise it is an
  expression. `select x = y` therefore means "output column x from
  expression y", never a boolean comparison.
- `*` may be combined with named expressions (`select *, total = price * qty`).
  An added name may not duplicate a live column.
- Aggregate calls are rejected here (they belong to `aggregate`).
- Default output names: explicit alias; else a bare identifier's name; else
  the lowercase function name for calls; else `expression`.
- **IR:** `SelectStage { items: [{ expr, alias? }], star: boolean }`
- **SQL:** `stage_u AS (SELECT <items> FROM stage_{u-1})`

### 3.10 `sort`

```ebnf
sort_stage = "sort" , sort_key , { "," , sort_key } ;
sort_key   = [ "-" ] , expr , [ "asc" | "desc" ] ;
```

- Repeatable. Leading `-` or trailing `desc` means descending; the formatter
  canonicalizes to `-`. The compiler carries sort order through subsequent
  row-preserving stages with a hidden ordinal and removes it from visible output.
- **IR:** `SortStage { keys: [{ expr, descending }] }`
- **SQL:** the stage assigns a hidden `ROW_NUMBER() OVER (ORDER BY <keys>)`;
  the final query orders by and then removes that internal ordinal.

### 3.11 `skip` / `take`

```ebnf
skip_stage = "skip" , integer ;
take_stage = "take" , integer ;
```

- Repeatable and composable in any order; each is its own CTE. If an earlier
  `sort` exists, pagination uses that order. Without a sort, row choice is
  engine-dependent.
- Only non-negative integers; reals and negative signs are parse errors.
- **IR:** `SkipStage { count }` / `TakeStage { count }`
- **SQL:** `SELECT * FROM stage_{u-1} OFFSET <n>` / `... LIMIT <n>`

### 3.12 `distinct`

```ebnf
distinct_stage = "distinct" ;
```

- Repeatable. `distinct` resets any prior sort order; sort again afterward if
  ordered output is required.
- Deduplicates the current relation's visible columns.
- **IR:** `DistinctStage`
- **SQL:** `stage_u AS (SELECT DISTINCT * FROM stage_{u-1})`

### 3.13 `sql` — escape hatch

```ebnf
sql_stage = "sql" , string ;
```

- One string literal containing a single query that starts with `SELECT` or
  `WITH` (leading whitespace and comments are allowed); a `;` outside string
  literals and comments is rejected.
- `join` may not follow a `sql` stage: the hatch output's columns are opaque,
  so the compiler cannot qualify or rename them safely. Join before the hatch
  or express the join in SQL.
- The previous relation is the table `__input__`; named tables stay visible.
- A `sql` stage starts a fresh, opaque relation and clears prior sort-order
  tracking. `from` remains program-global; other stages can follow the hatch
  subject to the normal relation/schema rules.
- Column tracking stops at the hatch (semantics treat everything after it as
  opaque).
- **IR:** `RawSqlStage { text }`
- **SQL:** the text becomes its own CTE with `__input__` rewritten to
  `stage_{u-1}` (substitution never touches string literals).

## 4. Ordering and composition rules (v1.2)

1. `from` is first and appears exactly once.
2. Any join kind may follow the current relation, including after grouping,
   projection, or pagination. A join after `sql` is rejected because the hatch
   output schema is opaque. Ambiguous bare references after a join are errors;
   qualify them with a source label.
3. Every `group by` must be immediately followed by `aggregate`. The pair may
   repeat: a later group can aggregate the earlier aggregate's output.
4. `filter`, `having`, `derive`, `window`, and `select` operate on the relation
   produced at their position and can be repeated/interleaved. Aggregate calls
   themselves remain confined to `aggregate`; later expressions refer to their
   named outputs.
5. `select *` can be extended by named expressions. If an added output name
   would duplicate a live column, the compiler reports it instead of silently
   overwriting it.
6. `sort`, `skip`, and `take` can occur at any point and can be repeated. A
   hidden row ordinal preserves sort order through row-preserving stages and
   pagination; `group by`, `distinct`, and `sql` establish a new ordering
   boundary. A projection may drop the visible sort keys while the ordinal
   preserves the established order.
7. `distinct` can occur anywhere; it resets previous sort order. Add a new
   `sort` when output order matters.
8. Aggregate calls are valid only inside `aggregate`. Aggregate arguments may
   not contain aggregate calls; `DISTINCT` and `*` are COUNT-only (`COUNT(*)`).
9. Backtick-quoted identifiers can name reserved, spaced, and digit-leading
   columns; dots separate qualified components and must be quoted separately.

## 5. Expression grammar

```ebnf
expr            = or_expr ;
or_expr         = and_expr , { "or" , and_expr } ;
and_expr        = not_expr , { "and" , not_expr } ;
not_expr        = [ "not" ] , comparison ;
comparison      = additive , [ comparison_tail ] ;
comparison_tail = ( "=" | "==" | "!=" | "<>" | "<" | "<=" | ">" | ">=" ) , additive
                | [ "not" ] , "in" , "(" , expr_list , ")"
                | [ "not" ] , "between" , additive , "and" , additive
                | [ "not" ] , "like" , additive
                | "is" , [ "not" ] , "null" ;
additive        = multiplicative , { ( "+" | "-" | "||" ) , multiplicative } ;
multiplicative  = unary , { ( "*" | "/" | "%" ) , unary } ;
unary           = ( "+" | "-" ) , unary | primary ;
primary         = literal | qualified_name | func_call
                | case_expr | cast_expr | "(" , expr , ")" ;
literal         = number | string | "true" | "false" | "null" ;
func_call       = identifier , "(" , [ "distinct" ] , ( expr_list | "*" ) , ")" ;
case_expr       = "case" , "when" , expr , "then" , expr ,
                  { "when" , expr , "then" , expr } ,
                  [ "else" , expr ] , "end" ;
cast_expr       = "cast" , "(" , expr , "as" , type_name , ")" ;
type_name       = identifier ;
expr_list       = expr , { "," , expr } ;
```

Binding rules:

- **Single comparison tail.** A comparison has at most one tail:
  `a < b < c` is a parse error ("chained comparisons are not supported").
- **`not` binding.** A prefix `not` negates the whole following comparison and
  stays a `UnaryExpr("not", …)`: `not a in (1, 2)` = `NOT (a IN (1, 2))`.
  Infix `not in` / `not like` / `not between` is recognized only immediately
  after an operand and sets the tail's `negated` flag. The forms are
  semantically equivalent but keep different AST shapes; consumers must handle
  both. `not not` is rejected.
- The `AND` inside `BETWEEN a AND b` binds to `BETWEEN`, not to boolean
  `AND`.
- `COUNT(*)` is the only wildcard call; `COUNT(DISTINCT expr)` the only
  distinct call form. `*` is rejected as a general function argument.

Precedence, lowest to highest: `OR` → `AND` → `NOT` → comparison
(`=`, `==`, `!=`, `<>`, `<`, `<=`, `>`, `>=`, `LIKE`, `IN`, `BETWEEN`,
`IS [NOT] NULL`) → `+`, `-`, `||` → `*`, `/`, `%` → unary `+`, `-`.

Scalar functions: `COALESCE`, `NULLIF`, `LOWER`, `UPPER`, `ABS`, `ROUND`,
`LENGTH`, `TRIM`, `CONCAT`, `STRFTIME`, `DATE_TRUNC`. Aggregates: `COUNT`, `SUM`, `AVG`, `MIN`, `MAX`
(valid only in `aggregate`). Window functions per §3.8.

## 6. Reference query validation

All 21 reference programs in [`examples/reference/`](../examples/reference/)
are parsed, schema-checked, compiled, and executed in CI. v1.2 qualifies
duplicated join keys and aliases the selected left-side column; the original
two dialect translations remain (`where` → `filter`/`having`, and combined
`group` → `group by` + `aggregate`).

| # | File | Stages | Translation from prototype |
| --- | --- | --- | --- |
| 1 | q01_all_orders | from | — |
| 2 | q02_project_columns | from, select | — |
| 3 | q03_row_filter | from, filter, select | where → filter (row) |
| 4 | q04_filter_and_sort | from, filter, sort | where → filter (row) |
| 5 | q05_derive_column | from, derive, select | — |
| 6 | q06_derive_rank_take | from, derive, select, sort, take | — |
| 7 | q07_inner_join | from, join, select | qualify repeated `product_id` |
| 8 | q08_left_join_antijoin | from, left join, filter, select | qualify `orders.customer`; where → filter |
| 9 | q09_group_count | from, group by, aggregate, sort | split group → group by + aggregate |
| 10 | q10_group_sum_rank | from, derive, group by, aggregate, sort | split group |
| 11 | q11_group_having | from, derive, group by, aggregate, having, sort | split group; where → having |
| 12 | q12_post_group_metric | from, derive, group by, aggregate, derive(group), sort | split group |
| 13 | q13_join_group_topn | from, join, derive, group by, aggregate, sort, take | split group; qualify join key |
| 14 | q14_distinct | from, select, distinct, sort | — |
| 15 | q15_pagination | from, select, sort, skip, take | — |
| 16 | q16_in_list | from, filter, select, sort | where → filter (row) |
| 17 | q17_between | from, filter, select, sort | where → filter (row) |
| 18 | q18_case_bucket | from, derive, select, sort | — |
| 19 | q19_multi_key_group | from, derive, group by, aggregate, sort | split group |
| 20 | q20_full_pipeline | from, join, derive, group by, aggregate, having, derive(group), sort, take | split group; qualify join key; where → having |
| 21 | q21_sql_hatch | from, sql, filter, select, sort | where → filter (row, after sql segment reset) |

### Ambiguity checks

- **q17** `filter order_date BETWEEN '2024-02-01' AND '2024-03-31'` — the `AND`
  is consumed by `BETWEEN` (§5); resolved by the single comparison tail.
- **q04** `sort -unit_price, order_id` — the leading `-` is the descending
  marker, not unary minus, because `sort_key` owns the optional prefix; `-` as
  subtraction never starts a sort key.
- **q21** `sql` then `filter rn = 1` — §3.13 resets the phase, so `filter` is
  row-phase and legal.
- **q14** `distinct` after `select` — allowed by rule 9; `distinct` is before
  the tail, as rule 11 requires.
- **Newlines**: every stage is one line; newlines at parenthesis depth zero
  separate stages and a newline inside parentheses is a parse error (§2).

Result: **21/21 queries parse and execute** under v1.2; golden SQL and
handwritten DuckDB parity suites are CI gates.

## 7. Formatter

Canonical form: lowercase stage keywords, one stage per line, single-quoted
strings, `-column` for descending sort, spaces around binary operators,
trailing newline. Idempotent. Leading `# source name: path` and `# schema:
path` file directives are preserved; every other comment is dropped. `sql`
payloads are re-quoted with newlines escaped so a stage never spans lines
(§2). Implemented in `plq/src/format.ts` with the `fmt --write|--check` CLI
command.

## 8. Dataset

Reference queries run against the repository dataset in
[`../../examples/spec/`](../../examples/spec/): `orders` (15 rows),
`customers`, `products`, `members`, plus `schema.json`.
