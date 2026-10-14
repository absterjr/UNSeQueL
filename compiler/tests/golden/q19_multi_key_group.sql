WITH stage_1 AS (
  SELECT * FROM orders
),
stage_2 AS (
  SELECT *, (quantity * unit_price) AS line_total FROM stage_1
),
stage_3 AS (
  SELECT country, product_id, COUNT(*) AS orders, SUM(line_total) AS revenue FROM stage_2 GROUP BY country, product_id
),
stage_4 AS (
  SELECT *, ROW_NUMBER() OVER (ORDER BY country, revenue DESC) AS __unsequel_internal_order_1 FROM stage_3
)
SELECT * EXCLUDE (__unsequel_internal_order_1) FROM stage_4 ORDER BY __unsequel_internal_order_1;
