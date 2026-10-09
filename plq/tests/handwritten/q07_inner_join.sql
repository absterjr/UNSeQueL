SELECT o.order_id, p.product_name, p.category, o.quantity
FROM orders o JOIN products p ON o.product_id = p.product_id;
