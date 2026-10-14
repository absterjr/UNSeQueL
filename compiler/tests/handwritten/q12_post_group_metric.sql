SELECT customer,
       COUNT(*) AS orders,
       SUM(quantity * unit_price) AS revenue,
       SUM(quantity * unit_price) / COUNT(*) AS avg_order
FROM orders GROUP BY customer
ORDER BY avg_order DESC;
