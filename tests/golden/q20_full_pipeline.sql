WITH stage_1 AS (
  SELECT * FROM orders
),
stage_2 AS (
  SELECT stage_1.*, products.product_id AS products_product_id, products.product_name, products.category, products.list_price FROM stage_1 JOIN products ON (stage_1.product_id = products.product_id)
),
stage_3 AS (
  SELECT *, (quantity * unit_price) AS line_total FROM stage_2
),
stage_4 AS (
  SELECT category, COUNT(*) AS orders, SUM(line_total) AS revenue FROM stage_3 GROUP BY category
),
stage_5 AS (
  SELECT * FROM stage_4 WHERE (revenue > 100)
),
stage_6 AS (
  SELECT *, (revenue / orders) AS avg_order FROM stage_5
),
stage_7 AS (
  SELECT * FROM stage_6 ORDER BY revenue DESC
),
stage_8 AS (
  SELECT * FROM stage_7 LIMIT 3
)
SELECT * FROM stage_8;
