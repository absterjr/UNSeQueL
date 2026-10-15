"""DuckDB parity tests for the in-memory engine (`unsequel.memory_engine`).

Every reference query and a battery of tricky expressions run on both
engines; the normalized row multisets must match. The extra programs pin the
behaviors that were probed against DuckDB: division, `%`, NULL logic,
coercion, strings, dates, set operators, and window frames.
"""

import datetime
import json
import math
import unittest
from decimal import Decimal
from pathlib import Path

from unsequel.grammar import parse_program
from unsequel.io import load_table
from unsequel.memory_engine import execute_program
from unsequel.schema import Schema
from unsequel.sqlgen import emit_sql

ROOT = Path(__file__).resolve().parent.parent
REFERENCE = ROOT / "compiler" / "examples" / "reference"
DATASET = ROOT / "examples" / "spec"
SCHEMA_PATH = DATASET / "schema.json"
TABLES = ["orders", "customers", "products", "members"]

EXTRA_PROGRAMS = [
    # division and modulo (division by zero is infinity, C-style remainder)
    "from orders\n"
    "select order_id, x = quantity / 0\n"
    "sort order_id",
    "from orders\n"
    "derive r = quantity % 3, z = -quantity % 3, half = quantity / 2\n"
    "select order_id, r, z, half\n"
    "sort order_id",
    # NULL logic and set membership
    "from orders\n"
    "filter customer is null or quantity > 9\n"
    "select order_id, customer\n"
    "sort order_id",
    "from orders\n"
    "select order_id, b = quantity between 2 and 5, i = customer in ('Ada', 'Grace'), "
    "n = customer is not null\n"
    "sort order_id",
    # string handling and number formatting
    "from orders\n"
    "derive lowered = lower(customer), rounded = round(unit_price, 1)\n"
    "select order_id, lowered, rounded\n"
    "sort order_id",
    "from orders\n"
    "select order_id, c = concat(customer, '-', country), s = strftime(order_date, '%Y-%m')\n"
    "sort order_id",
    "from orders\n"
    "select order_id, d = date_trunc('month', order_date), q = date_trunc('quarter', order_date)\n"
    "sort order_id",
    "from orders\n"
    "select order_id, t = cast(quantity as varchar), f = cast(unit_price as integer)\n"
    "sort order_id",
    # set operators
    "from orders\n"
    "select customer\n"
    "union (from customers | select customer)\n"
    "sort customer",
    "from orders\n"
    "select customer, country\n"
    "except (from orders | filter quantity > 5 | select customer, country)\n"
    "sort customer, country",
    "from orders\n"
    "select customer, country\n"
    "intersect (from orders | filter quantity > 5 | select customer, country)\n"
    "sort customer, country",
    # joins: right, full, cross, and post-join pipelines
    "from orders\n"
    "right join members on orders.customer = members.customer\n"
    "select orders.order_id, members.customer, members.plan\n"
    "sort customer, order_id",
    "from orders\n"
    "full join members on orders.customer = members.customer\n"
    "select orders.order_id, members.customer\n"
    "sort order_id, customer",
    "from members\n"
    "cross join products\n"
    "select customer, product_id\n"
    "sort customer, product_id\n"
    "take 5",
    # post-aggregate joins and repeated grouping
    "from orders\n"
    "group by person = customer\n"
    "aggregate n = COUNT(*)\n"
    "join members on person = members.customer\n"
    "select person, n, members.plan\n"
    "sort person, plan",
    "from orders\n"
    "group by country\n"
    "aggregate revenue = SUM(quantity * unit_price)\n"
    "group by revenue = revenue > 100\n"
    "aggregate n = COUNT(*)\n"
    "sort n, revenue",
    # windows: ranking, running sums (RANGE peers), lag, whole-relation
    "from orders\n"
    "window rn = ROW_NUMBER() over (partition by customer order by quantity desc, order_id)\n"
    "filter rn = 1\n"
    "select order_id, customer\n"
    "sort order_id",
    "from orders\n"
    "window running = SUM(quantity) over (partition by customer order by order_id)\n"
    "select order_id, running\n"
    "sort order_id",
    "from orders\n"
    "window previous = LAG(quantity) over (partition by customer order by order_id)\n"
    "select order_id, previous\n"
    "sort order_id",
    "from orders\n"
    "window total = SUM(quantity) over ()\n"
    "derive share = quantity / total\n"
    "select order_id, share\n"
    "sort order_id",
    # nested programs
    "with big = (from orders | filter quantity >= 5 | select order_id, customer)\n"
    "from big\n"
    "select customer\n"
    "sort customer",
    "from (from orders | take 3) as top\n"
    "select top.order_id\n"
    "sort order_id",
    # sql hatch (SQLite) followed by stages
    "from orders\n"
    "sql \"SELECT order_id, customer FROM __input__ WHERE quantity * unit_price > 100\"\n"
    "select order_id\n"
    "sort order_id",
]

try:
    import duckdb

    HAS_DUCKDB = True
except ImportError:  # pragma: no cover - duckdb is optional
    HAS_DUCKDB = False


def _num(value: float) -> str:
    if not math.isfinite(value):
        return str(value)
    rounded = round(value * 1e9) / 1e9
    if isinstance(rounded, float) and rounded.is_integer() and abs(rounded) < 1e21:
        return str(int(rounded))
    return str(rounded)


def _norm(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return _num(float(value))
    if isinstance(value, Decimal):
        as_float = float(value)
        return _num(as_float) if math.isfinite(as_float) else str(value)
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    return str(value)


def _row_set(columns, rows) -> list[str]:
    out = [
        json.dumps([[column, _norm(row.get(column))] for column in sorted(columns)])
        for row in rows
    ]
    out.sort()
    return out


@unittest.skipUnless(HAS_DUCKDB, "duckdb is not installed")
class MemoryParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = Schema.load(SCHEMA_PATH)
        cls.memory_tables = {
            name: load_table(name, DATASET / f"{name}.csv") for name in TABLES
        }
        cls.con = duckdb.connect()
        data = str(DATASET).replace("\\", "/")
        for table in TABLES:
            cls.con.execute(
                f"CREATE TABLE {table} AS SELECT * FROM "
                f"read_csv_auto('{data}/{table}.csv')")

    def _duckdb_rows(self, sql: str) -> list[str]:
        cursor = self.con.execute(sql)
        columns = [description[0] for description in cursor.description]
        rows = [dict(zip(columns, record)) for record in cursor.fetchall()]
        return _row_set(columns, rows)

    def _assert_program_matches(self, text: str):
        program = parse_program(text)
        sql = emit_sql(program, self.schema)
        memory = execute_program(program, self.memory_tables, schema=self.schema)
        self.assertEqual(
            _row_set(memory.columns, memory.rows),
            self._duckdb_rows(sql),
        )

    def test_every_reference_query_matches_duckdb(self):
        queries = sorted(REFERENCE.glob("*.unsequel"))
        self.assertEqual(len(queries), 21)
        for path in queries:
            with self.subTest(query=path.name):
                self._assert_program_matches(path.read_text(encoding="utf-8"))

    def test_probed_behavior_battery_matches_duckdb(self):
        for index, text in enumerate(EXTRA_PROGRAMS, start=1):
            with self.subTest(program=index, text=text):
                self._assert_program_matches(text)


if __name__ == "__main__":
    unittest.main()
