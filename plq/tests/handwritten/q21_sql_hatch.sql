SELECT order_id, customer, quantity
FROM (
    SELECT order_id, customer, quantity,
           ROW_NUMBER() OVER (PARTITION BY customer ORDER BY quantity DESC, order_id) AS rn
    FROM orders
)
WHERE rn = 1
ORDER BY order_id