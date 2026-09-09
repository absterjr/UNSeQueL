"""Step 5: schema-aware semantic analysis."""

import unittest
from pathlib import Path

from unsequel.pipeline_ir import parse_pipeline_stages
from unsequel.schema import Schema, SchemaError, type_family
from unsequel.semantics import SemanticError, analyze

SPEC = Path("examples/spec")
SCHEMA = Schema.load(SPEC / "schema.json")


def stages(text: str):
    return parse_pipeline_stages(text)


class SchemaLoadTests(unittest.TestCase):
    def test_type_families(self):
        self.assertEqual(type_family("integer"), "numeric")
        self.assertEqual(type_family("varchar"), "text")
        self.assertEqual(type_family("timestamp"), "temporal")
        self.assertEqual(type_family("geography"), "unknown")

    def test_rejects_malformed(self):
        with self.assertRaises(SchemaError):
            Schema.from_dict({"nope": {}})
        with self.assertRaises(SchemaError):
            Schema.from_dict({"tables": {"t": {"columns": "bad"}}})


class ValidQueryTests(unittest.TestCase):
    def test_all_twenty_reference_queries_pass_cleanly(self):
        for path in sorted(SPEC.glob("q*.pusql")):
            with self.subTest(query=path.stem):
                analyze(stages(path.read_text()), SCHEMA)

    def test_lineage_tracks_live_columns_per_stage(self):
        lineage = analyze(stages(SPEC.joinpath("q10_group_sum_rank.pusql").read_text()), SCHEMA)
        self.assertEqual(lineage[0].keyword, "from")
        self.assertIn("unit_price", lineage[0].columns)
        # after group, only keys + aggregates survive
        group_line = next(entry for entry in lineage if entry.keyword == "group")
        self.assertEqual(set(group_line.columns), {"country", "revenue", "orders"})


class InvalidQueryTests(unittest.TestCase):
    def _error(self, text: str) -> SemanticError:
        with self.assertRaises(SemanticError) as ctx:
            analyze(stages(text), SCHEMA)
        return ctx.exception

    def test_unknown_source_table(self):
        err = self._error("from warehouse_events")
        self.assertEqual((err.index, err.keyword), (1, "from"))
        self.assertIn("unknown table 'warehouse_events'", str(err))

    def test_unknown_column_in_where(self):
        err = self._error("from orders\nwhere shipping_cost > 10")
        self.assertEqual((err.index, err.keyword, err.line), (2, "where", 2))
        self.assertIn("unknown column 'shipping_cost'", str(err))

    def test_sum_over_text_column(self):
        err = self._error("from orders\ngroup country (bad = SUM(customer))")
        self.assertEqual(err.index, 2)
        self.assertIn("numeric", str(err))

    def test_row_column_dead_after_group(self):
        err = self._error(
            "from orders\ngroup country (n = COUNT(*))\nwhere quantity > 5"
        )
        self.assertEqual((err.index, err.keyword), (3, "where"))
        self.assertIn("unknown column 'quantity'", str(err))

    def test_unknown_column_in_join_condition(self):
        err = self._error(
            "from orders\njoin products on widget_id = products.product_id"
        )
        self.assertEqual((err.index, err.keyword), (2, "join"))
        self.assertIn("widget_id", str(err))

    def test_derived_column_is_usable_downstream(self):
        # no error: a row derive feeds a later sort
        analyze(stages(
            "from orders\nderive total = quantity * unit_price\n"
            "select order_id, total\nsort -total"
        ), SCHEMA)


if __name__ == "__main__":
    unittest.main()
