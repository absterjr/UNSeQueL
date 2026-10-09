# Changelog

## Unreleased

- `plq` Step 5 (schema-aware validation): `src/schema.ts` loads JSON schemas
  and collapses type strings into families; `src/semantics.ts` (`analyze`)
  walks a program tracking live columns per stage with join label defaults and
  collision renaming, the group boundary, window additions, `select *`
  pass-through, and `sql` opacity (`<raw sql>` / `<opaque>`), raising
  stage-located `SemanticError`s and returning per-stage lineage. New
  `SchemaError`/`SemanticError`; all 21 reference and 13 coverage fixtures
  analyze (the latter against an extended test schema); invalid programs are
  located. CLI exposure lands with the codegen/preview steps.
- Prototype parity: schema analysis resolves `derive` items left-to-right, so
  an item may reference an earlier item in the same stage (matching both
  engines).
- Scaffolded the production compiler (`plq/`): TypeScript/Node package with a
  strict `tsc` setup, vitest runner, an empty `plq` CLI (`--version`, `--help`,
  usage exit), and a Node 22/24 CI job. Plan Step 1 of the TypeScript stack
  decision (DuckDB target, recursive descent, one CTE per stage).
- `plq` Step 2: frozen language specification v1.0 (`plq/docs/grammar.md`) and
  the 21 reference queries converted to `.plq` syntax in
  `plq/examples/reference/`, hand-validated against the grammar.
- `plq` Step 3: the lexer (`plq/src/lexer.ts`) — case-insensitive keywords,
  single-line strings with escapes, `#`/`--` comments, CRLF-safe line/column
  tracking, and token-level errors. All 21 reference queries tokenize; 17
  lexer tests including five malformed-input cases.
- `plq` grammar v1.1 (audit batch B1): lowering-unit contract for
  `group by` + `aggregate` (one CTE per unit; preview numbering defined),
  mandatory `aggregate`, completed window EBNF, `select`/`not` binding rules,
  single comparison tail, aggregate naming/DISTINCT rules, the complete
  reserved-word table, number and separator semantics, and alias defaults.
- Prototype refinement (audit P-Fix4–6/8): the formatter escapes newlines in
  string literals (`\n`/`\r`, now documented in spec §2); group keys may
  contain parenthesized and call expressions (the aggregate-list parentheses
  are matched by balance, not by the first `(`); schema analysis uses the
  shared `expression_name` rule (e.g. `sort in` after a computed select); the
  `sql` hatch accepts `WITH` queries and leading comments and rejects `;` only
  outside string literals and comments; `sort` after a `sql` segment is
  documented as per-segment in spec §4.
- PLQ refinement (audit L-Doc/L-Tests): grammar records the prefix-`not` AST
  shape, window-after-tail behavior, and the `sql` comment/`WITH` rules;
  `skip`/`take` beyond the safe-integer range are rejected; parser regression
  tests cover sql resets, same-line spans, comment-only lines, `select *`,
  zero counts, CRLF, and `WITH RECURSIVE`.
- Prototype refinement (audit P-Fix1–3): aggregate calls are rejected anywhere
  in `select` (including inside `COALESCE`/`CASE`/unary/`IN`); duplicate output
  names are now errors — duplicate select items, derive redefinition within or
  across stages, group key/aggregate collisions and group-phase derive
  shadowing — and codegen plus schema analysis reject a derive name that
  collides with a live column; `__input__` substitution respects SQL comments
  (line and block) and quoted identifiers.
- PLQ refinement (audit L-Fix1–2): `left join` now obeys the same ordering
  checks as `join` (after `group by`, after the tail); empty stage bodies
  report the stage's own line instead of 1:1.
- `plq` parser (audit batch B6 / plan step 4): `src/parser.ts` — hand-written
  recursive descent over the stage grammar with additive/comparison/`BETWEEN`
  binding, `select x = y` aliasing, prefix `not` normalization, single-tail
  comparison enforcement, all §4 ordering rules as stage-located
  `PipelineError`s (stage ordinal, keyword, line, column), and stage splitting
  on newline/`|` at depth zero. All 21 reference queries and 13 coverage
  fixtures parse; 38 parser tests.
- `plq` corpus and hygiene (audit batch B4–B5): `examples/coverage/` adds 13
  parser fixtures for the grammar features the reference set misses (window,
  aliases, named group keys, DISTINCT aggregates, `select *`, NOT variants,
  CAST, scalar functions, pipes, comments); `package.json` `bin` now points at
  `dist/main.js`; `npm run check` type-checks tests via `tsconfig.test.json`;
  a test guards `VERSION`/`package.json` drift; engines pinned to Node ≥ 22.
- `plq` contracts (audit batch B3): `src/ast.ts` defines the stage and
  expression node types with source spans, plus the grammar-stage → CTE
  lowering-unit map from grammar §1.1; `src/errors.ts` gains `ParseError` and
  the stage-located `PipelineError`.
- `plq` lexer alignment (audit batch B2): tokens carry exclusive end positions
  (spans); `LexError` extends the shared `PlqError` base
  (`plq/src/errors.ts`); dangling-backslash input gets an explicit error; 11
  edge tests cover lone CR, tabs, number forms, `a--b` comments,
  reserved-word collisions, and function-name identifiers.
- Prototype refinement (audit batch A5–A9): pipeline grammar rejects subquery
  sources with a directed message; the `sql` hatch requires a `SELECT`/`WITH`
  opening and substitutes `__input__` only outside string literals; codegen and
  the memory engine share one column-naming rule and codegen quotes reserved
  identifiers (`AS "case"`); schema analysis resolves qualified names against
  the table that owns them; doc drift swept (README status/roadmap, spec
  wording, backlog item).
- Prototype refinement (audit batch A1–A4): derive-only pipelines now keep
  source columns and append derived ones; the engine supports `SELECT *, expr`;
  ordering rules are enforced (join after group, nothing row-changing after the
  sort/skip/take tail, `sort` once, `skip` before `take`); aggregate placement
  checks (calls only in `group`, no nested aggregates, `COUNT(*)` only, no
  `COUNT(DISTINCT *)`); formatter round-trips (`name = expr` in `derive`,
  escaped newlines in `sql` payloads, `IS NOT` precedence). Spec §4 updated to
  v0.3.1.
- Prototype refinement (audit P-Fix7): codegen resolves qualified names
  strictly — an aliased table's original name is rejected instead of silently
  collapsing to the left side, left labels render correctly in join
  conditions, columns after a `sql` stage stay literal, and `join` after a
  hatch is rejected at parse time (spec §3.13). Doc sweep: spec/README bumped
  to v0.3.1, version strings in error texts removed, test names no longer say
  "twenty", backlog hatch notes refreshed.

## 0.4.0 - 2026-09-09

Pipeline language, steps 1-9 of the implementation plan.

- Added the pipeline language specification (`docs/pipeline-spec.md`, now
  v0.3): every stage formally defined with its EBNF, IR node, and
  one-CTE-per-stage DuckDB lowering. Supersedes the frozen v0.1 POC note.
- Added 21 reference pipeline queries and a four-table sample dataset under
  `examples/spec/`, covering every stage and combination.
- Added a stage-preserving IR (`unsequel/pipeline_ir.py`): one typed node per
  stage with its source line. Pipeline parse errors are stage-located. Fixed a
  row-derive shadowing a group aggregate.
- Added schema-aware validation: `unsequel/schema.py` (JSON schema files) and
  `unsequel/semantics.py` (`analyze`), via `check --schema` / `run --schema`.
  Catches unknown tables/columns, dead-after-group references, and non-numeric
  `SUM`/`AVG` before execution; returns per-stage column lineage.
- Added DuckDB SQL codegen (`unsequel/codegen.py`): `emit_sql` lowers the stage
  IR to one CTE per stage, with golden files and execution-parity tests against
  hand-written SQL.
- Added `unsequel compile`, `unsequel preview` (run a pipeline truncated at a
  stage and show a sample), and `unsequel run --engine duckdb`. New optional
  `duckdb` extra; the core stays dependency-free.
- Added the `sql "..."` escape hatch (spec §3.10): previous relation is
  `__input__`; runs on stdlib SQLite with the memory engine and as its own CTE
  with the DuckDB engine. Reference query `q21_sql_hatch` covers window
  functions through the hatch.
- Added `unsequel fmt` (`--write`, `--check`): canonical, idempotent pipeline
  formatting (spec §9).
- Dogfooded all examples through the formatter; added the prioritised gap list
  [docs/backlog.md](docs/backlog.md) and the external-feedback exercise
  [docs/first-task.md](docs/first-task.md).

## 0.3.0 - 2026-09-07

- Added the pipeline-syntax POC (frozen grammar v0.1 in `docs/poc-syntax.md`).
- Pipeline queries lower onto the existing ordered-clause engine; no new
  execution semantics.
- Added `--pipeline` to `unsequel run` and `unsequel check`.
- Added six hand-translated pipeline examples and 7 pipeline tests.
- Added `==` to the lexer for LINQ-style equality.

## 0.2.0 - 2026-09-07

- Expanded the MVP into a complete read/query language core.
- Added CTEs, nested `FROM` sources, `DISTINCT`, `OFFSET`, and set operations.
- Added `RIGHT JOIN`, `FULL JOIN`, `CROSS JOIN`, and qualified null handling.
- Added `CASE`, `CAST`, `IN`, `BETWEEN`, concatenation, distinct aggregates,
  and additional scalar functions.
- Added a dependency-free SQLite table/view adapter.
- Added GitHub Actions tests and wheel builds.

## 0.1.0 - 2026-09-07

- Initial UNSeQueL language and command-line runner.
- Ordered clauses: `FROM`, `JOIN`, `WHERE`, `GROUP BY`, `HAVING`, `SELECT`,
  `ORDER BY`, and `LIMIT`.
- CSV and JSON input with no runtime dependencies.
- Aggregates, aliases, expressions, and basic joins.
