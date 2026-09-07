import json
import tempfile
import unittest
from pathlib import Path

from unsequel.engine import execute
from unsequel.io import load_table
from unsequel.parser import parse_query


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


def load_table_from_rows(name, rows):
    columns = list(dict.fromkeys(key for row in rows for key in row))
    from unsequel.model import Table
    return Table(name, columns, rows)


if __name__ == "__main__":
    unittest.main()
