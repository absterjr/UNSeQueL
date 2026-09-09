SELECT order_id, customer, country FROM orders
WHERE country IN ('United Kingdom', 'Netherlands')
ORDER BY order_id;
