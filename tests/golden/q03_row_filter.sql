WITH stage_1 AS (
  SELECT * FROM orders
),
stage_2 AS (
  SELECT * FROM stage_1 WHERE (quantity >= 5)
),
stage_3 AS (
  SELECT order_id, customer, quantity FROM stage_2
)
SELECT * FROM stage_3;
