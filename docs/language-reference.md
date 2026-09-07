# UNSeQueL Language Reference

UNSeQueL is line-oriented on purpose: each stage is easy to see while reading
the query. A clause may wrap across lines, but clause keywords must remain
unambiguous.

## Grammar shape

```text
FROM table [AS alias]
    [INNER JOIN table [AS alias] ON condition]
    [LEFT JOIN table [AS alias] ON condition]
WHERE row_condition
GROUP BY expression [, expression ...]
HAVING group_condition
SELECT expression [AS alias] [, expression [AS alias] ...]
ORDER BY expression [ASC|DESC] [, expression [ASC|DESC] ...]
LIMIT integer
```

`FROM` and `SELECT` are required. All other clauses are optional. The clause
order is part of the language, not a formatting preference.

## Logical execution order

UNSeQueL evaluates a query in this order:

1. `FROM` loads the first table.
2. `JOIN` combines related rows.
3. `WHERE` filters individual rows.
4. `GROUP BY` creates groups.
5. `HAVING` filters groups.
6. `SELECT` calculates output expressions and aliases.
7. `ORDER BY` sorts the output.
8. `LIMIT` keeps the first N output rows.

That is why this is valid:

```text
FROM orders
WHERE quantity > 0
GROUP BY country
HAVING SUM(quantity * unit_price) > 100
SELECT country, SUM(quantity * unit_price) AS revenue
ORDER BY revenue DESC
LIMIT 10
```

`WHERE` cannot use `SUM(...)`: at that stage, groups do not exist yet.
`HAVING` is the group-level filter.

## Tables and aliases

The CLI loads named files:

```bash
python -m unsequel run query.usql \
  --data orders=examples/orders.csv \
  --data customers=examples/customers.csv
```

Aliases make joins explicit:

```text
FROM orders AS o
JOIN customers AS c ON o.customer = c.customer
SELECT c.segment AS segment, SUM(o.quantity * o.unit_price) AS revenue
GROUP BY c.segment
ORDER BY revenue DESC
```

Both `INNER JOIN` and `LEFT JOIN` are supported. An unmatched left-join row
gets `NULL` values for the right table's columns.

## Expressions

Values can be column names, qualified names such as `o.customer`, numbers,
quoted strings, `TRUE`, `FALSE`, or `NULL`.

Operators:

```text
+  -  *  /  %
=  !=  <>  <  <=  >  >=
AND  OR  NOT
LIKE  IS NULL  IS NOT NULL
```

Functions:

```text
COUNT(*)       SUM(value)       AVG(value)
MIN(value)     MAX(value)       COALESCE(a, b)
LOWER(value)   UPPER(value)     ABS(value)
ROUND(value, digits)             LENGTH(value)
```

CSV values are inferred as integers, floats, `NULL`, or strings. JSON values
keep their native JSON types.

## Output formats

The default output is a readable table. Machine-readable formats are also
available:

```bash
python -m unsequel run query.usql --data orders=orders.csv --format csv
python -m unsequel run query.usql --data orders=orders.csv --format json
```

## Current boundaries

The 0.1 MVP executes data in memory and intentionally keeps the language
small. It does not yet include subqueries, CTEs, `DISTINCT`, user-defined
functions, or database-backed execution. These can be added behind the same
syntax after the core semantics have more tests.
