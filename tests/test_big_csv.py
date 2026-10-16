"""Regression: the Python DuckDB path must not read whole CSVs into Python.

An earlier version typed CSV columns with a row-by-row Python sniffer, so a
100 MB input needed over a gigabyte of Python objects and could kill the
process (the TypeScript track never did). Typing now goes through DuckDB's own
whole-file sniffer; this test runs a ~100 MB input in CI and enforces a Python
memory ceiling with tracemalloc, which would have caught the old behavior.
"""

import contextlib
import io
import os
import tempfile
import tracemalloc
import unittest
from pathlib import Path

from unsequel.cli import main

try:
    import duckdb  # noqa: F401
    HAS_DUCKDB = True
except ImportError:
    HAS_DUCKDB = False

ROWS = 1_500_000


@unittest.skipUnless(HAS_DUCKDB and os.environ.get("CI"),
                     "runs in CI (needs DuckDB and a few seconds)")
class BigCsvTests(unittest.TestCase):
    def test_large_csv_stays_within_python_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            data = directory / "big.csv"
            with data.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write("id,bucket,payload\n")
                for row in range(ROWS):
                    handle.write(f"{row},{row % 10},{'x' * 56}\n")
            self.assertGreater(data.stat().st_size, 90 * 1024 * 1024)

            query = directory / "query.unsequel"
            query.write_text(
                f"# source big: {data}\n"
                "from big\ngroup by bucket\naggregate n = COUNT(*)\nsort bucket\n",
                encoding="utf-8")

            out = io.StringIO()
            tracemalloc.start()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                main(["run", str(query)])
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()

            self.assertIn("150000", out.getvalue())
            self.assertLess(
                peak, 400 * 1024 * 1024,
                f"peak Python memory {peak / 1e6:.0f} MB suggests the whole "
                "file was read into Python objects")


if __name__ == "__main__":
    unittest.main()
