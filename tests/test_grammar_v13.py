"""Conformance tests for the Python `.unsequel` grammar v1.3 front-end.

The TypeScript compiler is the reference implementation; these tests pin the
Python port in `unsequel.grammar` to the same corpora. Every reference,
coverage, and dogfood query must parse, and the reference queries must parse
to the exact stage sequences documented in `compiler/docs/grammar.md` §6.
"""

import unittest
from pathlib import Path

from unsequel.grammar import (
    Case,
    DeriveStage,
    FromStage,
    GrammarError,
    GroupByStage,
    JoinStage,
    Name,
    RawSqlStage,
    SetStage,
    WindowStage,
    WithStage,
    parse_program,
)

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "compiler" / "examples"

REFERENCE_STAGES = {
    "q01": ["from"],
    "q02": ["from", "select"],
    "q03": ["from", "filter", "select"],
    "q04": ["from", "filter", "sort"],
    "q05": ["from", "derive", "select"],
    "q06": ["from", "derive", "select", "sort", "take"],
    "q07": ["from", "join", "select"],
    "q08": ["from", "left join", "filter", "select"],
    "q09": ["from", "group by", "aggregate", "sort"],
    "q10": ["from", "derive", "group by", "aggregate", "sort"],
    "q11": ["from", "derive", "group by", "aggregate", "having", "sort"],
    "q12": ["from", "derive", "group by", "aggregate", "derive", "sort"],
    "q13": ["from", "join", "derive", "group by", "aggregate", "sort", "take"],
    "q14": ["from", "select", "distinct", "sort"],
    "q15": ["from", "select", "sort", "skip", "take"],
    "q16": ["from", "filter", "select", "sort"],
    "q17": ["from", "filter", "select", "sort"],
    "q18": ["from", "derive", "select", "sort"],
    "q19": ["from", "derive", "group by", "aggregate", "sort"],
    "q20": ["from", "join", "derive", "group by", "aggregate", "having", "derive",
            "sort", "take"],
    "q21": ["from", "sql", "filter", "select", "sort"],
}


class CorpusConformanceTests(unittest.TestCase):
    def _parse_all(self, directory: str):
        files = sorted((EXAMPLES / directory).glob("*.unsequel"))
        self.assertTrue(files, f"no queries found in {directory}")
        for path in files:
            with self.subTest(query=path.name):
                program = parse_program(path.read_text(encoding="utf-8"))
                self.assertTrue(program, "a parsed program is never empty")
                self.assertEqual([stage.index for stage in program],
                                 list(range(1, len(program) + 1)))
                self.assertIn(program[0].keyword, {"with", "from"})
                for stage in program:
                    self.assertIn(stage.kind, {
                        "from", "with", "set", "join", "filter", "derive",
                        "group by", "aggregate", "having", "window", "select",
                        "sort", "skip", "take", "distinct", "sql"})

    def test_reference_corpus_matches_documented_stage_sequences(self):
        for path in sorted((EXAMPLES / "reference").glob("*.unsequel")):
            prefix = path.name[:3]
            expected = REFERENCE_STAGES[prefix]
            with self.subTest(query=path.name):
                program = parse_program(path.read_text(encoding="utf-8"))
                self.assertEqual([stage.keyword for stage in program], expected)

    def test_coverage_corpus_parses(self):
        self._parse_all("coverage")

    def test_dogfood_corpus_parses(self):
        self._parse_all("real")


class ParserBehaviorTests(unittest.TestCase):
    def test_inline_pipe_separates_stages(self):
        program = parse_program("from orders | derive x = 1 | take 2")
        self.assertEqual([stage.keyword for stage in program], ["from", "derive", "take"])

    def test_comments_and_blank_lines_are_ignored(self):
        program = parse_program(
            "# a comment\nfrom orders  -- trailing\n\n# another\nselect order_id\n"
        )
        self.assertEqual([stage.keyword for stage in program], ["from", "select"])

    def test_with_definition_then_from(self):
        program = parse_program(
            "with recent = (from orders | take 1)\nfrom recent"
        )
        self.assertIsInstance(program[0], WithStage)
        self.assertEqual(program[0].name, "recent")
        self.assertEqual([stage.keyword for stage in program[0].program],
                         ["from", "take"])
        self.assertIsInstance(program[1], FromStage)

    def test_with_must_precede_from(self):
        with self.assertRaisesRegex(GrammarError, "'with' definitions must appear before 'from'"):
            parse_program("from orders\nwith x = (from orders)")

    def test_duplicate_with_names_are_rejected(self):
        with self.assertRaisesRegex(GrammarError, "with 'x' is defined more than once"):
            parse_program("with x = (from orders)\nwith x = (from orders)\nfrom x")

    def test_subquery_source_requires_alias(self):
        program = parse_program("from (from orders | take 2) as top\nselect top.order_id")
        source = program[0].source
        self.assertEqual(source.name, "")
        self.assertEqual(source.alias, "top")
        self.assertEqual([stage.keyword for stage in source.program], ["from", "take"])
        item = program[1].items[0]
        self.assertIsInstance(item.expr, Name)
        self.assertEqual(item.expr.name, "top.order_id")

    def test_subquery_source_without_alias_is_rejected(self):
        with self.assertRaisesRegex(GrammarError, r"subquery requires an alias"):
            parse_program("from (from orders)")

    def test_set_operators_parse_with_nested_programs(self):
        program = parse_program(
            "from orders\nunion all (from orders | take 1)\nexcept (from orders)"
        )
        self.assertIsInstance(program[1], SetStage)
        self.assertEqual(program[1].op, "union all")
        self.assertEqual(program[2].op, "except")

    def test_group_by_must_be_followed_by_aggregate(self):
        with self.assertRaisesRegex(GrammarError, "group by must be immediately followed by aggregate"):
            parse_program("from orders\ngroup by country\nsort country")

    def test_aggregate_requires_group_by(self):
        with self.assertRaisesRegex(GrammarError, "aggregate requires a preceding group by"):
            parse_program("from orders\naggregate n = COUNT(*)")

    def test_join_after_sql_hatch_is_rejected(self):
        with self.assertRaisesRegex(GrammarError, "join cannot appear after a sql stage"):
            parse_program(
                'from orders\nsql "SELECT * FROM __input__"\njoin customers on orders.customer = customers.customer'
            )

    def test_chained_comparisons_are_rejected(self):
        with self.assertRaisesRegex(GrammarError, "chained comparisons are not supported"):
            parse_program("from t\nfilter a < b < c")

    def test_prefix_not_binds_to_the_whole_comparison(self):
        program = parse_program("from t\nfilter not a in (1, 2)")
        condition = program[1].condition
        self.assertEqual(type(condition).__name__, "Unary")
        self.assertEqual(condition.op, "not")
        self.assertEqual(type(condition.operand).__name__, "InExpr")

    def test_double_not_is_rejected(self):
        with self.assertRaisesRegex(GrammarError, "unexpected keyword 'not'"):
            parse_program("from t\nfilter not not a")

    def test_between_consumes_its_own_and(self):
        program = parse_program("from t\nfilter a between 1 and 2 and b = 1")
        condition = program[1].condition
        self.assertEqual(condition.op, "and")
        self.assertEqual(type(condition.left).__name__, "Between")

    def test_select_alias_binding_is_x_equals_y(self):
        program = parse_program("from t\nselect x = a + 1")
        item = program[1].items[0]
        self.assertEqual(item.alias, "x")
        self.assertEqual(type(item.expr).__name__, "Binary")

    def test_select_star_with_named_expression(self):
        program = parse_program("from t\nselect *, total = a + 1")
        self.assertTrue(program[1].star)
        self.assertEqual(program[1].items[0].alias, "total")

    def test_duplicate_select_outputs_are_rejected(self):
        with self.assertRaisesRegex(GrammarError, "select projects 'x' more than once"):
            parse_program("from t\nselect x = a, x = b")

    def test_aggregate_call_outside_aggregate_stage_is_rejected(self):
        with self.assertRaisesRegex(GrammarError, "only valid inside an aggregate stage"):
            parse_program("from t\nselect COUNT(*)")

    def test_unknown_function_is_rejected(self):
        with self.assertRaisesRegex(GrammarError, "unknown function 'frobnicate'"):
            parse_program("from t\nselect frobnicate(a)")

    def test_window_stage_parses_call_spec(self):
        program = parse_program(
            "from orders\n"
            "window rank = ROW_NUMBER() over (partition by customer order by quantity desc, order_id)"
        )
        stage = program[1]
        self.assertIsInstance(stage, WindowStage)
        self.assertEqual(stage.name, "rank")
        self.assertEqual(stage.call.func, "ROW_NUMBER")
        self.assertEqual(len(stage.call.partition_by), 1)
        self.assertEqual(len(stage.call.order_by), 2)
        self.assertTrue(stage.call.order_by[0].descending)

    def test_window_function_in_expression_is_rejected(self):
        with self.assertRaisesRegex(GrammarError, "only valid in a window stage"):
            parse_program("from t\nselect r = ROW_NUMBER() over ()")

    def test_count_star_is_the_only_wildcard_call(self):
        with self.assertRaisesRegex(GrammarError, "only COUNT accepts"):
            parse_program("from t\ngroup by g\naggregate m = SUM(*)")

    def test_case_requires_when_and_closes_with_end(self):
        program = parse_program(
            "from t\n"
            "select bucket = case when a > 1 then 'big' when a > 0 then 'small' else 'none' end"
        )
        expr = program[1].items[0].expr
        self.assertIsInstance(expr, Case)
        self.assertEqual(len(expr.branches), 2)

    def test_aggregate_items_are_named_or_count_star(self):
        program = parse_program("from t\ngroup by g\naggregate COUNT(*), total = SUM(x)")
        items = program[2].items
        self.assertEqual(items[0].name, "count")
        self.assertEqual(items[1].name, "total")
        self.assertTrue(items[0].call.star)

    def test_unnamed_non_count_aggregate_is_rejected(self):
        with self.assertRaisesRegex(GrammarError, "aggregates must be named"):
            parse_program("from t\ngroup by g\naggregate SUM(x)")

    def test_group_key_cannot_be_qualified(self):
        with self.assertRaisesRegex(GrammarError, "group keys cannot be qualified names"):
            parse_program("from t\ngroup by t.g\naggregate n = COUNT(*)")

    def test_sql_stage_validates_payload(self):
        with self.assertRaisesRegex(GrammarError, "start it with SELECT or WITH"):
            parse_program('from t\nsql "DELETE FROM __input__"')
        with self.assertRaisesRegex(GrammarError, "';' is not allowed"):
            parse_program('from t\nsql "SELECT * FROM __input__; SELECT 1"')
        program = parse_program('from t\nsql "/* note */ SELECT * FROM __input__"')
        self.assertIsInstance(program[1], RawSqlStage)

    def test_select_projects_must_be_present(self):
        with self.assertRaisesRegex(GrammarError, "select requires at least one column"):
            parse_program("from t\nselect")

    def test_take_requires_non_negative_integer(self):
        with self.assertRaisesRegex(GrammarError, "take requires a non-negative integer"):
            parse_program("from t\ntake -1")
        with self.assertRaisesRegex(GrammarError, "take requires a non-negative integer"):
            parse_program("from t\ntake 1.5")

    def test_from_may_appear_only_once(self):
        with self.assertRaisesRegex(GrammarError, "'from' may appear only once"):
            parse_program("from a\nfrom b")

    def test_quoting_identifiers_with_backticks(self):
        program = parse_program("from `Order Details`\nselect `2024_sales`, `order`")
        self.assertEqual(program[0].source.name, "Order Details")
        self.assertEqual(program[1].items[0].expr.name, "2024_sales")

    def test_derive_phase_follows_grouping(self):
        program = parse_program(
            "from t\ngroup by g\naggregate n = COUNT(*)\nderive doubled = n * 2"
        )
        self.assertIsInstance(program[1], GroupByStage)
        derive = program[3]
        self.assertIsInstance(derive, DeriveStage)
        self.assertEqual(derive.phase, "group")

    def test_join_kinds_and_aliases(self):
        program = parse_program(
            "from orders as o\n"
            "right join customers as c on o.customer = c.customer\n"
            "cross join calendar"
        )
        join = program[1]
        self.assertIsInstance(join, JoinStage)
        self.assertEqual(join.join_kind, "right")
        self.assertEqual(join.source.name, "customers")
        self.assertEqual(join.source.alias, "c")
        self.assertEqual(program[2].join_kind, "cross")

    def test_stage_errors_carry_stage_location(self):
        with self.assertRaises(GrammarError) as ctx:
            parse_program("from t\nselect a, a")
        error = ctx.exception
        self.assertEqual(error.stage_index, 2)
        self.assertEqual(error.keyword, "select")
        self.assertIsNotNone(error.line)
        self.assertIn("stage 2 (select)", str(error))

    def test_expressions_parse_into_typed_nodes(self):
        program = parse_program(
            "from t\n"
            "filter a = 1 or b <> 2 and c is not null\n"
            "derive x = -a * (b + 1) % 2 || 'z'"
        )
        self.assertEqual(type(program[1].condition).__name__, "Binary")
        derive_expr = program[2].items[0].expr
        self.assertEqual(type(derive_expr).__name__, "Binary")
        self.assertEqual(derive_expr.op, "||")

    def test_literals_and_null_parse(self):
        program = parse_program(
            "from t\nfilter a = null or b = true or c = 'x' or d = 1.5"
        )
        self.assertEqual(type(program[1].condition).__name__, "Binary")


if __name__ == "__main__":
    unittest.main()
