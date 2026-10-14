"""Small semantic probes shared by the memory and DuckDB backends."""

import tempfile
import unittest
from pathlib import Path

from unsequel.codegen import emit_sql
from unsequel.duckdb_backend import run_sql
from unsequel.io import load_table
from unsequel.pipeline import execute_pipeline_stages, parse_pipeline_stages
from unsequel.schema import Schema


try:
    import duckdb  # noqa: F401
    HAS_DUCKDB = True
except ImportError:
    HAS_DUCKDB = False


@unittest.skipUnless(HAS_DUCKDB, "duckdb not installed")
class CrossEngineConformanceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.data_path = Path(self.directory.name) / "samples.csv"
        self.data_path.write_text(
            "id,label,amount\n"
            "1,Ada,2\n"
            "2,ada,2\n"
            "3,None,\n"
            "4,none,1\n",
            encoding="utf-8",
        )
        self.table = load_table("samples", self.data_path)
        self.schema = Schema.from_dict({
            "tables": {"samples": {"columns": {
                "id": "integer", "label": "text", "amount": "integer",
            }}}
        })
        self.tables = {"samples": self.table}

    def _both(self, query: str):
        stages = parse_pipeline_stages(query)
        memory = execute_pipeline_stages(stages, self.tables)
        sql = emit_sql(stages, self.schema)
        duck = run_sql(sql, {"samples": str(self.data_path)})
        return memory, duck

    def test_like_case_and_null_predicates_match(self):
        memory, duck = self._both(
            "from samples\n"
            "where label LIKE 'a%' OR amount IS NULL\n"
            "select id, label, amount\n"
            "sort id"
        )
        self.assertEqual(memory.rows, duck.rows)
        self.assertEqual([row["id"] for row in memory.rows], [2, 3])

    def test_null_order_and_tied_sort_keys_match_with_a_tie_breaker(self):
        memory, duck = self._both(
            "from samples\nselect id, amount\nsort amount, id")
        self.assertEqual(memory.rows, duck.rows)
        self.assertEqual([row["id"] for row in memory.rows], [4, 1, 2, 3])

    def test_empty_filter_and_aggregate_nulls_match(self):
        memory, duck = self._both(
            "from samples\nwhere id > 100\ngroup label (n = COUNT(*))")
        self.assertEqual(memory.rows, duck.rows)
        self.assertEqual(memory.rows, [])

    def test_group_aggregates_ignore_nulls_in_both_engines(self):
        memory, duck = self._both(
            "from samples\ngroup label (n = COUNT(*), total = SUM(amount))\nsort label")
        self.assertEqual(memory.rows, duck.rows)
        none_group = next(row for row in memory.rows if row["label"] == "None")
        self.assertEqual(none_group["n"], 1)
        self.assertIsNone(none_group["total"])


if __name__ == "__main__":
    unittest.main()
