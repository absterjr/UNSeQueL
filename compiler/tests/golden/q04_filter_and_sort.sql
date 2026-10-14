WITH stage_1 AS (
  SELECT * FROM orders
),
stage_2 AS (
  SELECT * FROM stage_1 WHERE ((quantity > 1) AND (unit_price >= 10))
),
stage_3 AS (
  SELECT *, ROW_NUMBER() OVER (ORDER BY unit_price DESC, order_id) AS __unsequel_internal_order_1 FROM stage_2
)
SELECT * EXCLUDE (__unsequel_internal_order_1) FROM stage_3 ORDER BY __unsequel_internal_order_1;
