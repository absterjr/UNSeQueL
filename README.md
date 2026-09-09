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

The grammar is frozen as POC v0.1. Run a pipeline query with the `--pipeline`
flag:

```bash
python -m unsequel run examples/pipeline/revenue.pusql --pipeline \
  --data orders=examples/orders.csv
```

See [docs/pipeline-spec.md](docs/pipeline-spec.md) for the authoritative v0.2
grammar, the IR node and DuckDB CTE lowering for every stage, and 20 reference
queries in [examples/spec/](examples/spec/). [docs/poc-syntax.md](docs/poc-syntax.md)
keeps the frozen v0.1 history and the PRQL/LINQ/Malloy prior-art notes.

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
