"""Step 8: raw-SQL escape hatch (sql "...") and the canonical formatter."""

import contextlib
import io
import unittest
from pathlib import Path

from unsequel.cli import main
from unsequel.codegen import emit_sql
from unsequel.engine import execute
from unsequel.errors import ExecutionError
from unsequel.format import format_pipeline, is_formatted
from unsequel.io import load_table
from unsequel.pipeline import PipelineError, execute_pipeline_stages, parse_pipeline_stages
from unsequel.schema import Schema
from unsequel.semantics import analyze

SPEC = Path("examples/spec")
OLD = Path("examples/pipeline")
SCHEMA = Schema.load(SPEC / "schema.json")


def load_sample():
    return {
        name: load_table(name, SPEC / f"{name}.csv")
        for name in ("orders", "customers", "products", "members")
    }


def run_cli(*argv) -> tuple[str, int]:
    out = io.StringIO()
    code = 0
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            main(list(argv))
    except SystemExit as exit_:
        code = exit_.code or 0
    return out.getvalue(), code


class FormatterTests(unittest.TestCase):
    def test_idempotent_on_all_examples(self):
        paths = sorted(SPEC.glob("q*.pusql")) + sorted(OLD.glob("*.pusql"))
        self.assertTrue(paths)
        for path in paths:
            with self.subTest(query=path.name):
                original = path.read_text(encoding="utf-8")
                once = format_pipeline(original)
                twice = format_pipeline(once)
                self.assertEqual(once, twice)
                self.assertTrue(once.endswith("\n"))

    def test_canonical_rules(self):
        formatted = format_pipeline(
            "FROM orders | DERIVE total = quantity*unit_price | SORT -total DESC | TAKE 3"
        )
        self.assertEqual(
            formatted,
            "from orders\n"
            "derive total = quantity * unit_price\n"
            "sort -total\n"
            "take 3\n",
        )
        self.assertTrue(is_formatted(formatted))
        self.assertFalse(is_formatted("FROM orders\n"))

    def test_string_literals_normalize_to_single_quotes(self):
        formatted = format_pipeline('from orders\nwhere country = "UK"')
        self.assertEqual(formatted, "from orders\nwhere country = 'UK'\n")
        self.assertEqual(format_pipeline(formatted), formatted)

    def test_select_and_group_short_names(self):
        formatted = format_pipeline(
            "from orders\ngroup country (n = COUNT(*))\nselect country, n"
        )
        self.assertEqual(
            formatted,
            "from orders\ngroup country (n = COUNT(*))\nselect country, n\n",
        )

    def test_sql_stage_is_quoted_not_reformatted(self):
        raw = 'sql "SELECT   weird   FROM __input__"'
        formatted = format_pipeline(f"from orders\n{raw}")
        self.assertIn(raw, formatted)


class SqlStageMemoryTests(unittest.TestCase):
    """The in-memory engine runs sql segments through stdlib sqlite."""

    def setUp(self):
        self.tables = load_sample()

    def test_sql_filters_the_previous_relation(self):
        stages = parse_pipeline_stages(
            'from orders\nsql "SELECT * FROM __input__ WHERE quantity >= 8"\nsort order_id'
        )
        result = execute_pipeline_stages(stages, self.tables)
        self.assertEqual([row["order_id"] for row in result.rows], [5001, 5011])

    def test_top_order_per_customer_window(self):
        stages = parse_pipeline_stages((SPEC / "q21_sql_hatch.pusql").read_text(encoding="utf-8"))
        result = execute_pipeline_stages(stages, self.tables)
        self.assertEqual(len(result.rows), 5)
        self.assertEqual(result.rows[0], {"order_id": 5001, "customer": "Ada", "quantity": 10})
        self.assertEqual(result.rows[-1], {"order_id": 5015, "customer": "Alan", "quantity": 4})

    def test_table_names_remain_visible_to_the_hatch(self):
        stages = parse_pipeline_stages(
            "from orders\n"
            'sql "SELECT o.order_id FROM __input__ AS o JOIN customers AS c '
            'ON o.customer = c.customer WHERE c.customer = \'Ada\'"\n'
            "sort order_id"
        )
        result = execute_pipeline_stages(stages, self.tables)
        self.assertEqual({row["order_id"] for row in result.rows}, {5001, 5002, 5008, 5013})

    def test_sql_cannot_be_lowered_directly(self):
        stages = parse_pipeline_stages('from orders\nsql "SELECT * FROM __input__"')
        from unsequel.pipeline import lower_to_query
        with self.assertRaises(PipelineError):
            lower_to_query(stages)

    def test_sql_error_is_user_facing(self):
        stages = parse_pipeline_stages('from orders\nsql "SELECT nope FROM __input__"')
        with self.assertRaises(ExecutionError):
            execute_pipeline_stages(stages, self.tables)

    def test_parse_errors_point_at_the_sql_stage(self):
        with self.assertRaises(PipelineError) as ctx:
            parse_pipeline_stages("from orders\nsql SELECT")
        self.assertEqual((ctx.exception.index, ctx.exception.keyword), (2, "sql"))
        with self.assertRaises(PipelineError):
            parse_pipeline_stages('sql "SELECT 1"')
        with self.assertRaises(PipelineError) as ctx:
            parse_pipeline_stages('from orders\nsql "SELECT 1; DROP TABLE orders"')
        self.assertIn("';' is not allowed", str(ctx.exception))
        with self.assertRaises(PipelineError) as ctx:
            parse_pipeline_stages("from orders\nwindow w as (partition by country)")
        self.assertIn("reserved", str(ctx.exception))


class SqlStageCodegenTests(unittest.TestCase):
    def test_raw_stage_becomes_one_cte_with_input_substitution(self):
        stages = parse_pipeline_stages((SPEC / "q21_sql_hatch.pusql").read_text(encoding="utf-8"))
        sql = emit_sql(stages, SCHEMA)
        self.assertEqual(sql.count(" AS (\n"), len(stages))
        self.assertIn("FROM stage_1", sql)
        self.assertNotIn("__input__", sql)
        self.assertTrue(sql.rstrip().endswith("SELECT * FROM stage_5;"))

    def test_truncation_before_and_after_the_raw_stage(self):
        stages = parse_pipeline_stages((SPEC / "q21_sql_hatch.pusql").read_text(encoding="utf-8"))
        early = emit_sql(stages, SCHEMA, stop_at=1)
        self.assertNotIn("ROW_NUMBER", early)
        at_raw = emit_sql(stages, SCHEMA, stop_at=2)
        self.assertIn("ROW_NUMBER() OVER", at_raw)
        self.assertTrue(at_raw.rstrip().endswith("SELECT * FROM stage_2;"))

    def test_multi_line_raw_sql_stays_indented(self):
        stages = parse_pipeline_stages(
            'from orders\nsql "SELECT order_id,\n       quantity\nFROM __input__"'
        )
        sql = emit_sql(stages, SCHEMA)
        self.assertIn("  SELECT order_id,\n         quantity\n  FROM stage_1", sql)


class SqlStageSemanticsTests(unittest.TestCase):
    def test_hatch_passes_schema_check_and_marks_lineage_opaque(self):
        stages = parse_pipeline_stages((SPEC / "q21_sql_hatch.pusql").read_text(encoding="utf-8"))
        lineage = analyze(stages, SCHEMA)
        self.assertEqual(lineage[1].columns, ("<raw sql>",))
        self.assertEqual(lineage[2].columns, ("<opaque>",))

    def test_columns_after_the_hatch_are_not_checked(self):
        stages = parse_pipeline_stages(
            'from orders\nsql "SELECT 1 AS mystery FROM __input__"\nwhere mystery > 0'
        )
        analyze(stages, SCHEMA)  # must not raise


class FmtCliTests(unittest.TestCase):
    def test_check_passes_on_canonical_file(self):
        _, code = run_cli("fmt", "--check", str(SPEC / "q01_all_orders.pusql"))
        self.assertEqual(code, 0)

    def test_check_fails_on_non_canonical_text(self):
        with _temp_file("FROM orders\n") as path:
            _, code = run_cli("fmt", "--check", str(path))
            self.assertEqual(code, 1)
            _, code = run_cli("fmt", "--write", str(path))
            self.assertEqual(code, 0)
            self.assertEqual(Path(path).read_text(encoding="utf-8"), "from orders\n")
            _, code = run_cli("fmt", "--check", str(path))
            self.assertEqual(code, 0)

    def test_fmt_is_stable_on_example_files(self):
        for path in sorted(OLD.glob("*.pusql")) + sorted(SPEC.glob("q*.pusql")):
            with self.subTest(query=path.name):
                text, code = run_cli("fmt", str(path))
                self.assertEqual(code, 0)
                self.assertEqual(text, format_pipeline(path.read_text(encoding="utf-8")))
                self.assertTrue(is_formatted(text))


class RunCliTests(unittest.TestCase):
    def test_run_memory_engine_executes_the_hatch(self):
        text, code = run_cli("run", str(SPEC / "q21_sql_hatch.pusql"), "--pipeline",
                             "--format", "json",
                             f"--data=orders={SPEC / 'orders.csv'}")
        self.assertEqual(code, 0)
        import json
        rows = json.loads(text)
        self.assertEqual([row["order_id"] for row in rows], [5001, 5009, 5011, 5012, 5015])

    def test_compile_cli_handles_the_hatch(self):
        text, code = run_cli("compile", str(SPEC / "q21_sql_hatch.pusql"),
                             "--schema", str(SPEC / "schema.json"))
        self.assertEqual(code, 0)
        self.assertIn("ROW_NUMBER", text)


class _temp_file:
    def __init__(self, content: str):
        import tempfile
        self._manager = tempfile.NamedTemporaryFile("w", suffix=".pusql", delete=False)
        self.path = self._manager.name
        self._content = content

    def __enter__(self):
        self._manager.write(self._content)
        self._manager.close()
        return self.path

    def __exit__(self, *exc):
        Path(self.path).unlink(missing_ok=True)
        return False


if __name__ == "__main__":
    unittest.main()
