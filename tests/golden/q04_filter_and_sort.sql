WITH stage_1 AS (
  SELECT * FROM orders
),
stage_2 AS (
  SELECT * FROM stage_1 WHERE ((quantity > 1) AND (unit_price >= 10))
),
stage_3 AS (
  SELECT * FROM stage_2 ORDER BY unit_price DESC, order_id
)
SELECT * FROM stage_3;
