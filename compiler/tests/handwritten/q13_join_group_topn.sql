SELECT p.category, SUM(o.quantity * o.unit_price) AS revenue
FROM orders o JOIN products p ON o.product_id = p.product_id
GROUP BY p.category ORDER BY revenue DESC LIMIT 2;
