SELECT * FROM orders
WHERE quantity > 1 AND unit_price >= 10
ORDER BY unit_price DESC, order_id;
