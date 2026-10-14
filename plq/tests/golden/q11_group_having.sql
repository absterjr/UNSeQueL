WITH stage_1 AS (
  SELECT * FROM orders
),
stage_2 AS (
  SELECT *, (quantity * unit_price) AS line_total FROM stage_1
),
stage_3 AS (
  SELECT customer, SUM(line_total) AS revenue FROM stage_2 GROUP BY customer
),
stage_4 AS (
  SELECT * FROM stage_3 WHERE (revenue > 100)
),
stage_5 AS (
  SELECT *, ROW_NUMBER() OVER (ORDER BY revenue DESC) AS __unsequel_internal_order_1 FROM stage_4
)
SELECT * EXCLUDE (__unsequel_internal_order_1) FROM stage_5 ORDER BY __unsequel_internal_order_1;
