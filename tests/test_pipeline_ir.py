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

    def test_subquery_sources_are_rejected(self):
        err = self._error("from (from orders select country) as x")
        self.assertEqual((err.index, err.keyword), (1, "from"))
        self.assertIn("subquery", str(err))
        err = self._error(
            "from orders\njoin (from members select customer) as m on customer = m.customer"
        )
        self.assertEqual((err.index, err.keyword), (2, "join"))
        self.assertIn("subquery", str(err))

    def test_unknown_stage(self):
        err = self._error("from orders\nfrobnicate x")
        self.assertEqual(err.index, 2)


class OrderingRuleTests(unittest.TestCase):
    def _error(self, text: str) -> PipelineError:
        with self.assertRaises(PipelineError) as ctx:
            parse_pipeline_stages(text)
        return ctx.exception

    def test_join_after_group_is_rejected(self):
        err = self._error(
            "from orders\ngroup country (n = COUNT(*))\njoin members on a = members.customer"
        )
        self.assertEqual((err.index, err.keyword, err.line), (3, "join", 3))
        self.assertIn("after group", str(err))

    def test_stage_after_tail_is_rejected(self):
        for text, keyword in [
            ("from orders\ntake 2\nwhere order_id > 1", "where"),
            ("from orders\nsort order_id\nderive x = quantity", "derive"),
            ("from orders\nskip 2\nselect order_id", "select"),
            ("from orders\nselect country\nsort country\ndistinct", "distinct"),
        ]:
            with self.subTest(keyword=keyword):
                err = self._error(text)
                self.assertEqual(err.keyword, keyword)
                self.assertIn("after sort/skip/take", str(err))

    def test_second_sort_is_rejected(self):
        err = self._error("from orders\nsort order_id\ntake 2\nsort -order_id")
        self.assertEqual(err.keyword, "sort")
        self.assertIn("only once", str(err))

    def test_sort_after_skip_is_rejected(self):
        err = self._error("from orders\nskip 2\nsort order_id")
        self.assertIn("before skip/take", str(err))

    def test_skip_after_take_is_rejected(self):
        err = self._error("from orders\ntake 2\nskip 1")
        self.assertIn("before take", str(err))


class AggregatePlacementTests(unittest.TestCase):
    def _error(self, text: str) -> PipelineError:
        with self.assertRaises(PipelineError) as ctx:
            parse_pipeline_stages(text)
        return ctx.exception

    def test_derive_cannot_contain_aggregates(self):
        err = self._error("from orders\nderive t = SUM(quantity)")
        self.assertEqual(err.keyword, "derive")
        self.assertIn("aggregate", str(err))

    def test_where_cannot_contain_aggregates(self):
        err = self._error("from orders\nwhere SUM(quantity) > 1")
        self.assertEqual(err.keyword, "where")

    def test_join_condition_cannot_contain_aggregates(self):
        err = self._error(
            "from orders\n"
            "join members on customer = members.customer AND SUM(quantity) > 1"
        )
        self.assertEqual(err.keyword, "join")

    def test_sort_cannot_contain_aggregates(self):
        err = self._error("from orders\ngroup country (n = COUNT(*))\nsort SUM(quantity)")
        self.assertEqual(err.keyword, "sort")

    def test_nested_aggregates_are_rejected(self):
        err = self._error("from orders\ngroup country (n = SUM(SUM(quantity)))")
        self.assertIn("cannot contain aggregate", str(err))

    def test_wildcard_aggregates_limited_to_count(self):
        err = self._error("from orders\ngroup country (t = SUM(*))")
        self.assertIn("only COUNT accepts", str(err))

    def test_count_distinct_star_is_rejected(self):
        err = self._error("from orders\ngroup country (n = COUNT(DISTINCT *))")
        self.assertIn("COUNT(DISTINCT *)", str(err))

    def test_hidden_aggregates_in_select_are_rejected(self):
        for text in [
            "from orders\nselect s = COALESCE(SUM(quantity), 0)",
            "from orders\nselect s = -SUM(quantity)",
            "from orders\nselect s = CASE WHEN SUM(quantity) > 1 THEN 1 ELSE 0 END",
            "from orders\nselect quantity IN (SUM(quantity))",
        ]:
            with self.subTest(text=text):
                err = self._error(text)
                self.assertEqual(err.keyword, "select")
                self.assertIn("group stage", str(err))


class DuplicateNameTests(unittest.TestCase):
    def _error(self, text: str) -> PipelineError:
        with self.assertRaises(PipelineError) as ctx:
            parse_pipeline_stages(text)
        return ctx.exception

    def test_select_cannot_project_the_same_name_twice(self):
        err = self._error("from orders\nselect quantity, quantity = 1")
        self.assertEqual(err.keyword, "select")
        self.assertIn("more than once", str(err))

    def test_derive_cannot_redefine_within_a_stage(self):
        err = self._error("from orders\nderive a = quantity, a = quantity * 2")
        self.assertIn("more than once", str(err))

    def test_derive_cannot_redefine_across_stages(self):
        err = self._error("from orders\nderive a = quantity\nderive a = quantity * 2")
        self.assertIn("already defined", str(err))

    def test_group_key_and_aggregate_cannot_share_a_name(self):
        err = self._error("from orders\ngroup country (country = COUNT(*))")
        self.assertIn("conflicts with the group key", str(err))

    def test_group_names_cannot_repeat(self):
        err = self._error("from orders\ngroup country, country (n = COUNT(*))")
        self.assertIn("more than once", str(err))
        err = self._error("from orders\ngroup country (n = COUNT(*), n = SUM(quantity))")
        self.assertIn("more than once", str(err))

    def test_group_derive_cannot_shadow_group_names(self):
        err = self._error("from orders\ngroup country (n = COUNT(*))\nderive n = n + 1")
        self.assertIn("conflicts with a group key or aggregate", str(err))

    def test_row_derive_shadowing_an_aggregate_name_is_allowed(self):
        stages = parse_pipeline_stages(
            "from orders\n"
            "derive revenue = quantity * unit_price\n"
            "group customer (revenue = SUM(revenue))\n"
            "sort -revenue"
        )
        self.assertTrue(stages)


class DeriveDefaultProjectionTests(unittest.TestCase):
    def setUp(self):
        self.orders = load_table("orders", SPEC / "orders.csv")

    def test_derive_without_select_keeps_source_columns(self):
        stages = parse_pipeline_stages(
            "from orders\nderive line_total = quantity * unit_price\ntake 2"
        )
        result = execute(lower_to_query(stages), {"orders": self.orders})
        self.assertEqual(result.columns, [*self.orders.columns, "line_total"])
        self.assertEqual(len(result.rows), 2)
        self.assertEqual(result.rows[0]["line_total"], 10 * 4.5)
        self.assertEqual(result.rows[0]["order_id"], 5001)

    def test_default_projection_without_derives_is_wildcard(self):
        stages = parse_pipeline_stages("from orders\ntake 1")
        result = execute(lower_to_query(stages), {"orders": self.orders})
        self.assertEqual(result.columns, self.orders.columns)


if __name__ == "__main__":
    unittest.main()
