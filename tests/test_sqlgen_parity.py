"""Parity tests for the Python `.unsequel` analyzer and SQL generator.

The TypeScript compiler is the reference implementation:

1. golden files — generated SQL text matches `compiler/tests/golden/<name>.sql`
   for all 21 reference queries;
2. corpus agreement — every reference and coverage fixture parses, analyzes,
   and lowers without error;
3. execution parity — generated SQL and the hand-written equivalents in
   `compiler/tests/handwritten/` return identical rows in DuckDB.
"""

import datetime
import json
import math
import unittest
from decimal import Decimal
from pathlib import Path

from unsequel.analyzer import SemanticError, analyze
from unsequel.grammar import parse_program
from unsequel.schema import Schema
from unsequel.sqlgen import CodegenError, emit_sql

ROOT = Path(__file__).resolve().parent.parent
COMPILER = ROOT / "compiler"
REFERENCE = COMPILER / "examples" / "reference"
COVERAGE = COMPILER / "examples" / "coverage"
GOLDEN = COMPILER / "tests" / "golden"
HANDWRITTEN = COMPILER / "tests" / "handwritten"
SCHEMA_PATH = ROOT / "examples" / "spec" / "schema.json"
DATASET = ROOT / "examples" / "spec"


def _reference_files():
    return sorted(REFERENCE.glob("*.unsequel"))


def _extended_schema() -> Schema:
    data = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    data["tables"]["analytics.orders"] = data["tables"]["orders"]
    data["tables"]["orders"]["columns"]["shipped"] = "boolean"
    return Schema.from_dict(data)


class GoldenParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = Schema.load(SCHEMA_PATH)

    def test_reference_queries_match_the_compilers_golden_sql(self):
        queries = _reference_files()
        self.assertEqual(len(queries), 21)
        for query in queries:
            with self.subTest(query=query.name):
                program = parse_program(query.read_text(encoding="utf-8"))
                expected = (
                    (GOLDEN / (query.stem + ".sql"))
                    .read_text(encoding="utf-8")
                    .replace("\r\n", "\n")
                    .rstrip()
                )
                self.assertEqual(emit_sql(program, self.schema).rstrip(), expected)


class CorpusAgreementTests(unittest.TestCase):
    def test_every_reference_and_coverage_fixture_analyzes_and_lowers(self):
        schema = Schema.load(SCHEMA_PATH)
        extended = _extended_schema()
        fixtures = [(path, schema) for path in sorted(REFERENCE.glob("*.unsequel"))]
        fixtures += [(path, extended) for path in sorted(COVERAGE.glob("*.unsequel"))]
        self.assertEqual(len(fixtures), 37)
        for path, fixture_schema in fixtures:
            with self.subTest(query=path.name):
                program = parse_program(path.read_text(encoding="utf-8"))
                analyze(program, fixture_schema)
                emit_sql(program, fixture_schema)

    def test_stop_at_keeps_the_whole_group_unit(self):
        schema = Schema.load(SCHEMA_PATH)
        program = parse_program((REFERENCE / "q09_group_count.unsequel").read_text("utf-8"))
        self.assertTrue(emit_sql(program, schema, stop_at=3).endswith("SELECT * FROM stage_2;"))
        self.assertTrue(emit_sql(program, schema, stop_at=1).endswith("SELECT * FROM stage_1;"))

    def test_invalid_stop_at_is_rejected(self):
        schema = Schema.load(SCHEMA_PATH)
        program = parse_program("from orders")
        with self.assertRaises(CodegenError):
            emit_sql(program, schema, stop_at=0)

    def test_input_substitution_respects_word_boundaries(self):
        schema = Schema.load(SCHEMA_PATH)
        program = parse_program(
            'from orders\nsql "SELECT col__input__, __input__.order_id FROM __input__"'
        )
        sql = emit_sql(program, schema)
        self.assertIn("col__input__", sql)
        self.assertIn("stage_1.order_id", sql)


class AnalyzerTests(unittest.TestCase):
    def _schema(self) -> Schema:
        return Schema.from_dict({
            "tables": {
                "orders": {"columns": {"order_id": "integer", "customer": "string"}},
                "customers": {"columns": {"customer": "string", "segment": "string"}},
            }
        })

    def test_unknown_column_is_rejected(self):
        program = parse_program("from orders\nselect missing")
        with self.assertRaisesRegex(SemanticError, "unknown column 'missing'"):
            analyze(program, self._schema())

    def test_ambiguous_bare_reference_after_join_is_rejected(self):
        program = parse_program(
            "from orders\njoin customers on orders.customer = customers.customer\n"
            "filter customer = 'Ada'"
        )
        with self.assertRaisesRegex(SemanticError, "ambiguous column 'customer'"):
            analyze(program, self._schema())

    def test_qualified_reference_after_join_is_fine(self):
        program = parse_program(
            "from orders\njoin customers on orders.customer = customers.customer\n"
            "select customers.customer, segment"
        )
        lineage = analyze(program, self._schema())
        self.assertEqual(
            lineage[1].columns,
            ("order_id", "customer", "customers_customer", "segment"),
        )
        self.assertEqual(lineage[-1].columns, ("customer", "segment"))

    def test_post_group_reference_must_be_a_key_or_aggregate(self):
        program = parse_program("from orders\ngroup by customer\naggregate n = COUNT(*)\nselect n, order_id")
        with self.assertRaisesRegex(SemanticError, "unknown column 'order_id'"):
            analyze(program, self._schema())

    def test_sum_over_text_is_rejected(self):
        program = parse_program("from orders\ngroup by order_id\naggregate total = SUM(customer)")
        with self.assertRaisesRegex(SemanticError, "SUM over 'total' needs a numeric column, got text"):
            analyze(program, self._schema())

    def test_having_cannot_use_a_later_select_alias(self):
        program = parse_program(
            "from orders\ngroup by customer\naggregate n = COUNT(*)\nhaving total > 1\n"
            "select n\nselect total = n + 1"
        )
        with self.assertRaisesRegex(SemanticError, "having runs before select"):
            analyze(program, self._schema())

    def test_set_operator_requires_matching_column_counts(self):
        program = parse_program(
            "from orders\nselect order_id\nunion (from customers | select customer, segment)"
        )
        with self.assertRaisesRegex(SemanticError, "left 1, right 2"):
            analyze(program, self._schema())

    def test_stages_after_sql_are_opaque(self):
        program = parse_program(
            "from orders\nsql \"SELECT * FROM __input__\"\n"
            "filter anything_at_all > 0\nselect outra_column"
        )
        lineage = analyze(program, self._schema())
        self.assertEqual(lineage[1].columns, ("<raw sql>",))
        self.assertEqual(lineage[2].columns, ("<opaque>",))

    def test_with_definition_columns_flow_into_from(self):
        program = parse_program(
            "with recent = (from orders | take 1)\nfrom recent\nselect customer"
        )
        lineage = analyze(program, self._schema())
        self.assertEqual(lineage[1].columns, ("order_id", "customer"))


class DuckDBExecutionParityTests(unittest.TestCase):
    """Generated SQL vs the compiler's hand-written SQL, both in DuckDB."""

    @classmethod
    def setUpClass(cls):
        try:
            import duckdb
        except ImportError:  # pragma: no cover - duckdb is optional
            raise unittest.SkipTest("duckdb is not installed")
        cls.duckdb = duckdb
        cls.con = duckdb.connect()
        data = str(DATASET).replace("\\", "/")
        for table in ["orders", "customers", "products", "members"]:
            cls.con.execute(
                f"CREATE TABLE {table} AS SELECT * FROM "
                f"read_csv_auto('{data}/{table}.csv')"
            )
        cls.schema = Schema.load(SCHEMA_PATH)

    @staticmethod
    def _num(value: float) -> str:
        rounded = round(value * 1e9) / 1e9
        if isinstance(rounded, float) and rounded.is_integer() and abs(rounded) < 1e21:
            return str(int(rounded))
        return str(rounded)

    def _norm(self, value: object) -> str:
        if value is None:
            return "null"
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return self._num(value)
        if isinstance(value, Decimal):
            as_float = float(value)
            return self._num(as_float) if math.isfinite(as_float) else str(value)
        if isinstance(value, (datetime.date, datetime.datetime)):
            return value.isoformat()
        return str(value)

    def _rows(self, sql: str) -> list[str]:
        cursor = self.con.execute(sql)
        columns = [description[0] for description in cursor.description]
        out = [
            json.dumps([[key, self._norm(row[key])] for key in sorted(row)])
            for row in (dict(zip(columns, record)) for record in cursor.fetchall())
        ]
        out.sort()
        return out

    def test_generated_sql_matches_the_handwritten_equivalents(self):
        for query in _reference_files():
            name = query.stem
            program = parse_program(query.read_text(encoding="utf-8"))
            generated = emit_sql(program, self.schema)
            handwritten = (HANDWRITTEN / f"{name}.sql").read_text(encoding="utf-8")
            with self.subTest(query=query.name):
                self.assertEqual(self._rows(generated), self._rows(handwritten))


if __name__ == "__main__":
    unittest.main()
