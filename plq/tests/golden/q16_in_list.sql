WITH stage_1 AS (
  SELECT * FROM orders
),
stage_2 AS (
  SELECT * FROM stage_1 WHERE (country IN ('United Kingdom', 'Netherlands'))
),
stage_3 AS (
  SELECT order_id, customer, country FROM stage_2
),
stage_4 AS (
  SELECT * FROM stage_3 ORDER BY order_id
)
SELECT * FROM stage_4;
