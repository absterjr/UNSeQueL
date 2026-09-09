"""Step 6: DuckDB SQL codegen — one CTE per stage.

Two checks per reference query:

1. Golden-file — the generated SQL text matches `tests/golden/<name>.sql`.
2. Execution — the generated SQL and a hand-written equivalent in
   `tests/handwritten/<name>.sql` return the same rows when run against the
   seeded sample data in DuckDB. Skipped if `duckdb` is not installed.
"""

import unittest
from pathlib import Path

from unsequel.codegen import CodegenError, emit_sql
from unsequel.pipeline_ir import parse_pipeline_stages
from unsequel.schema import Schema

SPEC = Path("examples/spec")
GOLDEN = Path("tests/golden")
HANDWRITTEN = Path("tests/handwritten")
SCHEMA = Schema.load(SPEC / "schema.json")

try:
    import duckdb  # noqa: F401
    HAS_DUCKDB = True
except ImportError:
    HAS_DUCKDB = False

QUERIES = sorted(p.stem for p in SPEC.glob("q*.pusql"))


def generate(name: str) -> str:
    return emit_sql(parse_pipeline_stages((SPEC / f"{name}.pusql").read_text()), SCHEMA)


class GoldenFileTests(unittest.TestCase):
    def test_generated_sql_matches_golden(self):
        for name in QUERIES:
            with self.subTest(query=name):
                expected = (GOLDEN / f"{name}.sql").read_text().rstrip("\n")
                self.assertEqual(generate(name).rstrip("\n"), expected,
                                 f"golden drift for {name}; regenerate tests/golden/")

    def test_every_query_has_one_cte_per_stage(self):
        for name in QUERIES:
            with self.subTest(query=name):
                stages = parse_pipeline_stages((SPEC / f"{name}.pusql").read_text())
                sql = generate(name)
                self.assertEqual(sql.count(" AS (\n"), len(stages))
                self.assertTrue(sql.rstrip().endswith(f"SELECT * FROM stage_{len(stages)};"))

    def test_preview_truncates_the_chain(self):
        stages = parse_pipeline_stages((SPEC / "q20_full_pipeline.pusql").read_text())
        preview = emit_sql(stages, SCHEMA, stop_at=4)
        self.assertTrue(preview.rstrip().endswith("SELECT * FROM stage_4;"))
        self.assertNotIn("stage_5", preview)

    def test_codegen_requires_schema_knowledge(self):
        stages = parse_pipeline_stages("from mystery_table")
        with self.assertRaises(CodegenError):
            emit_sql(stages, SCHEMA)


@unittest.skipUnless(HAS_DUCKDB, "duckdb not installed")
class ExecutionParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import duckdb
        cls.con = duckdb.connect()
        for table in ("orders", "customers", "products", "members"):
            cls.con.execute(
                f"CREATE TABLE {table} AS "
                f"SELECT * FROM read_csv_auto('{SPEC / f'{table}.csv'}')"
            )

    def _rows(self, sql: str):
        cur = self.con.execute(sql)
        columns = [d[0] for d in cur.description]
        out = []
        for row in cur.fetchall():
            out.append(tuple((col, _norm(value)) for col, value in zip(columns, row)))
        return sorted(map(repr, out))

    def test_generated_sql_matches_handwritten(self):
        for name in QUERIES:
            with self.subTest(query=name):
                generated = self._rows(generate(name))
                handwritten = self._rows((HANDWRITTEN / f"{name}.sql").read_text())
                self.assertEqual(generated, handwritten)


def _norm(value):
    if isinstance(value, float):
        return round(value, 9)
    try:
        from decimal import Decimal
        if isinstance(value, Decimal):
            return round(float(value), 9)
    except ImportError:  # pragma: no cover
        pass
    return str(value) if value is not None else None


if __name__ == "__main__":
    unittest.main()
