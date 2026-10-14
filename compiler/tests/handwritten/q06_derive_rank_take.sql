SELECT order_id, customer, quantity * unit_price AS line_total
FROM orders ORDER BY line_total DESC LIMIT 5;
