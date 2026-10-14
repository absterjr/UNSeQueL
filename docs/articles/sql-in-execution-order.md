# UNSeQueL: SQL is written in a different order than it runs

*Draft for a dev.to, Hashnode, or personal-site post. Replace the first-person
origin paragraph with your own exact story before publishing.*

SQL commonly starts with `SELECT`, but its logical query processing starts at
`FROM`, then filters rows, groups them, filters groups, projects columns, and
orders/limits the result. That difference is normal and useful once you know
it; while learning or debugging, it can force you to reconstruct the pipeline
mentally.

UNSeQueL is an experiment in putting the supported relational stages in the
order they are applied:

```text
FROM orders
WHERE quantity > 0
GROUP BY country
HAVING COUNT(*) > 1
SELECT country, SUM(quantity * unit_price) AS revenue
ORDER BY revenue DESC
LIMIT 10
```

The idea is not that SQL is wrong, or that one syntax replaces the other. The
question is narrower: **does making the relation stages visible help people
reason about a query?** That is still an untested learning hypothesis. A user
must still learn SQL to work directly with databases and warehouses.

The project has a Python prototype (`.usql` and `.pusql`) and a production
compiler (`.unsequel`) targeting DuckDB. New users should start with `.unsequel`; the
prototype dialects remain available for comparison, not as extra choices in
the recommended path. UNSeQueL files can declare their sources, run from the
command line, execute a selected line range, or run from the VS Code extension.

This space is busy for good reason. DuckDB already supports FROM-first SQL;
PRQL compiles pipelines to multiple SQL dialects; Malloy focuses on semantic
modeling and composable queries; Kusto serves analytics/log workloads; and
GoogleSQL, Spark SQL, and Databricks SQL document composable pipe syntax.
UNSeQueL's current focus is a compact, locally runnable stage model with
preview and editor selection. It is DuckDB-only and still lacks some SQL features, so it
does not claim broader capability or portability.

Try it:

```bash
git clone https://github.com/absterjr/UNSeQueL.git
cd UNSeQueL/compiler
npm install -g .
unsequel run examples/real/r01_top_products.unsequel
```

Repository and discussions: <https://github.com/absterjr/UNSeQueL>

I would especially like feedback from people who can already read basic SQL:
does the order make intermediate results easier to predict, and does that
understanding transfer back to ordinary SQL? Please share a query where the
syntax helps, gets in the way, or gives you the wrong mental model.
