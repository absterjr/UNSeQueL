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

UNSeQueL is an early, dependency-free MVP. It currently runs queries against
CSV and JSON files in memory. The parser and execution stages are separated so
database-backed adapters can be added later.

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

The `--data` option uses `name=path`. JSON files containing an array of
objects are also supported.

## Language shape

The first MVP supports these stages:

```text
FROM table
JOIN other_table ON left_key = right_key
WHERE row_condition
GROUP BY grouping_expression
HAVING group_condition
SELECT output_expression AS alias
ORDER BY expression ASC|DESC
LIMIT number
```

`FROM` is required. The other stages are optional, but they must appear in
the order above. Multiple `JOIN` clauses are allowed directly after `FROM`.

Expressions support:

- Arithmetic: `+`, `-`, `*`, `/`, `%`
- Comparisons: `=`, `!=`, `<>`, `<`, `<=`, `>`, `>=`
- Logic: `AND`, `OR`, `NOT`
- Null checks: `IS NULL`, `IS NOT NULL`
- Strings, numbers, `TRUE`, `FALSE`, and `NULL`
- Functions: `COUNT`, `SUM`, `AVG`, `MIN`, `MAX`, `COALESCE`, `LOWER`,
  `UPPER`, `ABS`, `ROUND`, and `LENGTH`

See the [language reference](docs/language-reference.md) for the complete
MVP syntax and execution model.

## Design principles

- **Logical order first.** The syntax teaches the difference between row
  filtering and group filtering.
- **Small standard-library core.** The MVP has no runtime dependencies.
- **Readable internals.** The lexer, parser, evaluator, and I/O layers are
  separate so contributors can learn one part at a time.
- **SQL-compatible concepts, not SQL-compatible syntax.** UNSeQueL makes the
  execution pipeline explicit while preserving familiar relational ideas.

## Roadmap

- Better diagnostics with source line and column highlights.
- More joins and explicit table aliases.
- A richer standard function library.
- SQLite and DuckDB execution adapters.
- Streaming execution for larger files.
- A formatter and language-server support.
- Interactive learning mode that shows each intermediate table.

The MVP deliberately does not yet support subqueries, CTEs, `DISTINCT`, or
streaming large files. Those are planned after the execution model is stable.

## Contributing

Read [CONTRIBUTING.md](CONTRIBUTING.md). Issues, examples, documentation, and
small parser improvements are welcome.

## License

MIT. See [LICENSE](LICENSE).
