# UNSeQueL

[![CI](https://github.com/absterjr/UNSeQueL/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/absterjr/UNSeQueL/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Discussions](https://img.shields.io/badge/community-discussions-blueviolet)](https://github.com/absterjr/UNSeQueL/discussions)

[Project site](https://absterjr.github.io/UNSeQueL/) ·
[Discussions](https://github.com/absterjr/UNSeQueL/discussions)

UNSeQueL is an open-source, FROM-first query language for writing SQL-like
transformations in execution order — a stage-by-stage way to explore relational
queries. Its learning benefit is a hypothesis, not an established result.

Instead of writing:

```sql
SELECT country, SUM(quantity * unit_price) AS revenue
FROM orders
WHERE quantity > 0
GROUP BY country
HAVING COUNT(*) > 1
ORDER BY revenue DESC
LIMIT 10;
```

you write:

```text
FROM orders
WHERE quantity > 0
GROUP BY country
HAVING COUNT(*) > 1
SELECT country, SUM(quantity * unit_price) AS revenue
ORDER BY revenue DESC
LIMIT 10
```

This example shows the relation stages in scope, not every SQL evaluation
phase. SQL window functions execute after grouping/HAVING and before final
projection: the Python `.usql` prototype has no native window stage, while the
`.unsequel` dialect provides an explicit `window` stage.

The point is not to replace SQL: learners still need to read and write SQL.
The design goal is to make the supported relational stages visible while you
read top-to-bottom. We have not yet tested whether this improves learning or
transfer; see [the validation plan](docs/learning-validation.md).

Questions, ideas, and query showcases are welcome in
[Discussions](https://github.com/absterjr/UNSeQueL/discussions); bugs and
feature requests have issue templates.

## Repository map

`.unsequel` is the language. Two implementations run it: the production
TypeScript compiler (DuckDB target) and the Python package (ported front-end,
analyzer, and DuckDB code generator — `python -m unsequel run query.unsequel`).
The older `.usql` (ordered) and `.pusql` (pipeline) Python dialects are legacy
prototypes retained for comparison and are being retired.

| Surface | Extension | Tool | Dialect |
| --- | --- | --- | --- |
| UNSeQueL | `.unsequel` | `unsequel` CLI, [VS Code extension](compiler/editors/vscode/README.md), or `python -m unsequel` | composable stages, `filter`/`having`, split `group by` + `aggregate`, windows, nested programs (`with`/set/subqueries), backtick identifiers — [grammar v1.3](compiler/docs/grammar.md) |
| Ordered (legacy) | `.usql` | `python -m unsequel` | `FROM ... WHERE ... GROUP BY ... HAVING ... SELECT ... ORDER BY ...` |
| Pipeline (legacy) | `.pusql` | `python -m unsequel ... --pipeline` (inferred for `.pusql`) | one stage per line; `where` is position-sensitive (row vs group); `group keys (aggregates)` |

New here? Run one self-contained query — no schema or data flags required:

```bash
cd compiler
npm install -g .
unsequel run examples/real/r01_top_products.unsequel
```

`.unsequel` files can declare their own sources, run a line selection, read from
stdin, or open an interactive `unsequel repl`. A VS Code extension (syntax
highlighting, Run Selection/File/REPL) is in
[compiler/editors/vscode](compiler/editors/vscode/README.md).

## Status

The Python 0.2.0 prototype implements an ordered SQL-like subset with a lexer,
semantic checks, an in-memory relational executor, CTEs, nested sources, set
operations, five join types, and a SQLite adapter. It also hosts a port of the
canonical `.unsequel` grammar v1.3: `python -m unsequel run query.unsequel`
parses, checks, compiles, and executes the same programs as the TypeScript
compiler on DuckDB (analyzer, codegen, `check`/`compile`/`preview` included).
The separate production compiler is described below; neither surface claims
full SQL compatibility.

## Install the Python prototype

Requires Python 3.10 or newer.

The project is distributed from this repository (not yet on PyPI). Install the
current code directly from GitHub:

```bash
python -m pip install "git+https://github.com/absterjr/UNSeQueL.git"
```

For local development:

```bash
git clone https://github.com/absterjr/UNSeQueL.git
cd UNSeQueL
python -m pip install -e .
```

The Python prototype command is `unsequel-python` in a virtual environment or
pipx environment. `python -m unsequel` is the portable fallback. The production
compiler's Node command is `unsequel`.

## Run a Python prototype query

The repository includes a small example dataset:

```bash
python -m unsequel run examples/revenue.usql --data orders=examples/orders.csv
```

You can also run it without installing the console command:

```bash
python -m unsequel run examples/revenue.usql --data orders=examples/orders.csv
```

Join example:

```bash
python -m unsequel run examples/customer_revenue.usql \
  --data orders=examples/orders.csv \
  --data customers=examples/customers.csv
```

The advanced example combines a CTE, grouping, `COUNT(DISTINCT ...)`, and
ordered output:

```bash
python -m unsequel run examples/advanced.usql --data orders=examples/orders.csv
```

The `--data` option uses `name=path`. JSON files containing an array of
objects are also supported.

To query an existing SQLite database, expose all its tables and views with the
standard-library adapter:

```bash
python -m unsequel run query.usql --sqlite warehouse.db
```

## Language shape

The core supports these stages:

```text
WITH recent AS (query)
FROM table | (query) AS alias
INNER JOIN other_table ON condition
LEFT JOIN other_table ON condition
RIGHT JOIN other_table ON condition
FULL JOIN other_table ON condition
CROSS JOIN other_table
WHERE row_condition
GROUP BY grouping_expression
HAVING group_condition
SELECT [DISTINCT] output_expression AS alias
ORDER BY expression ASC|DESC
LIMIT number
OFFSET number
UNION [ALL] query
INTERSECT [ALL] query
EXCEPT [ALL] query
```

`FROM` and `SELECT` are required. Other stages are optional, but they must
appear in the order above. Multiple `JOIN` clauses are allowed directly after
`FROM`.

## Pipeline syntax

The pipeline surface — one transform per line, PRQL-style, lowered onto the
same engine and specified in [docs/pipeline-spec.md](docs/pipeline-spec.md)
(v0.3.1). This section documents the **Python prototype dialect** (`.pusql`):
`where` is position-sensitive and `group` combines keys with aggregates. The
production compiler uses the dialect in the repository map above.

```text
from orders
derive total = quantity * unit_price
group country (revenue = SUM(total), orders = COUNT(*))
sort -revenue
```

Run a pipeline query with the `--pipeline` flag:

```bash
python -m unsequel run examples/pipeline/revenue.pusql --pipeline \
  --data orders=examples/orders.csv
```

With a schema file, the pipeline can be validated, compiled to DuckDB SQL
(one CTE per stage), and stepped through stage by stage:

```bash
# validate columns/types before running anything
python -m unsequel check examples/spec/q20_full_pipeline.pusql --pipeline \
  --schema examples/spec/schema.json

# see the generated SQL
python -m unsequel compile examples/spec/q20_full_pipeline.pusql \
  --schema examples/spec/schema.json

# preview the intermediate result after stage 3
python -m unsequel preview examples/spec/q20_full_pipeline.pusql \
  --schema examples/spec/schema.json --stage 3 \
  --data orders=examples/spec/orders.csv --data products=examples/spec/products.csv
```

`preview` and `run --engine duckdb` execute SQL and need the `duckdb` extra
(`pip install "unsequel[duckdb]"`); `check` and `compile` are pure
dependency-free text operations.

When the pipeline cannot express something yet (window functions today), splice
raw SQL through the escape hatch. The previous relation is the table
`__input__`; on the memory engine it runs via stdlib SQLite, on DuckDB it
becomes its own CTE:

```bash
python -m unsequel run examples/spec/q21_sql_hatch.pusql --pipeline \
  --data orders=examples/spec/orders.csv
```

Format `.pusql` files into the canonical, idempotent form:

```bash
python -m unsequel fmt examples/spec/q01_all_orders.pusql
python -m unsequel fmt --write my_query.pusql     # rewrite in place
python -m unsequel fmt --check my_query.pusql     # exit 1 if not canonical
```

See [docs/pipeline-spec.md](docs/pipeline-spec.md) for the authoritative v0.3.1
grammar (including the `sql` hatch and formatting rules), the IR node and
DuckDB CTE lowering for every stage, and 21 reference queries in
[examples/spec/](examples/spec/). [docs/poc-syntax.md](docs/poc-syntax.md)
keeps the frozen v0.1 history and the PRQL/LINQ/Malloy prior-art notes.
Dogfooding results live in [docs/backlog.md](docs/backlog.md); the external
feedback exercise is [docs/first-task.md](docs/first-task.md).

Expressions support:

- Arithmetic: `+`, `-`, `*`, `/`, `%`
- Comparisons: `=`, `!=`, `<>`, `<`, `<=`, `>`, `>=`
- Logic: `AND`, `OR`, `NOT`
- Null checks: `IS NULL`, `IS NOT NULL`
- Membership and ranges: `IN`, `NOT IN`, `BETWEEN`, `NOT BETWEEN`, `LIKE`,
  `NOT LIKE`
- Conditional and conversion expressions: `CASE`, `CAST`, and `||`
- Strings, numbers, `TRUE`, `FALSE`, and `NULL`
- Functions: `COUNT`, `COUNT(DISTINCT ...)`, `SUM`, `AVG`, `MIN`, `MAX`,
  `COALESCE`, `NULLIF`, `LOWER`, `UPPER`, `ABS`, `ROUND`, `LENGTH`, `TRIM`,
  and `CONCAT`

See the [language reference](docs/language-reference.md) for the ordered
prototype syntax and [semantics/conformance notes](docs/semantics.md).

## Production compiler (TypeScript)

The production compiler in [compiler/](compiler/README.md) is a growing DuckDB-targeted
implementation in TypeScript/Node. It uses a hand-written recursive-descent
parser, one CTE per lowering unit, schema-aware validation, and golden plus
DuckDB execution tests. Its current frozen specification is
[UNSeQueL grammar v1.3](compiler/docs/grammar.md); window frames (`rows between ...`)
and non-DuckDB targets remain outside its implemented subset.

`.unsequel` files run like SQL — they declare their own sources, and a query can be
run whole, selected by line range, piped through stdin, or explored in an
interactive `repl`:

```bash
cd compiler
  npm install -g .        # once; puts `unsequel` on PATH
unsequel run examples/real/r01_top_products.unsequel
unsequel run examples/real/r01_top_products.unsequel --lines 4:10
unsequel repl
```

A VS Code extension (syntax highlighting plus Run Selection/File/REPL) lives
in [compiler/editors/vscode](compiler/editors/vscode/README.md). See
[compiler/docs/first-task.md](compiler/docs/first-task.md) for a guided exercise and
[compiler/docs/backlog.md](compiler/docs/backlog.md) for what comes next.

## How UNSeQueL fits among query languages

Ordered/pipeline syntax already exists in mature tools. DuckDB supports
[FROM-first SQL](https://duckdb.org/docs/current/sql/query_syntax/from#from-first-syntax);
[PRQL](https://prql-lang.org/) compiles pipelines to multiple SQL dialects;
[Malloy](https://docs.malloydata.dev/documentation/language/query.html) adds
semantic modeling and composable queries; and
[Kusto](https://learn.microsoft.com/en-us/kusto/query/queries?view=microsoft-fabric)
is a pipe-oriented language focused on analytics and logs. Pipe syntax is now
also part of
[GoogleSQL/BigQuery](https://docs.cloud.google.com/bigquery/docs/reference/standard-sql/pipe-syntax),
[Apache Spark SQL](https://spark.apache.org/docs/latest/sql-pipe-syntax.html),
and [Databricks SQL/Runtime](https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-syntax-qry-pipeline).

UNSeQueL does not claim to beat those tools on composability, portability, or
feature coverage. Its current distinction is a small, inspectable stage model
with file-level sources, stage previews, line-selection runs, and an editor
extension. The intended benefit is easier reasoning about the supported
relational pipeline; whether it improves learning or transfer to SQL remains
untested. See [semantics and scope](docs/semantics.md) and the
[learning-validation plan](docs/learning-validation.md).

## Design principles

- **Logical order first.** The syntax is designed to show the supported
  relation stages in sequence; its learning effect is still untested.
- **Small standard-library core.** The language has no runtime dependencies.
- **Readable internals.** The lexer, parser, evaluator, and I/O layers are
  separate so contributors can learn one part at a time.
- **SQL-compatible concepts, not SQL-compatible syntax.** UNSeQueL makes the
  supported pipeline explicit while keeping familiar relational ideas; it
  does not remove the need to learn standard SQL.

## Boundaries and roadmap

The Python core and the production compiler implement different read/query subsets; neither
is a full SQL implementation. DDL/DML (`CREATE`, `INSERT`, `UPDATE`, `DELETE`),
recursive queries, scalar subqueries inside expressions, user-defined
functions, and a streaming/native query planner are not implemented. The
production compiler now includes CTEs, set operators, subquery sources, and
integration with DuckDB; window frame clauses and a second SQL target
(non-DuckDB) remain open, even though the Python ordered prototype supports
some of those features.

The prioritised lists are [docs/backlog.md](docs/backlog.md) (Python
prototype) and [compiler/docs/backlog.md](compiler/docs/backlog.md) (compiler), tracked
in the [milestones](https://github.com/absterjr/UNSeQueL/milestones).

Known engine differences include tie ordering, division by zero (`NULL` in the
memory engine versus infinity in DuckDB), and float rendering. These engines
are not claimed to be interchangeable for every expression or CSV dialect;
the [semantics page](docs/semantics.md) lists the current contract and tests.

## Contributing

Read [CONTRIBUTING.md](CONTRIBUTING.md). Issues, examples, documentation, and
small parser improvements are welcome. Questions and open-ended ideas belong
in [Discussions](https://github.com/absterjr/UNSeQueL/discussions) so they get
answered without cluttering the tracker.

## License

MIT. See [LICENSE](LICENSE).
