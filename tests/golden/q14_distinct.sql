WITH stage_1 AS (
  SELECT * FROM orders
),
stage_2 AS (
  SELECT country FROM stage_1
),
stage_3 AS (
  SELECT DISTINCT * FROM stage_2
),
stage_4 AS (
  SELECT * FROM stage_3 ORDER BY country
)
SELECT * FROM stage_4;
