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
  SELECT * FROM stage_3 ORDER BY quantity DESC
)
SELECT * FROM stage_4;
