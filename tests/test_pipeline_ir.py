"""Step 4: stage-preserving IR and stage-located parse errors."""

import unittest
from pathlib import Path

from unsequel.engine import execute
from unsequel.io import load_table
from unsequel.pipeline import lower_to_query, parse_pipeline, parse_pipeline_stages
from unsequel.pipeline_ir import (Derive, Distinct, From, Group, Join, PipelineError,
                                  Select, Skip, Sort, Take, Where)

SPEC = Path("examples/spec")


class IRShapeTests(unittest.TestCase):
    def test_one_node_per_stage_in_order(self):
        stages = parse_pipeline_stages((SPEC / "q20_full_pipeline.pusql").read_text())
        self.assertEqual(
            [type(s) for s in stages],
            [From, Join, Derive, Group, Where, Derive, Sort, Take],
        )
        self.assertEqual([s.line for s in stages], [1, 2, 3, 4, 5, 6, 7, 8])

    def test_phase_is_tagged_relative_to_group(self):
        stages = parse_pipeline_stages(
            "from orders\nderive a = quantity * unit_price\n"
            "group customer (n = COUNT(*))\nwhere n > 1\nderive b = n + 1"
        )
        phased = [s for s in stages if isinstance(s, (Derive, Where))]
        self.assertEqual([s.phase for s in phased], ["row", "group", "group"])

    def test_all_twenty_reference_queries_produce_ir(self):
        for path in sorted(SPEC.glob("q*.pusql")):
            with self.subTest(query=path.stem):
                stages = parse_pipeline_stages(path.read_text())
                self.assertIsInstance(stages[0], From)
                if any(type(s).__name__ == "RawSql" for s in stages):
                    continue  # sql-hatch pipelines run via execute_pipeline_stages
                lower_to_query(stages)  # lowering must not raise


class NameCollisionTests(unittest.TestCase):
    def setUp(self):
        self.tables = {
            name: load_table(name, SPEC / f"{name}.csv")
            for name in ("orders", "customers", "products", "members")
        }

    def test_row_derive_name_may_match_aggregate_name(self):
        # 'revenue' is both a row-phase derive and the aggregate; the post-group
        # filter must resolve to the aggregate, not the row expression.
        query = parse_pipeline(
            "from orders\n"
            "derive revenue = quantity * unit_price\n"
            "group customer (revenue = SUM(revenue))\n"
            "where revenue > 100\n"
            "sort -revenue"
        )
        rows = execute(query, self.tables).rows
        self.assertEqual(rows, [
            {"customer": "Katherine", "revenue": 177.0},
            {"customer": "Ada", "revenue": 162.0},
            {"customer": "Grace", "revenue": 151.5},
        ])


class StageLocatedErrorTests(unittest.TestCase):
    def _error(self, text: str) -> PipelineError:
        with self.assertRaises(PipelineError) as ctx:
            parse_pipeline_stages(text)
        return ctx.exception

    def test_stage_before_from(self):
        err = self._error("where quantity > 1\nfrom orders")
        self.assertEqual((err.index, err.keyword, err.line), (1, "where", 1))
        self.assertIn("must start with 'from'", str(err))

    def test_stage_after_select(self):
        err = self._error("from orders\nselect customer\nwhere quantity > 1")
        self.assertEqual((err.index, err.keyword, err.line), (3, "where", 3))
        self.assertIn("after select", str(err))

    def test_group_without_parentheses(self):
        err = self._error("from orders\ngroup customer")
        self.assertEqual((err.index, err.keyword), (2, "group"))
        self.assertIn("parentheses", str(err))

    def test_aggregate_in_select(self):
        err = self._error("from orders\nselect SUM(quantity)")
        self.assertEqual((err.index, err.keyword, err.line), (2, "select", 2))
        self.assertIn("group stage", str(err))

    def test_take_needs_integer(self):
        err = self._error("from orders\ntake plenty")
        self.assertEqual((err.index, err.keyword), (2, "take"))

    def test_duplicate_from(self):
        err = self._error("from orders\nfrom customers")
        self.assertEqual((err.index, err.keyword), (2, "from"))
        self.assertIn("only once", str(err))

    def test_window_still_reserved(self):
        err = self._error("from orders\nwindow w as (partition by country)")
        self.assertEqual((err.index, err.keyword), (2, "window"))
        self.assertIn("reserved", str(err))

    def test_sql_stage_parses_to_raw_node(self):
        stages = parse_pipeline_stages('from orders\nsql "SELECT * FROM __input__"')
        self.assertEqual([type(s).__name__ for s in stages], ["From", "RawSql"])
        self.assertEqual(stages[1].text, "SELECT * FROM __input__")

    def test_unknown_stage(self):
        err = self._error("from orders\nfrobnicate x")
        self.assertEqual(err.index, 2)


if __name__ == "__main__":
    unittest.main()
