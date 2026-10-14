WITH stage_1 AS (
  SELECT * FROM orders
),
stage_2 AS (
  SELECT *, (CASE WHEN (quantity >= 6) THEN 'bulk' WHEN (quantity >= 3) THEN 'medium' ELSE 'small' END) AS size FROM stage_1
),
stage_3 AS (
  SELECT order_id, quantity, size FROM stage_2
),
stage_4 AS (
  SELECT *, ROW_NUMBER() OVER (ORDER BY quantity DESC) AS __unsequel_internal_order_1 FROM stage_3
)
SELECT * EXCLUDE (__unsequel_internal_order_1) FROM stage_4 ORDER BY __unsequel_internal_order_1;
