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

## TypeScript compiler (`plq/`)

The production compiler has its own development loop:

```bash
cd plq
npm install
npm run check   # tsc for src and tests
npm test        # vitest, including DuckDB execution parity
npm run build
```

Its grammar is [plq/docs/grammar.md](plq/docs/grammar.md) (frozen v1.1); the
reference corpus in `plq/examples/reference/` is the behavioural baseline, and
[plq/docs/backlog.md](plq/docs/backlog.md) lists the prioritised gaps.

## Good first contributions

- Add a focused expression function with tests.
- Add a small example query and explain its execution stages.
- Improve parser error messages.
- Add a data source adapter without changing the language syntax.

Please include tests and update the documentation when changing syntax or
execution behavior.
