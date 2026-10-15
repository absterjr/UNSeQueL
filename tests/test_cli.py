"""Step 7: compile / preview / run CLI."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from unsequel.cli import main

SPEC = Path("examples/spec")
SCHEMA = str(SPEC / "schema.json")
FULL = str(SPEC / "q20_full_pipeline.pusql")
DATA = [f"--data=orders={SPEC / 'orders.csv'}", f"--data=products={SPEC / 'products.csv'}"]

try:
    import duckdb  # noqa: F401
    HAS_DUCKDB = True
except ImportError:
    HAS_DUCKDB = False


def _nums(rows):
    return [{k: (round(float(v), 9) if isinstance(v, (int, float)) else v)
             for k, v in row.items()} for row in rows]


def _without_notices(text: str) -> str:
    """Drop stderr deprecation notices merged into the captured output."""
    lines = [line for line in text.splitlines() if not line.startswith("note: ")]
    result = "\n".join(lines)
    return f"{result}\n" if text.endswith("\n") else result


def run_cli(*argv) -> tuple[str, int]:
    out = io.StringIO()
    code = 0
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            main(list(argv))
    except SystemExit as exit_:
        code = exit_.code or 0
    return _without_notices(out.getvalue()), code


class CompileTests(unittest.TestCase):
    def test_compile_emits_one_cte_per_stage(self):
        text, code = run_cli("compile", FULL, "--schema", SCHEMA)
        self.assertEqual(code, 0)
        self.assertEqual(text.count(" AS (\n"), 8)
        self.assertIn("SELECT * FROM stage_8;", text)

    def test_compile_stage_truncates(self):
        text, code = run_cli("compile", FULL, "--schema", SCHEMA, "--stage", "4")
        self.assertEqual(code, 0)
        self.assertIn("SELECT * FROM stage_4;", text)
        self.assertNotIn("stage_5", text)

    def test_compile_reports_schema_error_with_stage(self):
        with patch("sys.stdin", io.StringIO("")):
            text, code = run_cli("compile", "-", "--schema", SCHEMA)
        # Explicitly patched empty stdin makes this deterministic and non-blocking.
        self.assertEqual(code, 1)
        self.assertIn("error:", text)


@unittest.skipUnless(HAS_DUCKDB, "duckdb not installed")
class PreviewTests(unittest.TestCase):
    def test_preview_shows_intermediate_relation(self):
        text, code = run_cli("preview", FULL, "--schema", SCHEMA,
                             "--stage", "3", "--limit", "5", *DATA)
        self.assertEqual(code, 0)
        self.assertIn("stage 3/8  (derive)", text)
        self.assertIn("line_total", text)
        self.assertIn("5 row(s)", text)

    def test_preview_later_stage_has_fewer_columns(self):
        text, _ = run_cli("preview", FULL, "--schema", SCHEMA, "--stage", "5", *DATA)
        self.assertIn("stage 5/8  (where)", text)
        self.assertIn("revenue", text)
        self.assertNotIn("order_date", text)

    def test_preview_rejects_out_of_range_stage(self):
        text, code = run_cli("preview", FULL, "--schema", SCHEMA, "--stage", "99", *DATA)
        self.assertEqual(code, 1)
        self.assertIn("between 1 and 8", text)

    def test_run_duckdb_engine_matches_memory(self):
        duck, _ = run_cli("run", FULL, "--pipeline", "--engine", "duckdb",
                          "--schema", SCHEMA, "--format", "json", *DATA)
        mem, _ = run_cli("run", FULL, "--pipeline", "--format", "json", *DATA)
        self.assertEqual(_nums(json.loads(duck)), _nums(json.loads(mem)))

    def test_duckdb_reader_honors_csv_delimiter_and_type_overrides(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory) / "orders.csv"
            query = Path(directory) / "query.pusql"
            schema = Path(directory) / "schema.json"
            data.write_text("postal;quantity\n02134;2\n00501;3\n", encoding="utf-8")
            query.write_text("from orders\nselect postal\n", encoding="utf-8")
            schema.write_text(
                json.dumps({"tables": {"orders": {"columns": {
                    "postal": "integer", "quantity": "integer",
                }}}}),
                encoding="utf-8")
            text, code = run_cli(
                "run", str(query), "--pipeline", "--engine", "duckdb",
                "--schema", str(schema), "--delimiter", ";",
                "--column-type", "orders.postal=integer", "--format", "json",
                f"--data=orders={data}")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(text), [{"postal": 2134}, {"postal": 501}])


class CliInputOutputTests(unittest.TestCase):
    def test_empty_results_print_headers_and_zero_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory) / "orders.csv"
            query = Path(directory) / "empty.usql"
            data.write_text("id,quantity\n1,2\n", encoding="utf-8")
            query.write_text("FROM orders WHERE quantity > 100 SELECT id\n", encoding="utf-8")
            text, code = run_cli("run", str(query), f"--data=orders={data}")
        self.assertEqual(code, 0)
        self.assertIn("id", text)
        self.assertIn("0 row(s)", text)

    def test_header_only_csv_is_a_valid_empty_table(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory) / "orders.csv"
            query = Path(directory) / "empty.usql"
            data.write_text("id,quantity\n", encoding="utf-8")
            query.write_text("FROM orders SELECT id\n", encoding="utf-8")
            text, code = run_cli("run", str(query), f"--data=orders={data}")
        self.assertEqual(code, 0)
        self.assertIn("0 row(s)", text)

    def test_csv_delimiter_and_column_type_overrides(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory) / "orders.csv"
            query = Path(directory) / "query.usql"
            data.write_text("postal;quantity\n02134;2\n00501;3\n", encoding="utf-8")
            query.write_text("FROM orders SELECT postal\n", encoding="utf-8")
            text, code = run_cli(
                "run", str(query), f"--data=orders={data}", "--delimiter", ";",
                "--column-type", "orders.postal=integer", "--format", "json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(text), [{"postal": 2134}, {"postal": 501}])


if __name__ == "__main__":
    unittest.main()
