# UNSeQueL semantics and conformance

This page records the behavior the engines are meant to share and names the
intentional boundaries. It is a conformance contract, not a claim that every
SQL feature is implemented.

## Which surface should I use?

For a new user, start with **UNSeQueL** (`.unsequel`, TypeScript compiler, DuckDB target,
VS Code extension). The Python `.usql` and `.pusql` surfaces are prototypes
retained for comparison and learning; they have different grammars and are not
three dialects a beginner is expected to choose between. See the
[repository map](../README.md#repository-map) and the
[UNSeQueL grammar](../plq/docs/grammar.md).

UNSeQueL's aim is to make relational transformations readable in the order
they are applied. That does not remove the need to learn SQL: the generated
query is SQL, database-specific behavior still matters, and UNSeQueL does not
currently target general-purpose warehouses. The learning benefit is a design
hypothesis; we have not run a controlled study showing that people learn SQL
faster. See [learning validation](learning-validation.md).

## Ordered prototype (`.usql`)

The Python ordered surface requires both `FROM` and `SELECT`:

```text
FROM orders
WHERE quantity > 0
GROUP BY country
HAVING COUNT(*) > 1
SELECT country, SUM(quantity * unit_price) AS revenue
ORDER BY revenue DESC
LIMIT 10
```

Its documented relation-stage sequence is `FROM`/`JOIN`, `WHERE`, `GROUP BY`,
`HAVING`, `SELECT`, `ORDER BY`, `LIMIT`, `OFFSET`, and set operations. This is
not a complete model of every SQL evaluation phase: in particular the Python
ordered surface has no native window stage. Real SQL windows execute after
aggregation/HAVING and before final projection; UNSeQueL exposes windows as an
explicit stage, while the Python prototype uses its SQL hatch.

`HAVING` runs before `SELECT`; a `SELECT` alias is not available there. The
error now says this directly. A bare column after a join that exists on both
sides is ambiguous: qualify it (for example, `orders.customer`). `SELECT *`
fails rather than silently discarding same-named columns. `ORDER BY 1` is a
one-based output-column ordinal; zero and out-of-range ordinals are errors.

## Values, NULLs, and expressions

- Empty/whitespace-only CSV cells become `NULL` (`None` in Python values).
  The literal strings `null`, `none`, and `nan` remain text; CSV has no
  universal spelling for NULL.
- `WHERE`, `HAVING`, and join predicates keep only `TRUE`; `FALSE` and `NULL`
  do not pass. `AND`/`OR` use three-valued logic. Use `IS NULL` rather than
  `= NULL`.
- `COUNT(*)` counts rows; `COUNT(expr)` counts non-NULL values. `SUM`, `AVG`,
  `MIN`, and `MAX` ignore NULL inputs and return NULL for an empty aggregate
  input; `COUNT` returns zero. A grouped query over empty input has no groups.
- `ORDER BY` puts NULLs last in the memory engine for both ascending and
  descending order. Equal sort keys have no portable guaranteed order; add a
  unique tie-breaker when deterministic output matters.
- `LIKE` is case-sensitive in both engines and `%`/`_` match within the whole
  string. Arithmetic requires numeric values; text repetition such as
  `'abc' * 2` is rejected rather than inheriting Python's string behavior.
  Comparisons of incompatible value types and mixed-type `SUM`/sorts produce a
  user-facing error, not a Python traceback.
- The table renderer prints NULL as `NULL`; JSON renders it as `null`.

## CSV input and inferred types (Python prototype)

CSV inference is column-wide, not per-cell:

- columns whose non-empty values are all numeric infer as integers or numbers;
- a leading-zero integer token such as `02134`, a dirty/mixed value, or other
  non-numeric content makes the entire column text;
- blank cells are NULL; non-empty strings are preserved without trimming;
- duplicate (case-insensitive) headers and rows with the wrong number of fields
  are errors.

Override inference with `--column-type TABLE.COLUMN=TYPE` (repeatable; types:
`text`, `integer`, `number`, `boolean`) and choose a one-character CSV
separator with `--delimiter`:

```bash
python -m unsequel run customer.usql \
  --data customers=customers.csv --delimiter ';' \
  --column-type customers.postal_code=text
```

The same delimiter and type overrides are passed to the optional DuckDB
backend. The production compiler applies equivalent whole-column inference before loading CSVs;
its CLI options are documented in [plq/README.md](../plq/README.md).

## Engine boundaries

The memory engine and DuckDB are intended to agree on the relational meaning
of the shared subset, but are not byte-for-byte identical execution engines.
The known, tested boundaries are:

- memory division by zero returns NULL; DuckDB can return infinity;
- equal `ORDER BY` keys are engine-defined unless an additional tie-breaker is
  supplied;
- floating-point rendering can differ (for example, JSON may render `192.0`
  and `192` differently).

`tests/test_conformance.py` covers NULL predicates and aggregates, case-sensitive
LIKE, null ordering, tied sort keys with a tie-breaker, and empty grouped
results across both Python backends when DuckDB is installed. Additional edge
cases belong here before a semantic change is considered complete.

## Scope of the production compiler

The production compiler is DuckDB-only today. Its stage composition, joins, backtick identifiers,
whole-relation windows, CSV inference and limitations are specified in
[plq/docs/grammar.md](../plq/docs/grammar.md); the prioritized unimplemented
features are in [plq/docs/backlog.md](../plq/docs/backlog.md). It is not a
portable warehouse query language yet.
