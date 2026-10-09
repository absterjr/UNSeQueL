WITH stage_1 AS (
  SELECT * FROM orders
),
stage_2 AS (
  SELECT *, (quantity * unit_price) AS line_total FROM stage_1
),
stage_3 AS (
  SELECT order_id, customer, line_total FROM stage_2
),
stage_4 AS (
  SELECT * FROM stage_3 ORDER BY line_total DESC
),
stage_5 AS (
  SELECT * FROM stage_4 LIMIT 5
)
SELECT * FROM stage_5;
