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
| 4–9 | pending |

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

## Development

```bash
cd plq
npm install
npm run check   # tsc for src and tests
npm test        # vitest
npm run build   # dist/main.js
node dist/main.js --version
```
