WITH stage_1 AS (
  SELECT * FROM orders
),
stage_2 AS (
  SELECT order_id, customer, quantity FROM stage_1
)
SELECT * FROM stage_2;
