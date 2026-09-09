WITH stage_1 AS (
  SELECT * FROM orders
),
stage_2 AS (
  SELECT country, COUNT(*) AS orders FROM stage_1 GROUP BY country
),
stage_3 AS (
  SELECT * FROM stage_2 ORDER BY orders DESC
)
SELECT * FROM stage_3;
