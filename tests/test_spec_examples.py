"""Step 2 validation: the 20 reference pipeline queries in examples/spec/.

Each query must parse unambiguously against docs/pipeline-spec.md v0.2 and
execute on the sample dataset. Row counts are pinned so later steps (semantic
analysis, DuckDB codegen) have a fixed behavioural baseline to match.
"""

import unittest
from pathlib import Path

from unsequel.engine import execute
from unsequel.io import load_table
from unsequel.pipeline import execute_pipeline_stages, parse_pipeline_stages

SPEC = Path("examples/spec")

EXPECTED_ROW_COUNT = {
    "q01_all_orders": 15,
    "q02_project_columns": 15,
    "q03_row_filter": 6,
    "q04_filter_and_sort": 5,
    "q05_derive_column": 15,
    "q06_derive_rank_take": 5,
    "q07_inner_join": 15,
    "q08_left_join_antijoin": 3,
    "q09_group_count": 3,
    "q10_group_sum_rank": 3,
    "q11_group_having": 3,
    "q12_post_group_metric": 5,
    "q13_join_group_topn": 2,
    "q14_distinct": 3,
    "q15_pagination": 5,
    "q16_in_list": 8,
    "q17_between": 6,
    "q18_case_bucket": 15,
    "q19_multi_key_group": 11,
    "q20_full_pipeline": 3,
    "q21_sql_hatch": 5,
}


def load_sample():
    return {
        name: load_table(name, SPEC / f"{name}.csv")
        for name in ("orders", "customers", "products", "members")
    }


class SpecExampleTests(unittest.TestCase):
    def setUp(self):
        self.tables = load_sample()

    def test_all_twenty_files_present(self):
        found = sorted(p.stem for p in SPEC.glob("q*.pusql"))
        self.assertEqual(found, sorted(EXPECTED_ROW_COUNT))

    def test_each_query_parses_and_executes(self):
        for name, expected in EXPECTED_ROW_COUNT.items():
            with self.subTest(query=name):
                stages = parse_pipeline_stages((SPEC / f"{name}.pusql").read_text(encoding="utf-8"))
                result = execute_pipeline_stages(stages, self.tables)
                self.assertEqual(len(result.rows), expected)

    def test_full_pipeline_output(self):
        stages = parse_pipeline_stages((SPEC / "q20_full_pipeline.pusql").read_text(encoding="utf-8"))
        result = execute_pipeline_stages(stages, self.tables)
        self.assertEqual(result.rows, [
            {"category": "home", "orders": 6, "revenue": 285, "avg_order": 47.5},
            {"category": "stationery", "orders": 6, "revenue": 177.0, "avg_order": 29.5},
            {"category": "grocery", "orders": 3, "revenue": 132.0, "avg_order": 44.0},
        ])

    def test_group_having_filters_groups(self):
        stages = parse_pipeline_stages((SPEC / "q11_group_having.pusql").read_text(encoding="utf-8"))
        result = execute_pipeline_stages(stages, self.tables)
        self.assertEqual(result.rows, [
            {"customer": "Katherine", "revenue": 177.0},
            {"customer": "Ada", "revenue": 162.0},
            {"customer": "Grace", "revenue": 151.5},
        ])

    def test_antijoin_keeps_only_unmatched(self):
        stages = parse_pipeline_stages((SPEC / "q08_left_join_antijoin.pusql").read_text(encoding="utf-8"))
        result = execute_pipeline_stages(stages, self.tables)
        self.assertEqual({row["customer"] for row in result.rows}, {"Alan"})


if __name__ == "__main__":
    unittest.main()
