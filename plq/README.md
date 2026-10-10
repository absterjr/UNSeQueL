# plq — pipeline query language compiler

TypeScript compiler for the pipeline query language: one transform per line,
compiled to DuckDB SQL with one CTE per stage.

This is the production implementation of the plan the team locked in:

| Decision | Locked value |
| --- | --- |
| Compiler language | TypeScript / Node.js |
| First SQL target | DuckDB |
| Parser style | hand-written recursive descent |
| Codegen | one CTE per pipeline stage |

The executable prototype lives in the Python package at the repository root;
this package is the compiler being built to the same 9-step plan.

## Plan status

| Step | Status |
| --- | --- |
| 1 — project scaffold, empty CLI + tests | done |
| 2 — grammar document (`docs/grammar.md`, v1.1 frozen) | done |
| 3 — lexer (`src/lexer.ts`) | done |
| 4 — parser and AST (`src/parser.ts`, `src/ast.ts`) | done |
| 5 — schema-aware validation (`src/schema.ts`, `src/semantics.ts`) | done |
| 6 — DuckDB codegen (`src/codegen.ts`) | done |
| 7 — stage-preview CLI (`src/cli.ts`, `src/duckdb.ts`) | done |
| 8–9 | pending |

## Try it

```bash
cd plq
npm install
npm run build

# generated SQL (truncated at grammar stage 4)
node dist/main.js compile examples/reference/q20_full_pipeline.plq \
  --schema ../examples/spec/schema.json --stage 4

# step through the pipeline: show the relation after stage 5
node dist/main.js preview examples/reference/q20_full_pipeline.plq \
  --schema ../examples/spec/schema.json --stage 5 --limit 3 \
  --data orders=../examples/spec/orders.csv \
  --data products=../examples/spec/products.csv

# execute the whole program
node dist/main.js run examples/reference/q20_full_pipeline.plq \
  --schema ../examples/spec/schema.json --format json \
  --data orders=../examples/spec/orders.csv \
  --data products=../examples/spec/products.csv
```

Step 2 ships the frozen grammar and the validated reference corpus:

- [`docs/grammar.md`](docs/grammar.md) — every stage's EBNF, IR shape, and
  DuckDB lowering, plus the hand-parse validation of all 21 queries
- [`examples/reference/`](examples/reference/) — the 21 reference programs in
  `.plq` syntax, the behavioral baseline for the lexer and parser
- [`examples/coverage/`](examples/coverage/README.md) — parser fixtures for
  grammar features the reference set does not exercise (window, aliases,
  DISTINCT aggregates, NOT variants, CAST, …)
- contract layer in place: `src/ast.ts` (stage/expr nodes with spans, the
  grammar-stage → CTE lowering-unit map) and `src/errors.ts` (`PlqError`,
  `LexError`, `ParseError`, `PipelineError`)
- SQL codegen with two-way verification: golden files in `tests/golden/` and
  DuckDB execution parity against the hand-written equivalents in
  `tests/handwritten/` (needs the `@duckdb/node-api` devDependency)

## Development

```bash
cd plq
npm install
npm run check   # tsc for src and tests
npm test        # vitest
npm run build   # dist/main.js
node dist/main.js --version
```
