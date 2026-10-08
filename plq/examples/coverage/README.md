# Coverage corpus

These fixtures exist because the 21 reference queries do not exercise every
grammar feature. They are parser fixtures: no expected rows are pinned here.
Step 4 must parse every file; step 5+ may extend them with schemas.

| File | Grammar features covered |
| --- | --- |
| c01_aliases | `as` aliases on from/join, qualified source name, qualified columns |
| c02_group_expr | named expression group key, `COUNT(DISTINCT)`, `AVG`/`MIN`/`MAX`, bare `COUNT(*)` |
| c03_select_star | `select *` |
| c04_select_exprs | named select expressions, `*` `+` `%` `\|\|` in select |
| c05_derive_multi | multi-item derive, reference to an earlier derived name, `UPPER`/`LOWER`/`ROUND`/`TRIM`/`COALESCE`/`NULLIF`/`LENGTH`/`CONCAT`/`ABS` |
| c06_skip_alone | `skip` without `take` |
| c07_not_variants | `NOT IN`, `NOT BETWEEN`, `NOT LIKE`, `IS NOT NULL`, prefix `NOT` |
| c08_logic_compare | `OR`/`AND` with parentheses, `>`, `<`, `!=`, `<>`, `<=`, `==` |
| c09_literals_cast | `TRUE`/`FALSE`/`NULL`, `CAST`, real number literal |
| c10_sort_markers | trailing `asc`/`desc` sort markers |
| c11_pipes_comments | `\|` separator, `#` and `--` comments, doubled-quote escape |
| c12_window | `window` stage, `partition by`/`order by`, `COUNT(*) OVER`, repeatable |
| c13_unary | unary plus and minus |

Lexer-level validation lives in `plq/tests/coverage.test.ts`; parse validation
is wired when the parser lands (step 4).
