SELECT p.category,
       COUNT(*) AS orders,
       SUM(o.quantity * o.unit_price) AS revenue,
       SUM(o.quantity * o.unit_price) / COUNT(*) AS avg_order
FROM orders o JOIN products p ON o.product_id = p.product_id
GROUP BY p.category
HAVING SUM(o.quantity * o.unit_price) > 100
ORDER BY revenue DESC LIMIT 3;
