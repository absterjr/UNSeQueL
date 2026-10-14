# Examples

Sample data and queries for the **Python prototype**.

| Path | Dialect | What it shows |
| --- | --- | --- |
| `orders.csv`, `customers.csv` | data | the tutorial dataset |
| `revenue.usql`, `customer_revenue.usql`, `advanced.usql` | ordered `.usql` | the SQL-like surface, including CTEs and `COUNT(DISTINCT ...)` |
| `pipeline/*.pusql` | pipeline `.pusql` | the prototype's pipeline surface (`where`, combined `group`) |

The production compiler's examples live in [`../compiler/examples/`](../compiler/examples/)
and use the evolved `.unsequel` dialect (`filter`/`having`, split `group by` +
`aggregate`) — see the repository map in the top-level README.
