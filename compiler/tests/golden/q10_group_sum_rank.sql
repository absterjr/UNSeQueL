WITH stage_1 AS (
  SELECT * FROM orders
),
stage_2 AS (
  SELECT *, (quantity * unit_price) AS line_total FROM stage_1
),
stage_3 AS (
  SELECT country, SUM(line_total) AS revenue, COUNT(*) AS orders FROM stage_2 GROUP BY country
),
stage_4 AS (
  SELECT *, ROW_NUMBER() OVER (ORDER BY revenue DESC) AS __unsequel_internal_order_1 FROM stage_3
)
SELECT * EXCLUDE (__unsequel_internal_order_1) FROM stage_4 ORDER BY __unsequel_internal_order_1;
