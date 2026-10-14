# Compiler examples

All files here use the **PLQ dialect** (`.plq`): `filter`/`having`, split
`group by` + `aggregate`, implemented `window`. The Python prototype's
pipeline examples use the older `.pusql` dialect and live in
[`../../examples/`](../../examples/) — do not mix them.

| Path | What it is |
| --- | --- |
| `reference/q01..q21` | the frozen reference corpus: the behavioural baseline for lexer, parser, analyzer, and codegen tests |
| `coverage/c01..c13` | parser fixtures for grammar features the reference set misses (window, aliases, DISTINCT aggregates, NOT variants, CAST, …); feature map in [coverage/README.md](coverage/README.md) |
| `real/r01..r05` | the five dogfooding queries migrated through the CLI; they exercise composable joins, multi-stage grouping, native date functions, and `over ()` |
| `spec/*.csv` | packaged copies of the shared sample dataset, so the real queries also run from the npm package |

The five `real/` queries carry their own `# source` directives, so once `plq`
is on PATH they need no flags:

```bash
cd plq
npm install -g .        # once
plq run examples/real/r01_top_products.plq
plq run examples/real/r05_share_of_total.plq --format json
```

The `reference/` and `coverage/` corpora are parse/codegen fixtures and are
normally driven with explicit flags:

```bash
plq preview examples/reference/q20_full_pipeline.plq --stage 5 --limit 3 \
  --data orders=examples/spec/orders.csv \
  --data products=examples/spec/products.csv
```
