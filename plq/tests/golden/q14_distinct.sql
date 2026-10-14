WITH stage_1 AS (
  SELECT * FROM orders
),
stage_2 AS (
  SELECT country FROM stage_1
),
stage_3 AS (
  SELECT DISTINCT * FROM stage_2
),
stage_4 AS (
  SELECT *, ROW_NUMBER() OVER (ORDER BY country) AS __unsequel_internal_order_1 FROM stage_3
)
SELECT * EXCLUDE (__unsequel_internal_order_1) FROM stage_4 ORDER BY __unsequel_internal_order_1;
