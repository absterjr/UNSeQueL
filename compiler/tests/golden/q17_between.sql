WITH stage_1 AS (
  SELECT * FROM orders
),
stage_2 AS (
  SELECT * FROM stage_1 WHERE (order_date BETWEEN '2024-02-01' AND '2024-03-31')
),
stage_3 AS (
  SELECT order_id, order_date, customer FROM stage_2
),
stage_4 AS (
  SELECT *, ROW_NUMBER() OVER (ORDER BY order_date) AS __unsequel_internal_order_1 FROM stage_3
)
SELECT * EXCLUDE (__unsequel_internal_order_1) FROM stage_4 ORDER BY __unsequel_internal_order_1;
