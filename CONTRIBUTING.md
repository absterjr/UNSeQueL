# Contributing to UNSeQueL

UNSeQueL is intentionally small and beginner-friendly. Contributions should
keep the language readable, dependency-light, and explainable from first
principles.

## Development setup

```bash
python -m venv .venv
python -m pip install -e .
python -m unittest discover -s tests -v
```

## TypeScript compiler (`compiler/`)

The production compiler has its own development loop:

```bash
cd compiler
npm install
npm run check         # tsc for src and tests
npm test              # vitest, including DuckDB execution parity
npm run fmt:check     # formatter fixtures stay canonical
npm run editors:check # VS Code extension manifest and grammar regexes
npm run build         # dist/main.js
```

The VS Code extension lives in `compiler/editors/vscode/`: open that folder and
press F5 to try it, or `npx @vscode/vsce package` for a VSIX. Its grammar is
[compiler/docs/grammar.md](compiler/docs/grammar.md) (frozen v1.2); the reference corpus
in `compiler/examples/reference/` is the behavioural baseline, and
[compiler/docs/backlog.md](compiler/docs/backlog.md) lists the prioritised gaps.

## Good first contributions

- Add a focused expression function with tests.
- Add a small example query and explain its execution stages.
- Improve parser error messages.
- Add a data source adapter without changing the language syntax.

Please include tests and update the documentation when changing syntax or
execution behavior.
