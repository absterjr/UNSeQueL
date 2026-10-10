# First task (external feedback)

A 20-minute exercise that uses only the public documentation. Please do not
read the source code. Record where you get stuck — that is the feedback.

## Setup

Requires Node 22 or newer.

```bash
git clone https://github.com/absterjr/UNSeQueL.git
cd UNSeQueL/plq
npm install
npm run build
```

## Task

1. Read [README.md](../README.md) and [docs/grammar.md](grammar.md).
2. Watch a pipeline move stage by stage:

   ```bash
   node dist/main.js preview examples/reference/q20_full_pipeline.plq \
     --schema ../examples/spec/schema.json --stage 5 --limit 3 \
     --data orders=../examples/spec/orders.csv \
     --data products=../examples/spec/products.csv
   ```

3. Write `my_query.plq` answering: *which customers spent more than 150 in
   total, highest first?* Use `from`, `group by`, `aggregate`, `having`, and
   `sort`. Sample data: [../examples/spec/orders.csv](../examples/spec/orders.csv).
4. Format your file and confirm it is canonical:

   ```bash
   node dist/main.js fmt my_query.plq --write
   node dist/main.js fmt my_query.plq --check
   ```

5. Validate and run it:

   ```bash
   node dist/main.js check my_query.plq --schema ../examples/spec/schema.json
   node dist/main.js run my_query.plq --schema ../examples/spec/schema.json \
     --format json --data orders=../examples/spec/orders.csv
   ```

6. Expected: three customers — Katherine (177), Ada (162), Grace (151.5).

## What to send back

- Did you reach for `filter` or `where` first?
- Was `aggregate` requiring a preceding `group by` obvious from the docs?
- Did the `stage N/total` preview help you understand the pipeline?
- What error message, if any, was unhelpful?
- What did you want to write that the language rejected?
