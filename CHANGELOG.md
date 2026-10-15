# Changelog

## Unreleased

- Convergence, continued: the Python package now carries the full `.unsequel`
  pipeline — a schema-aware analyzer (`unsequel/analyzer.py`), a DuckDB SQL
  generator (`unsequel/sqlgen.py`), and CLI wiring. `python -m unsequel run
  query.unsequel` parses, checks, compiles, and executes on DuckDB; `check`,
  `compile --stage`, and `preview --stage` support `.unsequel` files; `# source`
  and `# schema` directives work, and `--data` sources are introspected when
  no schema is given. Generated SQL is byte-identical to the compiler's
  golden files for all 21 reference queries, and DuckDB execution matches the
  hand-written SQL. The packaged standalone executable now runs `.unsequel`
  too.
- Standalone distribution: `packaging/unsequel.spec` builds a one-file
  executable that bundles Python, the `unsequel` package, and DuckDB
  (verified on Windows with both the memory and DuckDB engines), and the new
  `Standalone binaries` workflow builds Windows/Linux/macOS assets and
  attaches them to releases. The packaged CLI runs the current Python
  surfaces; `.unsequel` execution arrives with the grammar port.
- Convergence: the Python package now hosts a `.unsequel` grammar v1.3
  front-end (`unsequel/grammar.py`, a port of the compiler's lexer/AST/parser).
  It parses all 21 reference, 16 coverage, and 5 dogfood `.unsequel` queries
  with the same stage sequences and error messages as the TypeScript
  compiler. Analyzer, DuckDB codegen, and CLI wiring come next.
- Duplicate output names in `SELECT` are rejected everywhere now: the ordered
  `.usql` parser and engine raise `SELECT projects 'x' more than once; add an
  alias to one of them` (the compiler already did), and the `.pusql` select
  check is case-insensitive. Previously the later column silently replaced the
  earlier one in the Python engine.
- Division semantics are documented in `docs/semantics.md`,
  `compiler/docs/semantics.md`, and the site: `/` follows DuckDB and returns a
  number (`7 / 2` is `3.5`); PostgreSQL/Redshift/SQLite truncate integer
  division instead.

## 0.1.0 — 2026-10-15

First release. The compiler (npm), the Python prototype, and the VS Code
extension all start at 0.1.0; this entry summarizes everything that ships.

### Compiler (npm `unsequel`, TypeScript, DuckDB target)

- FROM-first stage language: one stage per line, read top to bottom in the
  order the data is processed. Stages compose and repeat (`join` after
  grouping, `filter` after `select`, a second `sort` after `take`).
- Stages: `with` (named CTEs), `from` (tables, CTEs, aliased subqueries),
  `join` (inner, left, right, full, cross), `filter`, `derive`,
  `group by` + `aggregate`, `having`, `window` (with and without
  `partition by`), `select` (`select *`, `name = expr`), `sort`/`skip`/
  `take`/`distinct`, set operators (`union`, `union all`, `intersect`,
  `except`), and the `sql` escape hatch (input relation is `__input__`).
- Grammar v1.3 nested programs: `with name = (...)`, subquery sources
  (`from (...) as x`, `join (...) as y on ...`), and set operators lowered
  to prefixed CTE blocks; nested stages write inline with `|` or across
  lines inside parentheses.
- Expression language: arithmetic, comparison, and logical operators;
  `CASE`, `CAST`, `IN`, `BETWEEN`, concatenation, `LIKE` (case-sensitive);
  aggregates (`COUNT(*)`, `COUNT(DISTINCT x)`, `SUM`, `AVG`, `MIN`, `MAX`);
  window functions (`ROW_NUMBER`, `RANK`, `DENSE_RANK`, `LAG`, `LEAD`,
  aggregates over windows); scalar functions including `strftime` and
  `date_trunc`; backtick-quoted identifiers.
- Strict semantics: non-grouped columns and ambiguous bare join references
  are errors, duplicate output names and CSV headers are rejected, numeric
  operations and comparisons are type-checked, `ORDER BY` ordinals are
  supported, and sort order survives later row-preserving stages through
  top-N (removed from the visible output).
- CSV rules: whole-column type inference preserves leading zeros and
  literal `null`/`none`/`nan` text, blanks become NULL, and `--delimiter` /
  `--column-type` override inference.
- SQL-like workflow: self-contained query files declare their connection
  with `# source name: path` and `# schema: path` directives; `run`,
  `compile`, `check`, `preview --stage N`, `fmt --write|--check`, and an
  interactive `repl` (queries execute on a blank line or `;`, dot commands,
  `.run`); `run -` reads from stdin and `--lines A:B` runs a selection while
  keeping the file's directives; JSON/CSV/table output.
- Verification: 207 tests including golden SQL files, DuckDB
  execution-parity tests, a dogfood corpus of real analyst queries, and
  formatter idempotency checks; CI covers Node 22/24 on Linux and Windows.

### Python prototype (PyPI `unsequel`, dependency-free core)

- The original implementation with two older surfaces: ordered `.usql` and
  pipeline `.pusql` syntax; new users should start with the compiler.
- In-memory relational executor plus an optional DuckDB engine
  (`--engine duckdb`) and a stdlib SQLite adapter behind the `sql` hatch.
- The same strictness hardening as the compiler: grouped-column
  enforcement, ambiguous-join errors, `ORDER BY` ordinals, mixed-type
  numeric errors, duplicate CSV headers, case-sensitive `LIKE`,
  column-wide CSV inference with leading-zero preservation, and
  `--delimiter`/`--column-type` overrides.
- SQL codegen with golden files and DuckDB execution-parity tests;
  conformance probes keep the memory and DuckDB engines aligned.
- 171 tests, including the site checks in `tests/test_site.py`.

### VS Code extension (unsequel-vscode 0.1.0)

- Syntax highlighting for `.unsequel` files, `UNSeQueL: Run Selection`
  (selected lines run via `--lines`), `UNSeQueL: Run File`, and
  `UNSeQueL: Open REPL`.

### Documentation

- Multi-page documentation at <https://absterjr.github.io/UNSeQueL/docs/>:
  getting started, language guide, expressions and functions, CLI
  reference, semantics and data rules, worked examples, VS Code, Python
  prototype, comparison and scope, and a roadmap — every page has sidebar
  navigation and in-page sections.
- Grammar v1.3 and semantics specifications in the repository; the
  learning-benefit claim is explicitly recorded as untested in
  `docs/learning-validation.md` until external evidence exists.
