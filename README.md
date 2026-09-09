# UNSeQueL

UNSeQueL is a small, open-source query language that lets you write a query
in the order SQL logically executes it.

Instead of writing:

```sql
SELECT country, SUM(quantity * unit_price) AS revenue
FROM orders
WHERE quantity > 0
GROUP BY country
HAVING SUM(quantity * unit_price) > 1000
ORDER BY revenue DESC
LIMIT 10;
```

you write:

```text
FROM orders
WHERE quantity > 0
GROUP BY country
HAVING SUM(quantity * unit_price) > 1000
SELECT country, SUM(quantity * unit_price) AS revenue
ORDER BY revenue DESC
LIMIT 10
```

The point is not to replace SQL. The point is to make the logical execution
pipeline visible and learnable.

## Status

UNSeQueL 0.2 is a complete query-language core. It includes a real lexer,
ordered grammar, semantic stage checks, an in-memory relational executor, CTEs,
nested sources, set operations, four join types plus cross joins, and a SQLite
input adapter. The implementation is deliberately dependency-free so the
language can be learned and extended without a framework.

## Install

Requires Python 3.10 or newer.

The first public release is being built in this repository. Install the
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

The `unsequel` command is installed in a virtual environment or pipx
environment. `python -m unsequel` is the portable fallback when a user-level
Python `Scripts` directory is not on `PATH`.

## Run your first query

The repository includes a small example dataset:

```bash
unsequel run examples/revenue.usql --data orders=examples/orders.csv
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

`FROM` is required. The other stages are optional, but they must appear in
the order above. Multiple `JOIN` clauses are allowed directly after `FROM`.

## Pipeline syntax POC

A second, experimental surface is being evaluated: one transform per line,
PRQL-style, lowered onto the same engine.

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

See [docs/pipeline-spec.md](docs/pipeline-spec.md) for the authoritative v0.3
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

See the [language reference](docs/language-reference.md) for the complete
core syntax and execution model.

## Production compiler (TypeScript)

The executable prototype lives in this Python package. The production
compiler — TypeScript/Node, DuckDB target, hand-written recursive descent,
one CTE per stage — is being built to the same plan in
[plq/](plq/README.md).

## Design principles

- **Logical order first.** The syntax teaches the difference between row
  filtering and group filtering.
- **Small standard-library core.** The language has no runtime dependencies.
- **Readable internals.** The lexer, parser, evaluator, and I/O layers are
  separate so contributors can learn one part at a time.
- **SQL-compatible concepts, not SQL-compatible syntax.** UNSeQueL makes the
  execution pipeline explicit while preserving familiar relational ideas.

## Boundaries and roadmap

The 0.2 core is a complete read/query language, not a SQL clone. It does not
yet include table-changing statements (`CREATE`, `INSERT`, `UPDATE`, `DELETE`),
recursive CTEs, scalar subqueries inside expressions, user-defined functions,
or a streaming/native query planner. Those are separate language and storage
designs, not hidden partial features.

Planned next steps are transactional storage adapters, recursive queries,
streaming execution, better source highlights, a formatter, and an interactive
learning mode that shows each intermediate relation.

## Contributing

Read [CONTRIBUTING.md](CONTRIBUTING.md). Issues, examples, documentation, and
small parser improvements are welcome.

## License

MIT. See [LICENSE](LICENSE).
