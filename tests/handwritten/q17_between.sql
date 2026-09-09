SELECT order_id, order_date, customer FROM orders
WHERE order_date BETWEEN '2024-02-01' AND '2024-03-31'
ORDER BY order_date;
