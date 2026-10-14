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
npm install -g .        # puts `unsequel` on PATH (`npm link` also works)
unsequel --version
```

## Task

1. Read [README.md](../README.md) and [docs/grammar.md](grammar.md).
2. Run a real analyst query — no flags needed, the file declares its sources:

   ```bash
   unsequel run examples/real/r01_top_products.unsequel
   ```

3. Watch a pipeline move stage by stage (this fixture has no directives,
   hence the explicit `--data`):

   ```bash
   unsequel preview examples/reference/q20_full_pipeline.unsequel \
     --stage 5 --limit 3 \
     --data orders=examples/spec/orders.csv \
     --data products=examples/spec/products.csv
   ```

4. Write `my_query.unsequel` answering: *which customers spent more than 150 in
   total, highest first?* Use `from`, `group by`, `aggregate`, `having`, and
   `sort`. Start the file with a directive so it runs flagless:

   ```text
   # source orders: examples/spec/orders.csv
   ```

   Sample data: [../../examples/spec/orders.csv](../../examples/spec/orders.csv).
5. Format your file and confirm it is canonical:

   ```bash
   unsequel fmt my_query.unsequel --write
   unsequel fmt my_query.unsequel --check
   ```

6. Validate and run it:

   ```bash
   unsequel check my_query.unsequel
   unsequel run my_query.unsequel --format json
   ```

7. Expected: three customers — Katherine (177), Ada (162), Grace (151.5).

## Optional: editor flow

If you use VS Code, install the extension in
[../editors/vscode/README.md](../editors/vscode/README.md) (F5 is fastest),
open `my_query.unsequel`, and use **UNSeQueL: Run File** — or select the query and
use **UNSeQueL: Run Selection**.

## What to send back

- Did you reach for `filter` or `where` first?
- Was `aggregate` requiring a preceding `group by` obvious from the docs?
- Did the `stage N/total` preview help you understand the pipeline?
- Was the flagless run (the `# source` directive) clear from the README?
- What error message, if any, was unhelpful?
- What did you want to write that the language rejected?
