WITH stage_1 AS (
  SELECT * FROM orders
),
stage_2 AS (
  SELECT stage_1.*, members.customer AS members_customer, members.plan, members.joined_date FROM stage_1 LEFT JOIN members ON (stage_1.customer = members.customer)
),
stage_3 AS (
  SELECT * FROM stage_2 WHERE (members_customer IS NULL)
),
stage_4 AS (
  SELECT order_id, customer FROM stage_3
)
SELECT * FROM stage_4;
