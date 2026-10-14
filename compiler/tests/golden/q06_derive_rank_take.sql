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
  SELECT *, ROW_NUMBER() OVER (ORDER BY line_total DESC) AS __unsequel_internal_order_1 FROM stage_3
),
stage_5 AS (
  SELECT * FROM stage_4 ORDER BY __unsequel_internal_order_1 LIMIT 5
)
SELECT * EXCLUDE (__unsequel_internal_order_1) FROM stage_5 ORDER BY __unsequel_internal_order_1;
