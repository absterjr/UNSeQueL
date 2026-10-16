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

    def test_default_engine_is_duckdb_when_available(self):
        default_out, code = run_cli("run", QUERY, "--schema", SCHEMA, *DATA)
        duckdb_out, code2 = run_cli("run", QUERY, "--schema", SCHEMA, *DATA,
                                    "--engine", "duckdb")
        memory_out, code3 = run_cli("run", QUERY, "--schema", SCHEMA, *DATA,
                                    "--engine", "memory")
        self.assertEqual((code, code2, code3), (0, 0, 0))
        self.assertEqual(sorted(default_out.splitlines()),
                         sorted(duckdb_out.splitlines()))
        self.assertEqual(sorted(default_out.splitlines()),
                         sorted(memory_out.splitlines()))

    def test_date_functions_agree_across_engines(self):
        query = "compiler/examples/real/r04_monthly_revenue.unsequel"
        memory_out, code1 = run_cli("run", query, "--engine", "memory")
        duckdb_out, code2 = run_cli("run", query, "--engine", "duckdb")
        self.assertEqual((code1, code2), (0, 0))
        self.assertEqual(memory_out, duckdb_out)

    @unittest.skipUnless(HAS_DUCKDB, "duckdb is not installed")
    def test_db_attach_and_directives(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            database = directory / "library.duckdb"
            import duckdb
            connection = duckdb.connect(str(database))
            connection.execute(
                "CREATE TABLE books AS SELECT * FROM "
                "(VALUES (1, 'Dune'), (2, 'Emma')) AS t(id, title)")
            connection.close()

            attached = directory / "attached.unsequel"
            attached.write_text("from library.books\nselect title\nsort title\n",
                                encoding="utf-8")
            out, code = run_cli("run", str(attached), "--attach", f"library={database}")
            self.assertEqual((code, "Dune" in out, "Emma" in out), (0, True, True))

            directive = directory / "directive.unsequel"
            directive.write_text(f"# db: {database}\nfrom books\nselect title\nsort title\n",
                                 encoding="utf-8")
            out2, code2 = run_cli("run", str(directive))
            self.assertEqual((code2, "Dune" in out2), (0, True))

            out3, code3 = run_cli("run", str(directive), "--engine", "memory")
            self.assertEqual(code3, 1)
            self.assertIn("databases need DuckDB", out3)

    def test_missing_database_is_reported(self):
        query = "compiler/examples/real/r06_composition.unsequel"
        out, code = run_cli("run", query, "--db", "missing_directory/missing.duckdb")
        self.assertEqual(code, 1)
        self.assertIn("database not found", out)

    def test_memory_engine_refuses_the_sql_hatch(self):
        query = "compiler/examples/reference/q21_sql_hatch.unsequel"
        out, code = run_cli("run", query, "--schema", SCHEMA, *DATA, "--engine", "memory")
        self.assertEqual(code, 1)
        self.assertIn("sql hatch runs DuckDB SQL", out)

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

    def test_unsequel_flag_routes_unrecognized_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "query.txt"
            path.write_text(
                "from orders\ngroup by country\naggregate n = COUNT(*)\nsort country\n",
                encoding="utf-8")
            out, code = run_cli("run", str(path), "--unsequel", "--schema", SCHEMA, *DATA)
            self.assertEqual(code, 0)
            self.assertIn("United Kingdom", out)

    def test_legacy_surfaces_get_a_deprecation_notice(self):
        legacy = str(SPEC / "q03_row_filter.pusql")
        out, code = run_cli("run", legacy, "--data", f"orders={SPEC / 'orders.csv'}")
        self.assertEqual(code, 0)
        self.assertIn("legacy surfaces being retired", out)

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
            rows = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(set(rows[0]), {"country", "orders"})

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

    @unittest.skipUnless(HAS_DUCKDB, "duckdb is not installed")
    def test_create_into_and_import(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            database = directory / "warehouse.duckdb"
            orders = (SPEC / "orders.csv").resolve()

            create = directory / "create.unsequel"
            create.write_text(
                f"# source orders: {orders}\n"
                "create table summary = (from orders | group by country | "
                "aggregate n = COUNT(*))\n",
                encoding="utf-8")
            # A write program never silently creates a database file...
            refused = directory / "refused.duckdb"
            out, code = run_cli("run", str(create), "--db", str(refused))
            self.assertEqual(code, 1)
            self.assertIn("--create-db", out)
            self.assertFalse(refused.exists())

            out, code = run_cli("run", str(create), "--db", str(database), "--create-db")
            self.assertEqual((code, "applied 1 statement(s)" in out), (0, True))
            self.assertTrue(database.exists())

            # ...and rerunning without `or replace` refuses to overwrite.
            out, code = run_cli("run", str(create), "--db", str(database))
            self.assertEqual(code, 1)
            self.assertIn("create or replace", out)
            create.write_text(
                f"# source orders: {orders}\n"
                "create or replace table summary = (from orders | group by country | "
                "aggregate n = COUNT(*))\n",
                encoding="utf-8")
            out, code = run_cli("run", str(create), "--db", str(database))
            self.assertEqual((code, "applied 1 statement(s)" in out), (0, True))

            readback = directory / "readback.unsequel"
            readback.write_text("from summary\nsort country\n", encoding="utf-8")
            out, code = run_cli("run", str(readback), "--db", str(database),
                                "--format", "json")
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out), [
                {"country": "Netherlands", "n": 1},
                {"country": "United Kingdom", "n": 7},
                {"country": "United States", "n": 7},
            ])

            append = directory / "append.unsequel"
            append.write_text(
                f"# source orders: {orders}\n"
                "into summary (from orders | group by country | aggregate n = COUNT(*))\n",
                encoding="utf-8")
            out, code = run_cli("run", str(append), "--db", str(database))
            self.assertEqual((code, "applied 1 statement(s)" in out), (0, True))

            readback.write_text(
                "from summary\ngroup by country\naggregate total = SUM(n)\nsort country\n",
                encoding="utf-8")
            out, code = run_cli("run", str(readback), "--db", str(database),
                                "--format", "json")
            self.assertEqual(json.loads(out)[1],
                             {"country": "United Kingdom", "total": 14})

            append.write_text(
                f"# source orders: {orders}\n"
                "into missing (from orders | select country)\n",
                encoding="utf-8")
            out, code = run_cli("run", str(append), "--db", str(database))
            self.assertEqual(code, 1)
            self.assertIn("needs an existing table", out)

            # BY NAME: column order doesn't matter, only the set.
            append.write_text(
                f"# source orders: {orders}\n"
                "into summary (from summary | select n, country)\n",
                encoding="utf-8")
            out, code = run_cli("run", str(append), "--db", str(database))
            self.assertEqual(code, 0)

            # Type families must match the target.
            append.write_text(
                f"# source orders: {orders}\n"
                "into summary (from orders | select country, n = country)\n",
                encoding="utf-8")
            out, code = run_cli("run", str(append), "--db", str(database))
            self.assertEqual(code, 1)
            self.assertIn("CAST it explicitly", out)

            # A persistent view may not read a temporary source.
            view = directory / "view.unsequel"
            view.write_text(
                f"# source orders: {orders}\n"
                "create view v = (from orders | select order_id)\n",
                encoding="utf-8")
            out, code = run_cli("run", str(view), "--db", str(database))
            self.assertEqual(code, 1)
            self.assertIn("reads the temporary source 'orders'", out)

            create.write_text("create table x = (from orders | select order_id)\n",
                              encoding="utf-8")
            out, code = run_cli("run", str(create))
            self.assertEqual(code, 1)
            self.assertIn("write to a database", out)

            nested = directory / "nested.unsequel"
            nested.write_text(
                "with inner = (create table y = (from orders))\nfrom inner\n",
                encoding="utf-8")
            out, code = run_cli("run", str(nested))
            self.assertEqual(code, 1)
            self.assertIn("only appear in the top-level program", out)

            imported = directory / "imported.duckdb"
            out, code = run_cli("import", str(orders), "--into", str(imported),
                                "--table", "orders")
            self.assertEqual((code, "imported" in out), (0, True))
            self.assertIn("created new database", out)

            # A plain import never overwrites; --replace does.
            out, code = run_cli("import", str(orders), "--into", str(imported),
                                "--table", "orders")
            self.assertEqual(code, 1)
            self.assertIn("already exists; pass --replace", out)
            out, code = run_cli("import", str(orders), "--into", str(imported),
                                "--table", "orders", "--replace")
            self.assertEqual(code, 0)
            self.assertNotIn("created new database", out)

            readback.write_text("from orders\nselect order_id\nsort order_id\ntake 1\n",
                                encoding="utf-8")
            out, code = run_cli("run", str(readback), "--db", str(imported),
                                "--format", "json")
            self.assertEqual(json.loads(out), [{"order_id": 5001}])

    def test_source_hiding_a_table_gets_a_note(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            database = directory / "warehouse.duckdb"
            orders = (SPEC / "orders.csv").resolve()
            out, code = run_cli("import", str(orders), "--into", str(database),
                                "--table", "orders")
            self.assertEqual(code, 0)
            query = directory / "q.unsequel"
            query.write_text(f"# source orders: {orders}\nfrom orders\ntake 1\n",
                             encoding="utf-8")
            out, code = run_cli("run", str(query), "--db", str(database),
                                "--format", "json")
            self.assertEqual(code, 0)
            self.assertIn("note: source 'orders' hides database table 'orders'", out)

    def test_hatch_built_table_gets_a_hint(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "hatch.unsequel"
            path.write_text(
                'with staging = (from orders | sql "CREATE TABLE helper AS SELECT 1")\n'
                "from helper\n", encoding="utf-8")
            out, code = run_cli("compile", str(path), "--schema", SCHEMA)
            self.assertEqual(code, 1)
            self.assertIn("a sql hatch cannot create tables", out)
            self.assertIn("create table helper = (...)", out)

    def test_read_query_runtime_errors_name_the_stage(self):
        with tempfile.TemporaryDirectory() as tmp:
            orders = (SPEC / "orders.csv").resolve()
            query = Path(tmp) / "bad.unsequel"
            query.write_text(
                f"# source orders: {orders}\n"
                "from orders\nderive n = CAST(country AS INTEGER)\n",
                encoding="utf-8")
            out, code = run_cli("run", str(query), "--engine", "duckdb")
            self.assertEqual(code, 1)
            self.assertIn("stage 2 (derive)", out)

    def test_check_reports_schema_ok(self):
        out, code = run_cli("check", QUERY, "--schema", SCHEMA)
        self.assertEqual(code, 0)
        self.assertIn("valid UNSeQueL program (schema OK)", out)

    def test_check_reports_syntax_only_without_schema_or_sources(self):
        out, code = run_cli("check", QUERY)
        self.assertEqual(code, 0)
        self.assertIn("valid UNSeQueL program (syntax OK; columns not checked)", out)

    def test_check_surfaces_parse_errors_without_a_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "broken.unsequel"
            path.write_text("from orders\nfilter quantity >\n", encoding="utf-8")
            out, code = run_cli("check", str(path))
            self.assertEqual(code, 1)
            self.assertIn("stage 2 (filter)", out)

    def test_check_validates_columns_from_source_directives(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            orders = (SPEC / "orders.csv").resolve()
            good = directory / "good.unsequel"
            good.write_text(f"# source orders: {orders}\nfrom orders\n"
                            "filter quantity > 1\n", encoding="utf-8")
            out, code = run_cli("check", str(good))
            self.assertEqual((code, "valid UNSeQueL program (schema OK)" in out), (0, True))

            bad = directory / "bad.unsequel"
            bad.write_text(f"# source orders: {orders}\nfrom orders\n"
                           "filter missing > 1\n", encoding="utf-8")
            out, code = run_cli("check", str(bad))
            self.assertEqual(code, 1)
            self.assertIn("stage 2 (filter)", out)

    def test_number_spellings_edge_fixture(self):
        edge = "compiler/examples/edge/e01_number_spellings.unsequel"
        out, code = run_cli("run", edge, "--format", "csv", "--engine", "duckdb")
        self.assertEqual(code, 0)
        self.assertEqual(
            out.splitlines()[0],
            "dec,half,tiny,sci,wideint,negzero,hugebig,wide,flag,mixed")
        self.assertIn("4.00,1,4e-7,4e+21,400000000000000000000,0,"
                      "9007199254740993,1234567890123456789012,true,10", out)

        out, code = run_cli("run", edge, "--format", "json", "--engine", "duckdb")
        self.assertEqual(code, 0)
        rows = json.loads(out)
        self.assertEqual(rows[0], {
            "dec": 4,
            "half": 1,
            "tiny": 4e-7,
            "sci": 4e21,
            "wideint": "400000000000000000000",
            "negzero": 0,
            "hugebig": "9007199254740993",
            "wide": "1234567890123456789012",
            "flag": True,
            # One JSON type per column: 'mixed' holds 10 and a value beyond
            # 2^53, so both values are exact text.
            "mixed": "10",
        })
        self.assertEqual(rows[1]["sci"], 1e22)
        self.assertEqual(rows[1]["mixed"], "9007199254740993")

    def test_big_literals_keep_source_text(self):
        edge = "compiler/examples/edge/e02_big_literals.unsequel"
        out, code = run_cli("run", edge, "--format", "csv", "--engine", "duckdb")
        self.assertEqual(code, 0)
        # The decoy row (id ...790) must not match a rounded literal.
        self.assertEqual(out.strip().splitlines(), [
            "n,id,wide,r",
            "4,1234567890123456789,123456789012345678901234567,"
            "0.1000000000000000000001",
        ])

        out, code = run_cli("compile", edge)
        self.assertEqual(code, 0)
        self.assertIn("id = 1234567890123456789", out)
        self.assertIn("wide = 123456789012345678901234567", out)
        self.assertIn("r = 0.1000000000000000000001", out)
        self.assertNotIn("e+26", out)
        self.assertNotIn("1234567890123456800", out)

    def test_statement_program_names_the_failing_stage_and_rolls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            database = directory / "warehouse.duckdb"
            orders = (SPEC / "orders.csv").resolve()
            program = directory / "bad.unsequel"
            program.write_text(
                f"# source orders: {orders}\n"
                f"# db: {database}\n"
                "create table plain = (from orders | select country, "
                "n = CAST(quantity AS BIGINT))\n"
                "into plain (from plain | select country, "
                "n = CAST('abc' AS INTEGER))\n",
                encoding="utf-8")
            out, code = run_cli("run", str(program), "--create-db")
            self.assertEqual(code, 1)
            self.assertIn("stage 2 (into)", out)

            readback = directory / "readback.unsequel"
            readback.write_text("from plain\n", encoding="utf-8")
            out, code = run_cli("run", str(readback), "--db", str(database))
            self.assertEqual(code, 1)
            self.assertIn("plain", out)

    def test_write_refreshes_an_existing_schema_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            database = directory / "warehouse.duckdb"
            snapshot = Path(f"{database}.schema.json")
            snapshot.write_text('{\n  "tables": {}\n}\n', encoding="utf-8")
            orders = (SPEC / "orders.csv").resolve()
            products = (SPEC / "products.csv").resolve()
            create = directory / "create.unsequel"
            create.write_text(
                f"# source orders: {orders}\n"
                f"# source products: {products}\n"
                "create table summary = (from orders | group by country | "
                "aggregate n = COUNT(*))\n",
                encoding="utf-8")
            out, code = run_cli("run", str(create), "--db", str(database),
                                "--create-db")
            self.assertEqual(code, 0)
            refreshed = json.loads(snapshot.read_text(encoding="utf-8"))
            # This run's temporary '# source' tables must not leak into the snapshot.
            self.assertEqual(list(refreshed["tables"]), ["summary"])
            self.assertEqual(list(refreshed["tables"]["summary"]["columns"]),
                             ["country", "n"])

    def test_parser_accepts_cast_type_parameters_and_exponents(self):
        from unsequel.errors import LocatedError
        from unsequel.grammar import parse_program

        program = parse_program(
            "from orders\nselect money = CAST(total AS DECIMAL(10,2)), cap = 1e20\n")
        select = program[1]
        money = select.items[0].expr
        cap = select.items[1].expr
        self.assertEqual(money.type_name, "DECIMAL(10,2)")
        self.assertEqual(cap.value, 1e20)

        with self.assertRaises(LocatedError) as raised:
            parse_program("from orders\nselect x = CAST(a AS DECIMAL(1.5))\n")
        self.assertIn("type parameter", str(raised.exception))

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
