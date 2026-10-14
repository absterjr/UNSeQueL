WITH stage_1 AS (
  SELECT * FROM orders
),
stage_2 AS (
  SELECT order_id, customer FROM stage_1
),
stage_3 AS (
  SELECT *, ROW_NUMBER() OVER (ORDER BY order_id) AS __unsequel_internal_order_1 FROM stage_2
),
stage_4 AS (
  SELECT * FROM stage_3 ORDER BY __unsequel_internal_order_1 OFFSET 5
),
stage_5 AS (
  SELECT * FROM stage_4 ORDER BY __unsequel_internal_order_1 LIMIT 5
)
SELECT * EXCLUDE (__unsequel_internal_order_1) FROM stage_5 ORDER BY __unsequel_internal_order_1;
