# Progress log

One note per push, describing what changed and why, so the history is readable
without diffing. Each note maps to a step in the Pipeline Query Language
implementation plan.

| Step | Note | Status |
| --- | --- | --- |
| 1 — Project setup | (pre-existing: package, tests, CI) | done |
| 2 — Grammar as a document | [step-02-grammar-spec.md](step-02-grammar-spec.md) | done |
| 3 — Lexer | (pre-existing: `unsequel/lexer.py`) | done |
| 4 — Parser and stage-preserving AST | [step-04-stage-ir.md](step-04-stage-ir.md) | done |
| 5 — Schema-aware semantic analysis | [step-05-semantic-analysis.md](step-05-semantic-analysis.md) | done |
| 6 — DuckDB SQL codegen (one CTE per stage) | [step-06-duckdb-codegen.md](step-06-duckdb-codegen.md) | done |
| 7 — Stage-preview CLI | [step-07-preview-cli.md](step-07-preview-cli.md) | done |
| 8 — Raw-SQL escape hatch and formatter | [step-08-sql-hatch-and-formatter.md](step-08-sql-hatch-and-formatter.md) | done |
| 9 — Dogfood and outside feedback | [step-09-dogfood.md](step-09-dogfood.md) | done |
