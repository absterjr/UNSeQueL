SELECT country, SUM(quantity * unit_price) AS revenue, COUNT(*) AS orders
FROM orders GROUP BY country ORDER BY revenue DESC;
