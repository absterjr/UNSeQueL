SELECT o.order_id, o.customer
FROM orders o LEFT JOIN members m ON o.customer = m.customer
WHERE m.customer IS NULL;
