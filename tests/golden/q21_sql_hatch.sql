WITH stage_1 AS (
  SELECT * FROM orders
),
stage_2 AS (
    SELECT order_id, customer, quantity, ROW_NUMBER() OVER (PARTITION BY customer ORDER BY quantity DESC, order_id) AS rn FROM stage_1
),
stage_3 AS (
  SELECT * FROM stage_2 WHERE (rn = 1)
),
stage_4 AS (
  SELECT order_id, customer, quantity FROM stage_3
),
stage_5 AS (
  SELECT * FROM stage_4 ORDER BY order_id
)
SELECT * FROM stage_5;