# Progress log

One note per push, describing what changed and why, so the history is readable
without diffing. Each note maps to a step in the Pipeline Query Language
implementation plan.

| Step | Note | Status |
| --- | --- | --- |
| 1 — Project setup | (pre-existing: package, tests, CI) | done |
| 2 — Grammar as a document | [step-02-grammar-spec.md](step-02-grammar-spec.md) | done |
| 3 — Lexer | (pre-existing: `unsequel/lexer.py`) | done |
| 4 — Parser and stage-preserving AST | — | next |
| 5 — Schema-aware semantic analysis | — | |
| 6 — DuckDB SQL codegen (one CTE per stage) | — | |
| 7 — Stage-preview CLI | — | |
| 8 — Raw-SQL escape hatch and formatter | — | |
| 9 — Dogfood and outside feedback | — | |
