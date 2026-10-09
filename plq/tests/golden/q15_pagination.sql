WITH stage_1 AS (
  SELECT * FROM orders
),
stage_2 AS (
  SELECT order_id, customer FROM stage_1
),
stage_3 AS (
  SELECT * FROM stage_2 ORDER BY order_id
),
stage_4 AS (
  SELECT * FROM stage_3 OFFSET 5
),
stage_5 AS (
  SELECT * FROM stage_4 LIMIT 5
)
SELECT * FROM stage_5;
