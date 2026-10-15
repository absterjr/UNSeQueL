"""CLI wiring for `.unsequel` files: run / check / compile / preview."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from unsequel.cli import main

SPEC = Path("examples/spec")
SCHEMA = str(SPEC / "schema.json")
QUERY = "compiler/examples/reference/q09_group_count.unsequel"
DATA = [f"--data=orders={SPEC / 'orders.csv'}"]

try:
    import duckdb  # noqa: F401
    HAS_DUCKDB = True
except ImportError:
    HAS_DUCKDB = False


def run_cli(*argv) -> tuple[str, int]:
    out = io.StringIO()
    code = 0
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            main(list(argv))
    except SystemExit as exit_:
        code = exit_.code or 0
    return out.getvalue(), code


@unittest.skipUnless(HAS_DUCKDB, "duckdb is not installed")
class UnsequelCliTests(unittest.TestCase):
    def test_run_with_schema_and_data(self):
        out, code = run_cli("run", QUERY, "--schema", SCHEMA, *DATA)
        self.assertEqual(code, 0)
        self.assertIn("country", out)
        self.assertIn("row(s)", out)

    def test_run_introspects_data_without_a_schema(self):
        out, code = run_cli("run", QUERY, *DATA)
        self.assertEqual(code, 0)
        self.assertIn("country", out)

    def test_run_needs_a_schema_or_data(self):
        out, code = run_cli("run", QUERY)
        self.assertEqual(code, 1)
        self.assertIn("schema", out)

    def test_default_engine_is_the_memory_engine(self):
        default_out, code = run_cli("run", QUERY, "--schema", SCHEMA, *DATA)
        memory_out, code2 = run_cli("run", QUERY, "--schema", SCHEMA, *DATA,
                                    "--engine", "memory")
        self.assertEqual((code, code2), (0, 0))
        self.assertEqual(default_out, memory_out)

    def test_memory_and_duckdb_engines_agree(self):
        memory_out, code = run_cli("run", QUERY, "--schema", SCHEMA, *DATA,
                                   "--engine", "memory")
        duckdb_out, code2 = run_cli("run", QUERY, "--schema", SCHEMA, *DATA,
                                    "--engine", "duckdb")
        self.assertEqual((code, code2), (0, 0))
        # tied sort keys are engine-defined; compare the row sets.
        self.assertEqual(sorted(memory_out.splitlines()), sorted(duckdb_out.splitlines()))

    def test_flagless_file_via_directives(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "orders.csv").write_text(
                "id,country\n1,UK\n2,UK\n3,US\n", encoding="utf-8")
            (directory / "schema.json").write_text(
                json.dumps({"tables": {"orders": {"columns": {
                    "id": "integer", "country": "string"}}}}),
                encoding="utf-8")
            (directory / "query.unsequel").write_text(
                "# source orders: orders.csv\n"
                "# schema: schema.json\n"
                "from orders\n"
                "group by country\n"
                "aggregate n = COUNT(*)\n"
                "sort country\n",
                encoding="utf-8")
            out, code = run_cli("run", str(directory / "query.unsequel"))
            self.assertEqual(code, 0)
            self.assertIn("UK", out)

    def test_out_writes_csv_and_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "result.csv"
            out, code = run_cli("run", QUERY, "--schema", SCHEMA, *DATA,
                                "--out", str(csv_path))
            self.assertEqual(code, 0)
            self.assertIn("wrote", out)
            text = csv_path.read_text(encoding="utf-8")
            self.assertEqual(text.splitlines()[0], "country,orders")
            self.assertIn("United Kingdom", text)

            json_path = Path(tmp) / "result.json"
            out, code = run_cli("run", QUERY, "--schema", SCHEMA, *DATA,
                                "--out", str(json_path))
            self.assertEqual(code, 0)
            first = json.loads(json_path.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(set(first), {"country", "orders"})

    @unittest.skipUnless(HAS_DUCKDB, "duckdb is not installed")
    def test_out_parquet_via_duckdb(self):
        with tempfile.TemporaryDirectory() as tmp:
            parquet = Path(tmp) / "result.parquet"
            out, code = run_cli("run", QUERY, "--schema", SCHEMA, *DATA,
                                "--out", str(parquet))
            self.assertEqual(code, 0)
            data = parquet.read_bytes()
            self.assertEqual(data[:4], b"PAR1")
            self.assertGreater(len(data), 100)

    def test_out_rejects_unknown_extension(self):
        out, code = run_cli("run", QUERY, "--schema", SCHEMA, *DATA,
                            "--out", "result.xlsx")
        self.assertEqual(code, 1)
        self.assertIn("--out must end in", out)

    def test_check_reports_schema_ok(self):
        out, code = run_cli("check", QUERY, "--schema", SCHEMA)
        self.assertEqual(code, 0)
        self.assertIn("valid UNSeQueL program (schema OK)", out)

    def test_check_reports_stage_located_semantic_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.unsequel"
            path.write_text("from orders\nselect missing_column\n", encoding="utf-8")
            out, code = run_cli("check", str(path), "--schema", SCHEMA)
            self.assertEqual(code, 1)
            self.assertIn("stage 2 (select)", out)
            self.assertIn("unknown column 'missing_column'", out)

    def test_compile_prints_the_with_chain(self):
        out, code = run_cli("compile", QUERY, "--schema", SCHEMA)
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("WITH stage_1 AS ("))
        self.assertIn("GROUP BY country", out)
        self.assertIn("stage_3 AS (", out)

    def test_compile_stage_truncates(self):
        out, code = run_cli("compile", QUERY, "--schema", SCHEMA, "--stage", "1")
        self.assertEqual(code, 0)
        self.assertTrue(out.rstrip().endswith("SELECT * FROM stage_1;"))

    def test_preview_truncates_at_a_stage(self):
        out, code = run_cli("preview", QUERY, "--schema", SCHEMA, "--stage", "2", *DATA)
        self.assertEqual(code, 0)
        self.assertIn("stage 2/4", out)
        self.assertIn("group by", out)

    def test_pipeline_compile_still_requires_a_schema(self):
        legacy = SPEC / "q03_row_filter.pusql"
        out, code = run_cli("compile", str(legacy))
        self.assertEqual(code, 1)
        self.assertIn("--schema is required", out)


if __name__ == "__main__":
    unittest.main()
