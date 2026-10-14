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
  SELECT *, ROW_NUMBER() OVER (ORDER BY order_id) AS __unsequel_internal_order_1 FROM stage_3
)
SELECT * EXCLUDE (__unsequel_internal_order_1) FROM stage_4 ORDER BY __unsequel_internal_order_1;
