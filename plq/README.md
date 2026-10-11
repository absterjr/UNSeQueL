# plq — pipeline query language compiler

Write a query in the order it executes — one transform per line — and run it
against CSV/Parquet/JSON files with one command:

```text
# source orders: ../spec/orders.csv
from orders
group by country
aggregate revenue = SUM(quantity * unit_price)
sort -revenue
```

```bash
plq run my_query.plq
```

Plans are compiled to DuckDB SQL with one CTE per pipeline unit.

## Install

From this directory (the npm package is not published yet):

```bash
npm install -g .        # puts `plq` on PATH
plq --version
```

`npm link` works the same way for development.

## Run queries like SQL

`.plq` files declare their own connection with `# source name: path`
directives (relative to the file), so no flags are needed:

```bash
plq run examples/real/r01_top_products.plq              # join + group + top-N
plq run examples/real/r03_repeat_customers.plq --format json
plq run examples/real/r01_top_products.plq --lines 4:10 # run just that range
echo "from orders | take 1" | plq run - \
  --data orders=../examples/spec/orders.csv             # query from stdin
plq repl                                                # interactive session
```

Without a schema file, loaded tables are introspected, so `run`, `compile`,
and `preview` work flagless. `--data`/`--schema` override directives when you
need them. `--lines A:B` selects one query out of a multi-query file; the
file's directives always apply, even when the range excludes them.

Inside `plq repl`: one stage per line; a query runs on a blank line, a lone
`;`, or `.run`. Dot commands:
`.load name=path`, `.sources`, `.schema [path]`, `.format table|json|csv`,
`.clear`, `.quit`. Pasted `plq>`/`...>` prompt prefixes are stripped, so
example transcripts can be copied wholesale.

## Use it from your editor

`plq/editors/vscode/` ships a VS Code extension (syntax highlighting plus
`PLQ: Run Selection`, `PLQ: Run File`, `PLQ: Open REPL`). Install it with F5
in that folder or from a VSIX — see
[editors/vscode/README.md](editors/vscode/README.md). Run Selection executes
`plq run <file> --lines A:B`, so select a query, run it, done.

In any editor that can send text to a terminal: open `plq repl` and use
"Run Selected Text in Active Terminal"; the selected stages execute on a
blank line.

## Locked decisions

| Decision | Locked value |
| --- | --- |
| Compiler language | TypeScript / Node.js |
| First SQL target | DuckDB |
| Parser style | hand-written recursive descent |
| Codegen | one CTE per pipeline stage |

The executable prototype lives in the Python package at the repository root;
this package is the production compiler. It implements an **evolved dialect**
of the prototype's pipeline surface — `filter`/`having` instead of
position-sensitive `where`, split `group by` + `aggregate`, and an implemented
`window` stage. The exact deltas are in [docs/grammar.md](docs/grammar.md) §2;
do not copy `.pusql` examples into `.plq` files or vice versa.

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
| 8 — sql hatch + canonical formatter (`src/format.ts`) | done |
| 9 — dogfood + external feedback (`examples/real/`, `docs/`) | done |

All nine plan steps are complete; see the repository changelog for release
notes and [docs/backlog.md](docs/backlog.md) for what comes next.

## What ships

- [`docs/grammar.md`](docs/grammar.md) — every stage's EBNF, IR shape, and
  DuckDB lowering, plus the hand-parse validation of all 21 queries
- [`examples/reference/`](examples/reference/) — the 21 reference programs in
  `.plq` syntax, the behavioral baseline for the lexer and parser
- [`examples/coverage/`](examples/coverage/README.md) — parser fixtures for
  grammar features the reference set does not exercise (window, aliases,
  DISTINCT aggregates, NOT variants, CAST, …)
- SQL codegen with two-way verification: golden files in `tests/golden/` and
  DuckDB execution parity against the hand-written equivalents in
  `tests/handwritten/`
- dogfooding: five real analyst queries in [`examples/real/`](examples/real/),
  the prioritised gaps they found in [docs/backlog.md](docs/backlog.md), and an
  external-feedback exercise in [docs/first-task.md](docs/first-task.md)

## Development

```bash
npm install
npm run check         # tsc for src and tests
npm test              # vitest
npm run build         # dist/main.js
npm run fmt:check     # formatter fixtures stay canonical
npm run editors:check # VS Code extension JSON + regexes
npm run smoke         # node dist/main.js run examples/real/r01_top_products.plq
```
