# First task (external feedback)

This exercise targets the TypeScript compiler. For the Python prototype, see
[../../docs/first-task.md](../../docs/first-task.md).

A 20-minute exercise that uses only the public documentation. Please do not
read the source code. Record where you get stuck — that is the feedback.

## Setup

Requires Node 22 or newer.

```bash
git clone https://github.com/absterjr/UNSeQueL.git
cd UNSeQueL/plq
npm install
npm install -g .        # puts `plq` on PATH (`npm link` also works)
plq --version
```

## Task

1. Read [README.md](../README.md) and [docs/grammar.md](grammar.md).
2. Run a real analyst query — no flags needed, the file declares its sources:

   ```bash
   plq run examples/real/r01_top_products.plq
   ```

3. Watch a pipeline move stage by stage (this fixture has no directives,
   hence the explicit `--data`):

   ```bash
   plq preview examples/reference/q20_full_pipeline.plq \
     --stage 5 --limit 3 \
     --data orders=../examples/spec/orders.csv \
     --data products=../examples/spec/products.csv
   ```

4. Write `my_query.plq` answering: *which customers spent more than 150 in
   total, highest first?* Use `from`, `group by`, `aggregate`, `having`, and
   `sort`. Start the file with a directive so it runs flagless:

   ```text
   # source orders: ../examples/spec/orders.csv
   ```

   Sample data: [../../examples/spec/orders.csv](../../examples/spec/orders.csv).
5. Format your file and confirm it is canonical:

   ```bash
   plq fmt my_query.plq --write
   plq fmt my_query.plq --check
   ```

6. Validate and run it:

   ```bash
   plq check my_query.plq
   plq run my_query.plq --format json
   ```

7. Expected: three customers — Katherine (177), Ada (162), Grace (151.5).

## Optional: editor flow

If you use VS Code, install the extension in
[../editors/vscode/README.md](../editors/vscode/README.md) (F5 is fastest),
open `my_query.plq`, and use **PLQ: Run File** — or select the query and use
**PLQ: Run Selection**.

## What to send back

- Did you reach for `filter` or `where` first?
- Was `aggregate` requiring a preceding `group by` obvious from the docs?
- Did the `stage N/total` preview help you understand the pipeline?
- Was the flagless run (the `# source` directive) clear from the README?
- What error message, if any, was unhelpful?
- What did you want to write that the language rejected?
