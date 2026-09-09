SELECT customer, SUM(quantity * unit_price) AS revenue
FROM orders GROUP BY customer
HAVING SUM(quantity * unit_price) > 100
ORDER BY revenue DESC;
