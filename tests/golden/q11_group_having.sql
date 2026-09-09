WITH stage_1 AS (
  SELECT * FROM orders
),
stage_2 AS (
  SELECT *, (quantity * unit_price) AS line_total FROM stage_1
),
stage_3 AS (
  SELECT customer, SUM(line_total) AS revenue FROM stage_2 GROUP BY customer
),
stage_4 AS (
  SELECT * FROM stage_3 WHERE (revenue > 100)
),
stage_5 AS (
  SELECT * FROM stage_4 ORDER BY revenue DESC
)
SELECT * FROM stage_5;
