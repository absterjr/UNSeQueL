# Compiler examples

All files here use the **PLQ dialect** (`.plq`): `filter`/`having`, split
`group by` + `aggregate`, implemented `window`. The Python prototype's
pipeline examples use the older `.pusql` dialect and live in
[`../../examples/`](../../examples/) — do not mix them.

| Path | What it is |
| --- | --- |
| `reference/q01..q21` | the frozen reference corpus: the behavioural baseline for lexer, parser, analyzer, and codegen tests |
| `coverage/c01..c13` | parser fixtures for grammar features the reference set misses (window, aliases, DISTINCT aggregates, NOT variants, CAST, …); feature map in [coverage/README.md](coverage/README.md) |
| `real/r01..r05` | the five dogfooding queries migrated through the CLI; two use the `sql` hatch, recorded in [../docs/backlog.md](../docs/backlog.md) |

Run any of them with the CLI, for example:

```bash
cd plq
npm run build
node dist/main.js run examples/real/r01_top_products.plq \
  --schema ../examples/spec/schema.json \
  --data orders=../examples/spec/orders.csv \
  --data products=../examples/spec/products.csv
```
