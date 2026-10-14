# Learning claim: validation plan

**Status: not yet tested.** UNSeQueL is designed to make the relation pipeline
visible while a learner reads a query. This is a hypothesis, not evidence that
learners acquire SQL faster or retain it better.

## Questions to test

1. Does writing in execution order improve a learner's ability to predict
   intermediate rows and columns?
2. Does it help learners explain the difference between row filtering,
   grouping, and group filtering?
3. Does practice transfer to ordinary SQL, or does the changed clause order
   add a translation cost?
4. Do learners understand windows, NULLs, joins, and aggregates correctly, or
   does the staged presentation hide important SQL behavior?

## Small, practical study

- Recruit learners who can read basic SELECT/FROM/WHERE but are new to GROUP BY.
- Randomly assign half to an execution-order lesson and half to a conventional
  SQL lesson; swap methods in a second session to reduce prior-knowledge bias.
- Use matched tasks and datasets, with equivalent practice time.
- Measure completion time, correct intermediate-result predictions, query
  correctness, explanation quality, and a short delayed SQL transfer task.
- Record which syntax each participant used first, confusion points, and
  whether they can solve the follow-up task in regular SQL without UNSeQueL.
- Publish anonymized aggregate results and the task materials before making
  comparative learning claims.

The repository's [`plq/docs/first-task.md`](../plq/docs/first-task.md) is a
usability exercise, not this controlled study: its feedback must not be cited
as proof of learning effectiveness.
