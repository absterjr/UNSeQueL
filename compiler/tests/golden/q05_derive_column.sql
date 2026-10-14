WITH stage_1 AS (
  SELECT * FROM orders
),
stage_2 AS (
  SELECT *, (quantity * unit_price) AS revenue FROM stage_1
),
stage_3 AS (
  SELECT order_id, customer, revenue FROM stage_2
)
SELECT * FROM stage_3;
