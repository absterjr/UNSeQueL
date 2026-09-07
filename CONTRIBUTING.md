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

## Good first contributions

- Add a focused expression function with tests.
- Add a small example query and explain its execution stages.
- Improve parser error messages.
- Add a data source adapter without changing the language syntax.

Please include tests and update the documentation when changing syntax or
execution behavior.
