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
| 2 — grammar document | next |
| 3-9 | pending |

## Development

```bash
cd plq
npm install
npm run check   # tsc --noEmit
npm test        # vitest
npm run build   # dist/cli.js
node dist/main.js --version
```
