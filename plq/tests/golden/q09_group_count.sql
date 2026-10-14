WITH stage_1 AS (
  SELECT * FROM orders
),
stage_2 AS (
  SELECT country, COUNT(*) AS orders FROM stage_1 GROUP BY country
),
stage_3 AS (
  SELECT *, ROW_NUMBER() OVER (ORDER BY orders DESC) AS __unsequel_internal_order_1 FROM stage_2
)
SELECT * EXCLUDE (__unsequel_internal_order_1) FROM stage_3 ORDER BY __unsequel_internal_order_1;
