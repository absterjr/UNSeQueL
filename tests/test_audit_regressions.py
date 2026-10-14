import tempfile
import unittest
from pathlib import Path

from unsequel.cli import _table_output
from unsequel.engine import execute
from unsequel.errors import ExecutionError
from unsequel.io import load_table
from unsequel.model import Table
from unsequel.parser import parse_query


class ExecutionAuditRegressionTests(unittest.TestCase):
    def test_grouped_query_rejects_unaggregated_non_group_columns(self):
        query = parse_query("FROM t GROUP BY g SELECT g, v, s")
        table = Table("t", ["g", "v", "s"], [
            {"g": "a", "v": 1, "s": "first"},
            {"g": "a", "v": 2, "s": "second"},
        ])
        with self.assertRaisesRegex(ExecutionError, "must appear in GROUP BY"):
            execute(query, {"t": table})

    def test_having_explains_that_select_aliases_are_not_available_yet(self):
        query = parse_query(
            "FROM t GROUP BY g HAVING n > 0 SELECT g, COUNT(*) AS n")
        table = Table("t", ["g"], [{"g": "a"}])
        with self.assertRaisesRegex(ExecutionError, "HAVING runs before SELECT"):
            execute(query, {"t": table})

    def test_grouped_query_rejects_wildcard(self):
        query = parse_query("FROM t GROUP BY g SELECT *")
        with self.assertRaisesRegex(ExecutionError, r"SELECT \* cannot be used"):
            execute(query, {"t": Table("t", ["g"], [{"g": "a"}])})

    def test_order_by_one_based_ordinal_sorts_projected_columns(self):
        query = parse_query("FROM t SELECT label, id ORDER BY 2 DESC")
        table = Table("t", ["label", "id"], [
            {"label": "low", "id": 1},
            {"label": "high", "id": 3},
            {"label": "middle", "id": 2},
        ])
        result = execute(query, {"t": table})
        self.assertEqual([row["id"] for row in result.rows], [3, 2, 1])

    def test_order_by_rejects_out_of_range_ordinal(self):
        query = parse_query("FROM t SELECT id ORDER BY 2")
        with self.assertRaisesRegex(ExecutionError, "ordinal 2 is out of range"):
            execute(query, {"t": Table("t", ["id"], [{"id": 1}])})

    def test_ambiguous_join_reference_is_rejected(self):
        query = parse_query(
            "FROM t AS a JOIN u AS b ON a.id = b.id SELECT id, name")
        tables = {
            "t": Table("t", ["id", "name"], [{"id": 1, "name": "left"}]),
            "u": Table("u", ["id", "name"], [{"id": 1, "name": "right"}]),
        }
        with self.assertRaisesRegex(ExecutionError, "ambiguous column 'id'"):
            execute(query, tables)

    def test_select_star_refuses_to_drop_duplicate_join_columns(self):
        query = parse_query("FROM t AS a JOIN u AS b ON a.id = b.id SELECT *")
        tables = {
            "t": Table("t", ["id", "name"], [{"id": 1, "name": "left"}]),
            "u": Table("u", ["id", "name"], [{"id": 1, "name": "right"}]),
        }
        with self.assertRaisesRegex(ExecutionError, r"SELECT \* would drop duplicate"):
            execute(query, tables)

    def test_arithmetic_rejects_text_instead_of_using_python_coercion(self):
        query = parse_query("FROM t SELECT value * 2")
        table = Table("t", ["value"], [{"value": "abc"}])
        with self.assertRaisesRegex(ExecutionError, "requires numeric operands"):
            execute(query, {"t": table})

    def test_aggregates_and_sort_report_incompatible_types_cleanly(self):
        table = Table("t", ["value"], [{"value": 1}, {"value": "dirty"}])
        with self.assertRaisesRegex(ExecutionError, "SUM requires numeric operands"):
            execute(parse_query("FROM t SELECT SUM(value) AS total"), {"t": table})
        with self.assertRaisesRegex(ExecutionError, "ORDER BY values have incompatible types"):
            execute(parse_query("FROM t SELECT value ORDER BY value"), {"t": table})

    def test_like_is_case_sensitive(self):
        table = Table("t", ["value"], [{"value": "Ada"}, {"value": "ada"}])
        result = execute(parse_query("FROM t WHERE value LIKE 'ada' SELECT value"), {"t": table})
        self.assertEqual(result.rows, [{"value": "ada"}])

    def test_empty_table_output_has_headers_and_zero_rows(self):
        self.assertEqual(_table_output([], ["id"]), "id\n--")

    def test_table_output_renders_null_explicitly(self):
        self.assertIn("NULL", _table_output([{"value": None}], ["value"]))
        self.assertNotIn("None", _table_output([{"value": None}], ["value"]))


class CsvAuditRegressionTests(unittest.TestCase):
    def _csv(self, text: str) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "input.csv"
        path.write_text(text, encoding="utf-8")
        return path

    def test_inference_is_column_wide_and_preserves_identifiers_and_null_words(self):
        path = self._csv(
            "postal;dirty;word\n"
            "02134;1;null\n"
            "00501;bad;None\n"
            "12345;3;nan\n")
        table = load_table("t", path, delimiter=";")
        self.assertEqual([row["postal"] for row in table.rows], ["02134", "00501", "12345"])
        self.assertEqual([row["dirty"] for row in table.rows], ["1", "bad", "3"])
        self.assertEqual([row["word"] for row in table.rows], ["null", "None", "nan"])

    def test_empty_cells_become_null_but_literal_null_strings_survive(self):
        path = self._csv('value\n""\nnull\nnone\nnan\n')
        table = load_table("t", path)
        self.assertEqual([row["value"] for row in table.rows], [None, "null", "none", "nan"])

    def test_duplicate_headers_are_rejected_instead_of_overwritten(self):
        path = self._csv("a,a,b\n1,2,3\n")
        with self.assertRaisesRegex(ExecutionError, "duplicate column name 'a'"):
            load_table("t", path)

    def test_column_type_override_is_explicit_and_validated(self):
        path = self._csv("postal;quantity\n02134;2\n00501;3\n")
        table = load_table("t", path, delimiter=";", column_types={"postal": "integer"})
        self.assertEqual([row["postal"] for row in table.rows], [2134, 501])
        with self.assertRaisesRegex(ExecutionError, "cannot convert '02134'"):
            load_table("t", path, delimiter=";", column_types={"postal": "boolean"})


if __name__ == "__main__":
    unittest.main()
