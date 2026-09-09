import unittest
from pathlib import Path

from unsequel.engine import execute
from unsequel.io import load_table
from unsequel.parser import parse_query
from unsequel.pipeline import parse_pipeline

EXAMPLES = Path("examples/pipeline")


def run_file(name: str, tables: dict):
    query = parse_pipeline((EXAMPLES / name).read_text(encoding="utf-8"))
    return execute(query, tables)


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.orders = load_table("orders", Path("examples/orders.csv"))
        self.customers = load_table("customers", Path("examples/customers.csv"))
        self.members = load_table("members", EXAMPLES / "members.csv")

    def test_pipeline_reduces_to_ordered_engine(self):
        pipeline = run_file("revenue.pusql", {"orders": self.orders})
        ordered = execute(parse_query(
            "FROM orders GROUP BY country "
            "SELECT country, SUM(quantity * unit_price) AS revenue, COUNT(*) AS orders "
            "ORDER BY revenue DESC"
        ), {"orders": self.orders})
        self.assertEqual(pipeline.rows, ordered.rows)
        self.assertEqual(pipeline.rows, [
            {"country": "United Kingdom", "revenue": 192.0, "orders": 4},
            {"country": "United States", "revenue": 130, "orders": 2},
        ])

    def test_top_customers(self):
        result = run_file("top_customers.pusql",
                          {"orders": self.orders, "customers": self.customers})
        self.assertEqual(result.rows, [
            {"customer": "Ada", "revenue": 172.0},
            {"customer": "Grace", "revenue": 130},
        ])

    def test_post_group_where_and_derive(self):
        result = run_file("avg_order_value.pusql", {"orders": self.orders})
        self.assertEqual(result.rows, [
            {"customer": "Grace", "orders": 2, "revenue": 130, "avg_order": 65.0},
            {"customer": "Ada", "orders": 3, "revenue": 172.0, "avg_order": 57.333333333333336},
        ])

    def test_unmatched_orders(self):
        result = run_file("unmatched_orders.pusql",
                          {"orders": self.orders, "members": self.members})
        self.assertEqual(result.rows, [{"order_id": 1004, "customer": "Alan"}])

    def test_distinct_countries(self):
        result = run_file("countries.pusql", {"orders": self.orders})
        self.assertEqual(result.rows, [
            {"country": "United Kingdom"},
            {"country": "United States"},
        ])

    def test_pagination(self):
        result = run_file("page.pusql", {"orders": self.orders})
        self.assertEqual(result.rows, [
            {"order_id": 1003, "customer": "Ada"},
            {"order_id": 1004, "customer": "Alan"},
        ])


if __name__ == "__main__":
    unittest.main()
