# First task (external feedback)

A 20-minute exercise that uses only the public documentation. Please do not
read the source code. Record where you get stuck — that is the feedback.

## Setup

Requires Python 3.10 or newer.

```bash
git clone https://github.com/absterjr/UNSeQueL.git
cd UNSeQueL
python -m pip install -e .
```

## Task

1. Read [README.md](../README.md) and [docs/pipeline-spec.md](pipeline-spec.md).
2. Run the reference query:

   ```bash
   python -m unsequel run examples/spec/q09_group_count.pusql --pipeline \
     --data orders=examples/spec/orders.csv
   ```

3. Write `my_query.pusql` answering: *which customers spent more than 100 in
   total, highest first?* Use only `from`, `derive`, `group`, `where`, `sort`.
   Sample data: [examples/spec/orders.csv](../examples/spec/orders.csv).
4. Format your file and confirm it is canonical:

   ```bash
   python -m unsequel fmt --write my_query.pusql
   python -m unsequel fmt --check my_query.pusql
   ```

5. Run it. Expected: three customers (Ada 162, Grace 151.5, Katherine 177 —
   highest first means Katherine, Ada, Grace).
6. Validate it against the schema without running it:

   ```bash
   python -m unsequel check my_query.pusql --pipeline --schema examples/spec/schema.json
   ```

7. Optional: using `sql "..."`, keep only the largest order per customer. The
   previous relation is the table `__input__`. Peek at
   [examples/spec/q21_sql_hatch.pusql](../examples/spec/q21_sql_hatch.pusql)
   only if you are stuck.

## What to send back

- Did the docs tell you to pass `--pipeline`?
- Did `group customer (revenue = SUM(...))` make sense without knowing SQL?
- Was `fmt --check`'s exit code behaviour clear?
- What error message, if any, was unhelpful?
- What did you want to write that the language rejected?
