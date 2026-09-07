# UNSeQueL Language Reference

UNSeQueL is line-oriented on purpose: each stage is easy to see while reading
the query. A clause may wrap across lines, but clause keywords must remain
unambiguous. `WITH` declarations and set operations surround a normal ordered
query; the body still follows the execution order below.

## Grammar shape

```text
WITH name AS (query) [, name AS (query) ...]
FROM table [AS alias] | (query) AS alias
    [INNER JOIN source ON condition]
    [LEFT JOIN source ON condition]
    [RIGHT JOIN source ON condition]
    [FULL JOIN source ON condition]
    [CROSS JOIN source]
WHERE row_condition
GROUP BY expression [, expression ...]
HAVING group_condition
SELECT [DISTINCT] expression [AS alias] [, expression [AS alias] ...]
ORDER BY expression [ASC|DESC] [, expression [ASC|DESC] ...]
LIMIT integer
OFFSET integer
[UNION [ALL] query]
[INTERSECT [ALL] query]
[EXCEPT [ALL] query]
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

`INNER JOIN`, `LEFT JOIN`, `RIGHT JOIN`, `FULL JOIN`, and `CROSS JOIN` are
supported. An unmatched outer-join row gets `NULL` values for the missing
side's columns. A source may be a table, a CTE, or an aliased subquery:

```text
WITH profitable AS (
    FROM orders
    WHERE quantity * unit_price > 25
    SELECT customer, quantity * unit_price AS revenue
)
FROM (FROM profitable SELECT customer, revenue) AS p
GROUP BY customer
SELECT customer, SUM(revenue) AS revenue
ORDER BY revenue DESC
```

`UNION` removes duplicate rows; `UNION ALL` preserves them. `INTERSECT` and
`EXCEPT` have the usual distinct-row behavior, with `ALL` variants available.

## Expressions

Values can be column names, qualified names such as `o.customer`, numbers,
quoted strings, `TRUE`, `FALSE`, or `NULL`.

Operators:

```text
+  -  *  /  %
=  !=  <>  <  <=  >  >=
AND  OR  NOT
LIKE  IS NULL  IS NOT NULL
IN  NOT IN  BETWEEN  NOT BETWEEN
||
CASE WHEN condition THEN value [WHEN ...] [ELSE value] END
CAST(value AS type)
```

Functions:

```text
COUNT(*)       COUNT(DISTINCT value)  SUM(value)  AVG(value)
MIN(value)     MAX(value)       COALESCE(a, b)
LOWER(value)   UPPER(value)     ABS(value)
ROUND(value, digits)             LENGTH(value)
NULLIF(a, b)     TRIM(value)    CONCAT(a, b, ...)
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

## SQLite input

The CLI can load every table and view from a SQLite database without an extra
dependency:

```bash
python -m unsequel run query.usql --sqlite warehouse.db --format json
```

The adapter currently materializes the selected database sources in memory,
which keeps execution semantics identical to CSV and JSON input.

## Current boundaries

The 0.2 release is a complete read/query core and intentionally does not
include table-changing statements, recursive CTEs, scalar subqueries inside
expressions, user-defined functions, or streaming/native query planning.
