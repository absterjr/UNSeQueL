SELECT country, COUNT(*) AS orders FROM orders GROUP BY country ORDER BY orders DESC;
