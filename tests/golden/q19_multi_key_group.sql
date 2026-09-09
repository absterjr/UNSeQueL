WITH stage_1 AS (
  SELECT * FROM orders
),
stage_2 AS (
  SELECT *, (quantity * unit_price) AS line_total FROM stage_1
),
stage_3 AS (
  SELECT country, product_id, COUNT(*) AS orders, SUM(line_total) AS revenue FROM stage_2 GROUP BY country, product_id
),
stage_4 AS (
  SELECT * FROM stage_3 ORDER BY country, revenue DESC
)
SELECT * FROM stage_4;
