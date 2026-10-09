SELECT order_id, quantity,
       CASE WHEN quantity >= 6 THEN 'bulk'
            WHEN quantity >= 3 THEN 'medium'
            ELSE 'small' END AS size
FROM orders ORDER BY quantity DESC;
