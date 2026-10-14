SELECT country, product_id, COUNT(*) AS orders, SUM(quantity * unit_price) AS revenue
FROM orders GROUP BY country, product_id
ORDER BY country, revenue DESC;
