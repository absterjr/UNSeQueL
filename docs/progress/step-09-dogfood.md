# Step 9 — Dogfood and outside feedback

**Plan step:** "Use the language on real queries, record gaps as a prioritised
backlog, and have someone external complete a task using only the docs."
**Status:** dogfooding done; external exercise published, awaiting a participant.

## Dogfooding

- All 21 reference queries plus the six older examples were run through the
  new formatter; idempotency is asserted in the test suite, so every example
  file is now canonical and `fmt --check` passes across the repo.
- `q21_sql_hatch` was written *as a user*: the first draft needed a window
  function, which the core stages do not have. The hatch covered it end to
  end — same result through the memory engine (SQLite) and the DuckDB CTE
  chain (execution-parity test compares against a hand-written SQL
  equivalent).
- Gaps found while writing it became the prioritised backlog in
  [docs/backlog.md](../backlog.md). Top three: native window syntax,
  `--pipeline` inference for `.pusql` files, stage-located runtime errors.

## External feedback exercise

[docs/first-task.md](../first-task.md) is a self-contained 20-minute task
(setup → run a query → write one → format → schema-check → optional hatch).
It deliberately withholds the `--pipeline` flag in step 2's command to see
whether the docs communicate it. The questionnaire at the bottom defines what
feedback we collect.

## What changed

| File | What |
| --- | --- |
| `docs/backlog.md` | Prioritised gap list from dogfooding (P0/P1/P2 + out-of-scope) |
| `docs/first-task.md` | The external-feedback exercise and questionnaire |
| `examples/spec/` | All examples canonical; q21 added (Step 8) |

## What's next

Work the P0 backlog items in order; re-run the external exercise after each to
confirm the friction actually disappeared.
