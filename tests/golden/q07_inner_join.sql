WITH stage_1 AS (
  SELECT * FROM orders
),
stage_2 AS (
  SELECT stage_1.*, products.product_id AS products_product_id, products.product_name, products.category, products.list_price FROM stage_1 JOIN products ON (stage_1.product_id = products.product_id)
),
stage_3 AS (
  SELECT order_id, product_name, category, quantity FROM stage_2
)
SELECT * FROM stage_3;
