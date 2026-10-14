# Changelog

## Unreleased

- Documentation site: the GitHub Pages site is now a full documentation set
  (docs/getting-started, language, expressions, CLI reference, semantics,
  worked examples, VS Code, Python prototype, comparison, roadmap) with a
  shared stylesheet, cross-page navigation, per-page metadata and canonical
  URLs, and an expanded sitemap. Site tests now check every page's metadata,
  that local links resolve, and that the sitemap lists each page. Every docs
  page also has a W3Schools-style left sidebar listing all documentation pages
  plus in-page section anchors; site tests check the sidebar marks the current
  page and that every anchor target exists.
- Nested programs (grammar v1.3, compiler 0.4.0): `with name = (...)` CTE
  definitions, aliased subquery sources (`from (...) as x`,
  `join (...) as y on ...`), and set operators (`union`, `union all`,
  `intersect`, `except`) with arity checking; nested stages are written
  inline with `|` or across lines inside parentheses and lower to prefixed
  CTE blocks; the formatter round-trips them on one line. Golden/parity and
  corpus coverage extended (3 new fixtures; compiler suite 207 tests).
  A second SQL target is deferred pending external feedback.
- Naming migration: the production TypeScript package, executable, and VS Code
  extension all use the full `unsequel` / `UNSeQueL` name; the canonical query
  suffix is `.unsequel` (converted from `.plq` throughout the corpus, examples,
  tests, and docs). The compiler folder in the repository is now `compiler/`
  (was `plq/`). The Python package remains `unsequel`, with `unsequel-python`
  as its console script and `python -m unsequel` unchanged. The v0.6.0 release
  assets keep their original names; the follow-up release uses the full brand.

## 0.6.0 (Python) / plq 0.2.0 — 2026-10-14

- External-audit hardening: the Python engine now rejects non-grouped columns,
  ambiguous bare join references, mixed-type numeric operations/sorts, and
  duplicate CSV headers; `ORDER BY` ordinals, empty results, NULL rendering,
  case-sensitive LIKE, column-wide CSV inference, leading-zero preservation,
  `--delimiter`, and `--column-type` are covered by regressions. The CLI test
  that reads stdin no longer blocks. Added Python memory/DuckDB conformance
  probes and `docs/semantics.md`.
- PLQ grammar v1.2: stages compose after projection/group/pagination; repeated
  group/aggregate units, right/full/cross joins, backtick identifiers,
  `select *, expression = ...`, `SUM(x) over ()`, and native `strftime` /
  `date_trunc` are implemented. Sort order now survives later row-preserving
  stages and top-N. CSV loading scans columns before DuckDB inference, with
  delimiter/type overrides and duplicate-header checks. PLQ test suite: 199.
- Release metadata: Python package `unsequel` is 0.6.0; npm package
  `unsequel-plq` is 0.2.0 (the executable remains `plq`). The Python suite has
  165 passing tests.
- Positioning/discovery: README now names the prototype as a prototype, makes
  the learning claim explicitly untested, documents the tradeoffs against
  DuckDB FROM-first, PRQL, Malloy, Kusto, BigQuery, Spark, and Databricks, and
  clarifies the window phase. Added a static Pages landing site, sitemap,
  robots.txt, social-preview artwork/source, a Pages workflow, a blog draft,
  and a Search Console/Bing/inbound-link checklist. Repo description/topics
  updated; npm package name changed to `unsequel-plq` because `plq` is already
  owned by another publisher (the executable remains `plq`).
- Documentation pass: every doc now describes the installed-`plq` workflow —
  the root README compiler section leads with flagless `plq run`/`--lines`/
  `repl`; `compiler/docs/first-task.md` is a flagless exercise (with the `# source`
  directive and an optional VS Code step); `compiler/examples/README.md` splits
  self-contained `real/` from fixture corpora; `compiler/docs/backlog.md` records
  shipped vs open tooling gaps; `CONTRIBUTING.md` covers
  `fmt:check`/`editors:check` and the extension folder; the progress log
  points at the post-step-9 work.
- Editor experience: the five dogfood queries in `compiler/examples/real/` now
  carry `# source` directives and run with zero flags; `compiler/editors/vscode/`
  ships a VS Code extension — syntax highlighting, `PLQ: Run Selection`
  (piped as `plq run <file> --lines A:B`), `PLQ: Run File`, `PLQ: Open REPL` —
  validated by a new `npm run editors:check` CI step; the compiler README now
  leads with the installed `plq` command ("run queries like SQL") and moves
  `node dist/main.js` into the development section.
- Editor-selection fix: `--lines A:B` now keeps the file's `# source`/`# schema`
  directives even when the selection excludes them, so running a selected
  query needs no flags (the file is the connection; the selection is the
  query). The REPL gains `.run` to execute the query being typed, a `...>`
  continuation prompt while a query is open, a warning when `.quit`
  discards pending lines, and IPython-style stripping of pasted `plq>`/`...>`
  prompt prefixes so example transcripts can be copied wholesale. Dev
  dependencies bumped to vitest 5 (`npm audit` clean).
- SQL-like execution model for the compiler: `.plq` files can declare their
  own connection with `# source name: path` and `# schema: path` directives
  (paths relative to the file; `--data`/`--schema` override); `plq run -`
  reads the query from stdin and `--lines A:B` runs a selected range; without
  a schema file, loaded sources are introspected so `run`, `compile`, and
  `preview` work flagless; new interactive `repl` (a query executes on a
  blank line or `;`; dot commands `.load`/`.sources`/`.schema`/`.format`/
  `.clear`/`.quit`), opened by `plq repl` or a bare `plq` on a terminal; the
  formatter preserves file directives. `npm link` verified so `plq` is a
  first-class command. Tests cover directives, selection ranges,
  introspection, and the session engine.
- R3 coherence batch: a repository map table names the three dialects
  (`.usql` ordered, `.pusql` prototype pipeline, `.plq` compiler pipeline) and
  the compiler grammar §2 delta table covers window/`$`/newline/ordered-surface
  differences; the Python CLI infers pipeline mode from the `.pusql` extension
  and hints at `--pipeline` when an ordered parse fails; pipeline-style
  subquery attempts get a directed message; cross-engine divergences
  (`ORDER BY` ties, division-by-zero, float rendering) are documented in the
  README; stale docs fixed (`language-reference` version, pipeline-spec §3.1
  EBNF and §5 illustrative note, poc-syntax pointer, progress note, plq
  first-task link); examples READMEs added; the compiler CI gains a formatter
  check, a dist smoke run, and a Windows leg.
- `plq` R2 robustness batch: DuckDB failures surface as clean `error:` lines
  (no raw stacks); unknown per-command options are rejected; duplicate or
  dotted `--data` names are rejected with guidance; `compile --stage`
  validates its range like `preview`; UTF-8 BOMs are stripped from query and
  schema files; CRLF input counts as canonical for `fmt --check`;
  `.gitattributes` pins LF so fresh Windows clones pass the formatter and
  golden tests. Packaging: `npm pack` now ships LICENSE, docs, and examples;
  `package.json` gains license/repository/keywords/main/types and a prepack
  build; the Python package gains classifiers, keywords, and project URLs.
- `plq` R1 correctness batch: same-stage `derive` chains now compile (earlier
  items are inlined into later ones); window/aggregate/join/select/derive name
  collisions are stage-located errors across parser, analyzer, and codegen;
  `join` after a `sql` hatch is rejected at parse time (grammar §3.13); the
  formatter keeps `--` sequences lexable (`-(-x)`, negated sort keys);
  `__input__` substitution gained word boundaries; unknown scalar functions
  are rejected at parse time; `sql` leading comments are accepted per the
  grammar; coverage-corpus codegen, check⇒compile agreement, and a same-stage
  derive DuckDB execution test were added.

## 0.5.0 - 2026-10-10

TypeScript compiler (`compiler/`) 0.1.0: all nine plan steps complete.
Python prototype 0.5.0: audit refinements A1–A9 / P-Fix1–8, spec v0.3.1.

- `plq` Step 9 (dogfood + external feedback): five real analyst queries in
  `compiler/examples/real/` migrated through the CLI — two needed the `sql` hatch
  (month bucketing, share-of-total), which drove the prioritised gaps in
  `compiler/docs/backlog.md`; `compiler/docs/first-task.md` is the external-feedback
  exercise; a dogfood test parses, validates, formats, and executes all five
  against the sample dataset. Dogfooding found and fixed a real bug: the
  hatch's opaque column tracking is now preserved through following
  `derive`/`window` stages. The TypeScript compiler now covers all nine plan
  steps.
- `plq` Step 8 (sql hatch + formatter): the hatch was already implemented;
  this step adds the canonical formatter `src/format.ts` (`formatProgram`,
  `isFormatted`) and the `fmt [--write|--check]` CLI command. Lowercase stage
  keywords, uppercase expression keywords, minimal parentheses,
  single-quoted strings, `-column` descending, one-line `sql` payloads,
  trailing newline; comments are dropped. All 21 reference queries are
  canonical; comments in coverage fixtures make them the idempotency case.
- `plq` Step 7 (stage-preview CLI): `check`, `compile [--stage N]`, `preview
  --stage N [--limit K]` and `run` execute real DuckDB queries against
  `--data name=path` sources (CSV/JSON/Parquet). `preview` runs the program
  truncated at any grammar stage and labels the sample (`stage N/total
  (keyword)`), the plan's standout feature. DuckDB moved from a devDependency
  to a runtime dependency; the CLI is async (`runCli` returns a promise). New
  `src/duckdb.ts` backend and table/JSON/CSV output formatting.
- `plq` Step 6 (DuckDB codegen): `src/codegen.ts` lowers a program to a `WITH`
  chain with one CTE per lowering unit (`group by` + `aggregate` share one),
  with `stopAt` truncation that keeps the unit containing a grammar stage.
  Shared output-naming moved to `src/ast.ts` (`expressionName`). Verified
  two ways: 21 golden files (`compiler/tests/golden/`) and DuckDB execution parity
  against the hand-written equivalents (`compiler/tests/handwritten/`, run with
  the `@duckdb/node-api` devDependency).
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
- Scaffolded the production compiler (`compiler/`): TypeScript/Node package with a
  strict `tsc` setup, vitest runner, an empty `plq` CLI (`--version`, `--help`,
  usage exit), and a Node 22/24 CI job. Plan Step 1 of the TypeScript stack
  decision (DuckDB target, recursive descent, one CTE per stage).
- `plq` Step 2: frozen language specification v1.0 (`compiler/docs/grammar.md`) and
  the 21 reference queries converted to `.plq` syntax in
  `compiler/examples/reference/`, hand-validated against the grammar.
- `plq` Step 3: the lexer (`compiler/src/lexer.ts`) — case-insensitive keywords,
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
  (`compiler/src/errors.ts`); dangling-backslash input gets an explicit error; 11
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
