import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from unsequel.engine import execute
from unsequel.io import load_sqlite_tables, load_table
from unsequel.parser import parse_query
from unsequel.errors import ParseError


class LanguageTests(unittest.TestCase):
    def setUp(self):
        self.orders = load_table("orders", Path("examples/orders.csv"))

    def test_execution_order_query(self):
        query = parse_query(Path("examples/revenue.usql").read_text(encoding="utf-8"))
        result = execute(query, {"orders": self.orders})
        self.assertEqual(result.columns, ["country", "revenue", "orders"])
        self.assertEqual(result.rows[0]["country"], "United Kingdom")
        self.assertEqual(result.rows[0]["revenue"], 192.0)
        self.assertEqual(result.rows[0]["orders"], 4)

    def test_advanced_example(self):
        query = parse_query(Path("examples/advanced.usql").read_text(encoding="utf-8"))
        result = execute(query, {"orders": self.orders})
        self.assertEqual(result.rows, [
            {"country": "United Kingdom", "customers": 2, "revenue": 192.0},
            {"country": "United States", "customers": 1, "revenue": 130},
        ])

    def test_left_join_preserves_unmatched_rows(self):
        query = parse_query(
            """
            FROM orders AS o
            LEFT JOIN customers AS c ON o.customer = c.customer
            SELECT o.order_id AS order_id, c.segment AS segment
            ORDER BY order_id ASC
            """
        )
        customers = load_table_from_rows("customers", [
            {"customer": "Ada", "segment": "A"},
        ])
        result = execute(query, {"orders": self.orders, "customers": customers})
        self.assertEqual(len(result.rows), 6)
        self.assertIsNone(result.rows[1]["segment"])

    def test_json_input(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "items.json"
            path.write_text(json.dumps([{"name": "a", "value": 2}]), encoding="utf-8")
            table = load_table("items", path)
        self.assertEqual(table.rows[0]["value"], 2)

    def test_having_alias_order_and_limit(self):
        query = parse_query(
            """
            FROM orders
            WHERE quantity > 0
            GROUP BY country
            HAVING SUM(quantity * unit_price) > 100
            SELECT country, ROUND(SUM(quantity * unit_price), 2) AS revenue
            ORDER BY revenue DESC
            LIMIT 1
            """
        )
        result = execute(query, {"orders": self.orders})
        self.assertEqual(result.rows, [{"country": "United Kingdom", "revenue": 192.0}])

    def test_distinct_aggregate(self):
        query = parse_query(
            "FROM orders SELECT COUNT(DISTINCT customer) AS customers, "
            "COUNT(*) AS orders"
        )
        result = execute(query, {"orders": self.orders})
        self.assertEqual(result.rows, [{"customers": 3, "orders": 6}])

    def test_select_wildcard(self):
        query = parse_query("FROM orders SELECT * LIMIT 1")
        result = execute(query, {"orders": self.orders})
        self.assertEqual(result.columns, self.orders.columns)
        self.assertEqual(result.rows[0]["order_id"], 1001)

    def test_qualified_join_example(self):
        query = parse_query(Path("examples/customer_revenue.usql").read_text(encoding="utf-8"))
        customers = load_table_from_rows("customers", [
            {"customer": "Ada", "country": "United Kingdom", "segment": "wholesale"},
            {"customer": "Grace", "country": "United States", "segment": "enterprise"},
            {"customer": "Alan", "country": "United Kingdom", "segment": "small-business"},
        ])
        result = execute(query, {"orders": self.orders, "customers": customers})
        self.assertEqual(result.rows[0]["segment"], "wholesale")
        self.assertEqual(result.rows[0]["revenue"], 172.0)

    def test_parser_rejects_wrong_clause_order(self):
        with self.assertRaises(Exception):
            parse_query("SELECT country FROM orders WHERE country = 'UK'")

    def test_cte_and_nested_from_query(self):
        query = parse_query(
            """
            WITH positive AS (
                FROM orders
                WHERE quantity > 1
                SELECT country, quantity
            )
            FROM (FROM positive SELECT country, quantity) AS filtered
            GROUP BY country
            SELECT country, SUM(quantity) AS quantity
            ORDER BY country
            """
        )
        result = execute(query, {"orders": self.orders})
        self.assertEqual(result.rows, [
            {"country": "United Kingdom", "quantity": 10},
            {"country": "United States", "quantity": 2},
        ])

    def test_distinct_offset_and_union(self):
        query = parse_query(
            """
            FROM orders
            SELECT DISTINCT country
            ORDER BY country
            LIMIT 1
            OFFSET 1
            UNION ALL
            FROM orders
            WHERE country = 'United Kingdom'
            SELECT country
            """
        )
        result = execute(query, {"orders": self.orders})
        self.assertEqual(result.rows, [
            {"country": "United States"},
            {"country": "United Kingdom"},
            {"country": "United Kingdom"},
            {"country": "United Kingdom"},
            {"country": "United Kingdom"},
        ])

    def test_intersect_and_except(self):
        intersect = execute(parse_query(
            "FROM orders SELECT DISTINCT country "
            "INTERSECT FROM orders WHERE country = 'United Kingdom' SELECT country"
        ), {"orders": self.orders})
        self.assertEqual(intersect.rows, [{"country": "United Kingdom"}])

        excluded = execute(parse_query(
            "FROM orders SELECT DISTINCT country "
            "EXCEPT FROM orders WHERE country = 'United Kingdom' SELECT country"
        ), {"orders": self.orders})
        self.assertEqual(excluded.rows, [{"country": "United States"}])

    def test_richer_expressions(self):
        query = parse_query(
            """
            FROM orders
            WHERE country IN ('United Kingdom', 'United States')
              AND quantity BETWEEN 1 AND 10
            SELECT CASE WHEN country = 'United Kingdom' THEN 'UK' ELSE 'US' END AS code,
                   CAST(quantity AS TEXT) || ':' || customer AS label,
                   country NOT LIKE 'Canada%' AS not_canada
            ORDER BY order_id
            LIMIT 1
            """
        )
        result = execute(query, {"orders": self.orders})
        self.assertEqual(result.rows, [{"code": "UK", "label": "2:Ada", "not_canada": True}])

    def test_empty_left_join_has_qualified_nulls(self):
        query = parse_query(
            """
            FROM orders AS o
            LEFT JOIN customers AS c ON o.customer = c.customer
            SELECT o.customer AS customer, c.segment AS segment
            LIMIT 1
            """
        )
        from unsequel.model import Table
        result = execute(query, {
            "orders": self.orders,
            "customers": Table("customers", ["customer", "segment"], []),
        })
        self.assertIsNone(result.rows[0]["segment"])

    def test_right_full_and_cross_joins(self):
        customers = load_table_from_rows("customers", [
            {"customer": "Ada", "segment": "A"},
            {"customer": "New", "segment": "N"},
        ])
        right = execute(parse_query(
            """
            FROM orders AS o
            RIGHT JOIN customers AS c ON o.customer = c.customer
            SELECT c.customer AS customer, o.order_id AS order_id
            ORDER BY customer
            """
        ), {"orders": self.orders, "customers": customers})
        self.assertEqual(right.rows[-1], {"customer": "New", "order_id": None})

        full = execute(parse_query(
            """
            FROM orders AS o
            FULL JOIN customers AS c ON o.customer = c.customer
            SELECT c.customer AS customer, o.order_id AS order_id
            """
        ), {"orders": self.orders, "customers": customers})
        self.assertTrue(any(row["customer"] == "New" and row["order_id"] is None
                            for row in full.rows))

        cross = execute(parse_query(
            """
            FROM customers AS c
            CROSS JOIN customers AS d
            SELECT c.customer AS left_customer, d.customer AS right_customer
            LIMIT 1
            """
        ), {"customers": customers})
        self.assertEqual(len(cross.rows), 1)

    def test_sqlite_adapter(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "orders.db"
            connection = sqlite3.connect(path)
            try:
                connection.execute("CREATE TABLE orders (country TEXT, quantity INTEGER)")
                connection.executemany("INSERT INTO orders VALUES (?, ?)", [
                    ("UK", 2), ("UK", 3), ("US", 1),
                ])
                connection.commit()
            finally:
                connection.close()
            tables = load_sqlite_tables(path)
        query = parse_query("FROM orders GROUP BY country SELECT country, SUM(quantity) AS quantity")
        result = execute(query, tables)
        self.assertEqual(result.rows, [{"country": "UK", "quantity": 5}, {"country": "US", "quantity": 1}])

    def test_aggregate_where_is_rejected_early(self):
        with self.assertRaises(ParseError):
            parse_query("FROM orders WHERE SUM(quantity) > 1 SELECT country")


def load_table_from_rows(name, rows):
    columns = list(dict.fromkeys(key for row in rows for key in row))
    from unsequel.model import Table
    return Table(name, columns, rows)


if __name__ == "__main__":
    unittest.main()
